from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from state_aware_rag.embedding import HashedEmbedder
from state_aware_rag.extraction import (
    EntityResolver,
    LlmEntityExtractor,
    RuleEntityExtractor,
    normalize_entity_name,
    strip_corporate_suffix,
)
from state_aware_rag.models import Chunk, Entity, EntityType, Relation, new_id, now_iso
from state_aware_rag.store import SQLiteRagStore
from state_aware_rag.text import extract_entities


def _chunk(body: str) -> Chunk:
    return Chunk(
        id="chunk_test",
        document_id="doc_test",
        body=body,
        embedding=[0.1, 0.2],
        token_count=10,
        position=0,
        section_title=None,
        source_uri="memory://test",
        metadata={},
    )


def test_extract_entities_regex_is_unchanged() -> None:
    text = "State-Aware RAG keeps 作業用メモ for HelixDB."
    assert extract_entities(text) == ["State-Aware", "RAG", "作業用", "HelixDB"]


def test_rule_entity_extractor_matches_regex_entities() -> None:
    embedder = HashedEmbedder()
    extractor = RuleEntityExtractor(embedder)
    result = extractor.extract(_chunk("HelixDB stores documents and chunks."))

    assert result.chunk_id == "chunk_test"
    assert {entity.canonical_name for entity in result.entities} == {"HelixDB"}
    assert all(entity.entity_type == EntityType.OTHER for entity in result.entities)
    assert result.relations == ()


def test_normalize_entity_name_handles_nfkc_and_ascii_case() -> None:
    assert normalize_entity_name("  ＡＢＣ Corp.  ") == "abc corp."
    assert strip_corporate_suffix("ABC株式会社") == normalize_entity_name("ABC")


def test_entity_resolver_merges_alias_and_company_suffix(tmp_path: Path) -> None:
    store = SQLiteRagStore(tmp_path / "rag.sqlite3", embedder=HashedEmbedder())
    resolver = EntityResolver(store, store.embedder)
    timestamp = now_iso()
    first = Entity(
        id=new_id("ent"),
        entity_type=EntityType.COMPANY,
        canonical_name="ABC株式会社",
        normalized_name=normalize_entity_name("ABC株式会社"),
        aliases=(),
        embedding=store.embedder.embed_claim("ABC株式会社"),
        attributes={},
        created_at=timestamp,
        updated_at=timestamp,
    )
    resolved_first = resolver.resolve(first, surface="ABC株式会社", source_chunk_id="chunk_a")
    second = Entity(
        id=new_id("ent"),
        entity_type=EntityType.COMPANY,
        canonical_name="ABC Corp.",
        normalized_name=normalize_entity_name("ABC Corp."),
        aliases=("ABC",),
        embedding=store.embedder.embed_claim("ABC Corp."),
        attributes={},
        created_at=timestamp,
        updated_at=timestamp,
    )
    resolved_second = resolver.resolve(second, surface="ABC Corp.", source_chunk_id="chunk_b")

    assert resolved_first.id == resolved_second.id
    aliases = set(store.get_entity(resolved_first.id).aliases)
    assert "ABC Corp." in aliases or "ABC" in aliases
    assert len(store.list_entities()) == 1


def test_llm_entity_extractor_validates_json_payload() -> None:
    embedder = HashedEmbedder()
    extractor = LlmEntityExtractor(embedder)

    async def fake_complete(prompt: str) -> str:
        return json.dumps(
            {
                "entities": [
                    {
                        "type": "Person",
                        "canonical_name": "山田太郎",
                        "aliases": [],
                        "attributes": {},
                    },
                    {
                        "type": "Company",
                        "canonical_name": "ABC株式会社",
                        "aliases": ["ABC"],
                        "attributes": {},
                    },
                    {
                        "type": "Date",
                        "canonical_name": "2020年",
                        "aliases": [],
                        "attributes": {},
                    },
                ],
                "relations": [
                    {
                        "from": "山田太郎",
                        "to": "ABC株式会社",
                        "relation_type": "WORKS_AT",
                        "evidence_text": "山田太郎は2020年からABC株式会社で働いている。",
                        "attributes": {"since": "2020"},
                        "confidence": 1.2,
                    },
                    {
                        "from": "存在しない",
                        "to": "ABC株式会社",
                        "relation_type": "WORKS_AT",
                        "evidence_text": "hallucination",
                        "attributes": {},
                        "confidence": 0.5,
                    },
                ],
            },
            ensure_ascii=False,
        )

    extractor.config.complete = fake_complete  # type: ignore[method-assign]
    result = extractor.extract(_chunk("山田太郎は2020年からABC株式会社で働いている。"))

    assert len(result.entities) == 3
    assert {entity.entity_type for entity in result.entities} == {
        EntityType.PERSON,
        EntityType.COMPANY,
        EntityType.DATE,
    }
    assert len(result.relations) == 1
    assert result.relations[0].relation_type == "WORKS_AT"
    assert result.relations[0].confidence == 1.0


def test_relation_round_trip(tmp_path: Path) -> None:
    store = SQLiteRagStore(tmp_path / "rag.sqlite3", embedder=HashedEmbedder())
    embedder = store.embedder
    timestamp = now_iso()
    person = Entity(
        id=new_id("ent"),
        entity_type=EntityType.PERSON,
        canonical_name="山田太郎",
        normalized_name=normalize_entity_name("山田太郎"),
        aliases=(),
        embedding=embedder.embed_claim("山田太郎"),
        attributes={},
        created_at=timestamp,
        updated_at=timestamp,
    )
    company = Entity(
        id=new_id("ent"),
        entity_type=EntityType.COMPANY,
        canonical_name="ABC株式会社",
        normalized_name=normalize_entity_name("ABC株式会社"),
        aliases=(),
        embedding=embedder.embed_claim("ABC株式会社"),
        attributes={},
        created_at=timestamp,
        updated_at=timestamp,
    )
    resolver = EntityResolver(store, embedder)
    doc = store.ingest_document(
        title="雇用",
        body="山田太郎は2020年からABC株式会社で働いている。",
        source_uri="memory://employment",
        chunk_size=200,
        overlap=0,
        extract_entities=False,
    )
    chunk = doc.chunks[0]
    resolved_person = resolver.resolve(person, surface="山田太郎", source_chunk_id=chunk.id)
    resolved_company = resolver.resolve(company, surface="ABC株式会社", source_chunk_id=chunk.id)
    store.link_chunk_entity(chunk.id, resolved_person.id, surface="山田太郎")
    store.link_chunk_entity(chunk.id, resolved_company.id, surface="ABC株式会社")
    relation = store.save_relation(
        Relation(
            id=new_id("rel"),
            from_entity_id=resolved_person.id,
            to_entity_id=resolved_company.id,
            relation_type="WORKS_AT",
            source_chunk_id=chunk.id,
            confidence=0.92,
            evidence_text="山田太郎は2020年からABC株式会社で働いている。",
            attributes={"since": "2020"},
            created_at=timestamp,
        )
    )
    loaded = store.get_relation(relation.id)

    assert loaded.relation_type == "WORKS_AT"
    assert store.get_entity(resolved_person.id).entity_type == EntityType.PERSON
    assert store.get_entity(resolved_company.id).entity_type == EntityType.COMPANY
    assert store.list_relations_for_chunk(chunk.id)[0].from_entity_id == resolved_person.id


class ScriptedEntityExtractor:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.embedder = HashedEmbedder()

    def extract(self, chunk: Chunk):
        from state_aware_rag.extraction import _parse_llm_payload

        return _parse_llm_payload(chunk, self.payload, self.embedder)


def test_ingest_with_scripted_extractor_persists_entities_and_relations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SQLiteRagStore(tmp_path / "rag.sqlite3", embedder=HashedEmbedder())
    payload = {
        "entities": [
            {"type": "Person", "canonical_name": "山田太郎", "aliases": [], "attributes": {}},
            {"type": "Company", "canonical_name": "ABC株式会社", "aliases": ["ABC"], "attributes": {}},
            {"type": "Date", "canonical_name": "2020年", "aliases": [], "attributes": {}},
        ],
        "relations": [
            {
                "from": "山田太郎",
                "to": "ABC株式会社",
                "relation_type": "WORKS_AT",
                "evidence_text": "山田太郎は2020年からABC株式会社で働いている。",
                "attributes": {"since": "2020"},
                "confidence": 0.92,
            }
        ],
    }
    monkeypatch.setattr(
        "state_aware_rag.store.build_entity_extractor",
        lambda backend, embedder: ScriptedEntityExtractor(payload),
    )
    doc = store.ingest_document(
        title="雇用",
        body="山田太郎は2020年からABC株式会社で働いている。",
        source_uri="memory://employment",
        chunk_size=200,
        overlap=0,
        extractor_backend="llm",
    )
    chunk = doc.chunks[0]
    entities = store.list_entities()
    relations = store.list_relations_for_chunk(chunk.id)

    assert len(entities) == 3
    assert {entity.entity_type for entity in entities} == {
        EntityType.PERSON,
        EntityType.COMPANY,
        EntityType.DATE,
    }
    assert len(relations) == 1
    assert relations[0].relation_type == "WORKS_AT"


def test_find_entity_seed_match_partial_name(tmp_path: Path) -> None:
    store = SQLiteRagStore(tmp_path / "rag.sqlite3", embedder=HashedEmbedder())
    store.add_entity("1000 Genomes Project")

    matched = store.find_entity_seed("1000 Genomes")

    assert matched is not None
    assert matched.canonical_name == "1000 Genomes Project"


def test_find_entity_seed_match_alias_similarity(tmp_path: Path) -> None:
    store = SQLiteRagStore(tmp_path / "rag.sqlite3", embedder=HashedEmbedder())
    entity_id = store.add_entity("Rare variant")
    store.add_entity_alias(entity_id, "rare variants")

    matched = store.find_entity_seed("Rare variants")

    assert matched is not None
    assert matched.id == entity_id
