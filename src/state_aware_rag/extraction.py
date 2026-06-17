from __future__ import annotations

import json
import logging
import re
import unicodedata
from typing import Any, Protocol

from state_aware_rag.embedding import Embedder
from state_aware_rag.llm import LlamaServerEnvConfig, _extract_json_object
from state_aware_rag.models import Chunk, Entity, EntityType, ExtractionResult, Relation, new_id, now_iso
from state_aware_rag.text import cosine_similarity, extract_entities, overlap_score

logger = logging.getLogger(__name__)

RELATION_TYPE_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
CORP_SUFFIX_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"(?:株式会社|有限会社|合同会社|合資会社|合名会社)$",
        r"\s*(?:co\.?,?\s*ltd\.?|inc\.?|corp\.?|corporation|limited|llc)\.?$",
    )
)

JACCARD_MATCH_THRESHOLD = 0.85
EDIT_DISTANCE_MATCH_THRESHOLD = 0.90
EMBEDDING_MATCH_THRESHOLD = 0.92


class EntityExtractor(Protocol):
    def extract(self, chunk: Chunk) -> ExtractionResult: ...


def normalize_entity_name(name: str) -> str:
    text = unicodedata.normalize("NFKC", name).strip()
    text = re.sub(r"\s+", " ", text)
    return "".join(char.lower() if char.isascii() and char.isalpha() else char for char in text)


def strip_corporate_suffix(name: str) -> str:
    text = unicodedata.normalize("NFKC", name).strip()
    for pattern in CORP_SUFFIX_PATTERNS:
        text = pattern.sub("", text).strip()
    return normalize_entity_name(text)


def parse_entity_type(raw: str | None) -> EntityType:
    if not raw:
        return EntityType.OTHER
    try:
        return EntityType(raw)
    except ValueError:
        return EntityType.OTHER


def _levenshtein_ratio(left: str, right: str) -> float:
    if left == right:
        return 1.0
    if not left or not right:
        return 0.0
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for index, left_char in enumerate(left, start=1):
        current = [index]
        for col, right_char in enumerate(right, start=1):
            insert_cost = current[col - 1] + 1
            delete_cost = previous[col] + 1
            replace_cost = previous[col - 1] + (left_char != right_char)
            current.append(min(insert_cost, delete_cost, replace_cost))
        previous = current
    distance = previous[-1]
    return 1.0 - distance / max(len(left), len(right))


def _name_similarity(left: str, right: str) -> float:
    left_norm = normalize_entity_name(left)
    right_norm = normalize_entity_name(right)
    if left_norm == right_norm:
        return 1.0
    left_stripped = strip_corporate_suffix(left)
    right_stripped = strip_corporate_suffix(right)
    if left_stripped and left_stripped == right_stripped:
        return 1.0
    return max(overlap_score(left_norm, right_norm), _levenshtein_ratio(left_norm, right_norm))


def _clip_confidence(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, score))


def _build_entity_lookup(entities: tuple[Entity, ...]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for entity in entities:
        lookup[entity.canonical_name] = entity.id
        lookup[normalize_entity_name(entity.canonical_name)] = entity.id
        for alias in entity.aliases:
            lookup[alias] = entity.id
            lookup[normalize_entity_name(alias)] = entity.id
    return lookup


def _parse_llm_payload(chunk: Chunk, payload: dict[str, Any], embedder: Embedder) -> ExtractionResult:
    raw_entities = payload.get("entities")
    raw_relations = payload.get("relations")
    if not isinstance(raw_entities, list) or not isinstance(raw_relations, list):
        return ExtractionResult(chunk_id=chunk.id, entities=(), relations=())

    timestamp = now_iso()
    entities: list[Entity] = []
    for item in raw_entities:
        if not isinstance(item, dict):
            continue
        canonical_name = str(item.get("canonical_name", "")).strip()
        if not canonical_name:
            continue
        aliases = tuple(
            dict.fromkeys(
                alias.strip()
                for alias in item.get("aliases", [])
                if isinstance(alias, str) and alias.strip() and alias.strip() != canonical_name
            )
        )
        attributes = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        entities.append(
            Entity(
                id=new_id("ent"),
                entity_type=parse_entity_type(str(item.get("type", ""))),
                canonical_name=canonical_name,
                normalized_name=normalize_entity_name(canonical_name),
                aliases=aliases,
                embedding=embedder.embed_claim(canonical_name),
                attributes=dict(attributes),
                created_at=timestamp,
                updated_at=timestamp,
            )
        )

    lookup = _build_entity_lookup(tuple(entities))
    relations: list[Relation] = []
    for item in raw_relations:
        if not isinstance(item, dict):
            continue
        relation_type = str(item.get("relation_type", "")).strip()
        if not RELATION_TYPE_RE.fullmatch(relation_type):
            continue
        from_name = str(item.get("from", "")).strip()
        to_name = str(item.get("to", "")).strip()
        from_id = lookup.get(from_name) or lookup.get(normalize_entity_name(from_name))
        to_id = lookup.get(to_name) or lookup.get(normalize_entity_name(to_name))
        if not from_id or not to_id:
            continue
        attributes = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        relations.append(
            Relation(
                id=new_id("rel"),
                from_entity_id=from_id,
                to_entity_id=to_id,
                relation_type=relation_type,
                source_chunk_id=chunk.id,
                confidence=_clip_confidence(item.get("confidence", 0.0)),
                evidence_text=str(item.get("evidence_text", "")).strip(),
                attributes=dict(attributes),
                created_at=timestamp,
            )
        )

    return ExtractionResult(chunk_id=chunk.id, entities=tuple(entities), relations=tuple(relations))


class RuleEntityExtractor:
    """正規表現ベースのフォールバック抽出器。"""

    def __init__(self, embedder: Embedder) -> None:
        self.embedder = embedder

    def extract(self, chunk: Chunk) -> ExtractionResult:
        timestamp = now_iso()
        entities: list[Entity] = []
        for name in extract_entities(chunk.body):
            entities.append(
                Entity(
                    id=new_id("ent"),
                    entity_type=EntityType.OTHER,
                    canonical_name=name,
                    normalized_name=normalize_entity_name(name),
                    aliases=(),
                    embedding=self.embedder.embed_claim(name),
                    attributes={},
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
        return ExtractionResult(chunk_id=chunk.id, entities=tuple(entities), relations=())


class LlmEntityExtractor:
    """llama-server 経由でエンティティと関係を抽出する。"""

    def __init__(self, embedder: Embedder, config: LlamaServerEnvConfig | None = None) -> None:
        self.embedder = embedder
        self.config = config or LlamaServerEnvConfig.from_env()

    def extract(self, chunk: Chunk) -> ExtractionResult:
        prompt = f"""
あなたは文書からエンティティと関係を抽出する抽出器です。

入力チャンク:
{chunk.body}

抽出方針:
- 型は Person / Company / Product / Contract / Date / Concept / Other のいずれか。
- 一般名詞や指示語（彼/同社/それ など）は抽出しない。
- 同じ実体への異表記は同じ canonical_name にまとめてよい。
- 関係は二項関係のみ。三項以上は分解する。
- relation_type は大文字スネーク (例: WORKS_AT, BELONGS_TO, SIGNED_ON)。
- 根拠が原文に書かれていないものは出力しない。
- 出力は JSON のみ。

出力形式:
{{
  "entities": [
    {{
      "type": "Company",
      "canonical_name": "ABC株式会社",
      "aliases": ["ABC", "ABC Corp."],
      "attributes": {{}}
    }}
  ],
  "relations": [
    {{
      "from": "山田太郎",
      "to": "ABC株式会社",
      "relation_type": "WORKS_AT",
      "evidence_text": "山田太郎は2020年からABC株式会社に勤務している。",
      "attributes": {{"since": "2020"}},
      "confidence": 0.92
    }}
  ]
}}
"""
        try:
            import asyncio

            text = asyncio.run(self.config.complete(prompt))
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                extracted = _extract_json_object(text)
                if extracted is None:
                    raise RuntimeError("invalid JSON from llama-server")
                payload = extracted
            if not isinstance(payload, dict):
                return ExtractionResult(chunk_id=chunk.id, entities=(), relations=())
            return _parse_llm_payload(chunk, payload, self.embedder)
        except Exception as exc:
            logger.warning("Entity extraction failed for chunk %s: %s", chunk.id, exc)
            return ExtractionResult(chunk_id=chunk.id, entities=(), relations=())


class EntityResolver:
    """抽出候補を既存エンティティへ名寄せする。"""

    def __init__(self, store: Any, embedder: Embedder) -> None:
        self.store = store
        self.embedder = embedder

    def resolve(self, candidate: Entity, *, surface: str, source_chunk_id: str | None = None) -> Entity:
        existing = self.store.find_entity_by_type_and_normalized(candidate.entity_type, candidate.normalized_name)
        if existing is not None:
            return self._merge_alias(existing, surface, source_chunk_id, candidate)

        for alias in (surface, candidate.canonical_name, *candidate.aliases):
            normalized_alias = normalize_entity_name(alias)
            if not normalized_alias:
                continue
            by_alias = self.store.find_entity_by_alias(normalized_alias)
            if by_alias is not None and self._types_compatible(by_alias.entity_type, candidate.entity_type):
                return self._merge_alias(by_alias, surface, source_chunk_id, candidate)

        similar = self._find_similar_entity(candidate)
        if similar is not None:
            return self._merge_alias(similar, surface, source_chunk_id, candidate)

        return self.store.insert_entity(candidate, source_chunk_id=source_chunk_id)

    def _types_compatible(self, existing_type: EntityType, candidate_type: EntityType) -> bool:
        if existing_type == candidate_type:
            return True
        if existing_type == EntityType.OTHER and candidate_type != EntityType.OTHER:
            return True
        if candidate_type == EntityType.OTHER and existing_type != EntityType.OTHER:
            return True
        return False

    def _merge_alias(
        self,
        existing: Entity,
        surface: str,
        source_chunk_id: str | None,
        candidate: Entity,
    ) -> Entity:
        merged_type = candidate.entity_type if existing.entity_type == EntityType.OTHER else existing.entity_type
        updated = existing
        if merged_type != existing.entity_type:
            updated = self.store.update_entity_type(existing.id, merged_type)
        for alias in (surface, candidate.canonical_name, *candidate.aliases):
            alias = alias.strip()
            if not alias or alias == updated.canonical_name:
                continue
            updated = self.store.add_entity_alias(updated.id, alias, source_chunk_id)
        return updated

    def _find_similar_entity(self, candidate: Entity) -> Entity | None:
        candidates = self.store.list_entities_by_type(candidate.entity_type)
        if candidate.entity_type == EntityType.OTHER:
            candidates = self.store.list_entities()

        best: Entity | None = None
        best_score = 0.0
        for existing in candidates:
            if not self._types_compatible(existing.entity_type, candidate.entity_type):
                continue
            score = _name_similarity(existing.canonical_name, candidate.canonical_name)
            if score >= JACCARD_MATCH_THRESHOLD and score > best_score:
                best = existing
                best_score = score

        if best is not None:
            return best

        for existing in candidates:
            if not self._types_compatible(existing.entity_type, candidate.entity_type):
                continue
            similarity = cosine_similarity(existing.embedding, candidate.embedding)
            if similarity >= EMBEDDING_MATCH_THRESHOLD:
                return existing
        return None


def build_entity_extractor(backend: str, embedder: Embedder) -> EntityExtractor:
    name = (backend or "rule").strip().lower()
    if name in {"rule", "local", "regex"}:
        return RuleEntityExtractor(embedder)
    if name in {"llm", "server"}:
        return LlmEntityExtractor(embedder)
    raise ValueError(f"Unknown entity extractor backend: {backend}")
