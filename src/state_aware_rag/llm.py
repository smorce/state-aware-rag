from __future__ import annotations

import asyncio
import json
import os
import random
from dataclasses import dataclass
from typing import Any, Protocol

from state_aware_rag.models import Evidence, MemoryNote, OpenQuestion
from state_aware_rag.text import MSG_NO_EVIDENCE, compact_fact, extract_entities, normalize_claim, split_sentences, tokenize


class PlannerAndWriter(Protocol):
    def plan(self, question: str, working_memory: list[MemoryNote], open_questions: list[OpenQuestion], round_number: int, max_actions: int) -> list[dict[str, Any]]:
        ...

    def create_atomic_notes(self, question: str, working_memory: list[MemoryNote], evidence: list[Evidence]) -> dict[str, Any]:
        ...

    def generate_final_answer(
        self,
        question: str,
        memory_notes: list[MemoryNote],
        evidence_by_note: dict[str, list[Evidence]],
        conflicts: list[tuple[MemoryNote, MemoryNote, float]],
        open_questions: list[OpenQuestion],
    ) -> str:
        ...


class LocalHeuristicLLM:
    def plan(self, question: str, working_memory: list[MemoryNote], open_questions: list[OpenQuestion], round_number: int, max_actions: int) -> list[dict[str, Any]]:
        seeds = [item.question for item in open_questions] or [question]
        known = " ".join(note.claim for note in working_memory)
        actions: list[dict[str, Any]] = []
        for seed in seeds:
            if normalize_claim(seed) and normalize_claim(seed) in normalize_claim(known):
                continue
            entities = extract_entities(seed) or extract_entities(question)
            actions.append(
                {
                    "sub_question": seed,
                    "why_needed": "元の質問に答えるため",
                    "vector_query": seed,
                    "text_query": " ".join(tokenize(seed)[:8]) or seed,
                    "graph_seed_entities": entities,
                    "priority": 1,
                }
            )
            if len(actions) >= max_actions:
                break
        return actions

    def create_atomic_notes(self, question: str, working_memory: list[MemoryNote], evidence: list[Evidence]) -> dict[str, Any]:
        existing = {note.normalized_claim for note in working_memory}
        question_terms = set(tokenize(question))
        notes: list[dict[str, Any]] = []
        for ev in evidence:
            sentence = self._best_sentence(ev.body_excerpt, question_terms)
            claim = compact_fact(sentence)
            normalized = normalize_claim(claim)
            if not normalized or normalized in existing:
                continue
            existing.add(normalized)
            notes.append(
                {
                    "claim": claim,
                    "note_type": "fact",
                    "supported_by_evidence_ids": [ev.id],
                    "confidence": round(min(0.95, (ev.relevance_score + ev.memory_value_score) / 2), 3),
                }
            )
        return {"notes": notes, "open_questions": []}

    def generate_final_answer(
        self,
        question: str,
        memory_notes: list[MemoryNote],
        evidence_by_note: dict[str, list[Evidence]],
        conflicts: list[tuple[MemoryNote, MemoryNote, float]],
        open_questions: list[OpenQuestion],
    ) -> str:
        if not memory_notes:
            return MSG_NO_EVIDENCE
        lines = ["作業用メモから確認できた範囲では、次の通りです。"]
        if conflicts:
            lines.append("作業用メモ内に矛盾する情報があります。")
        for index, note in enumerate(memory_notes, start=1):
            sources = sorted({ev.source_uri for ev in evidence_by_note.get(note.id, [])})
            source_text = f" 出典: {', '.join(sources)}" if sources else ""
            lines.append(f"{index}. {note.claim}（信頼度 {note.confidence:.2f}）。{source_text}".rstrip())
        if open_questions:
            lines.append("一部の情報は確認できていません。")
            for item in open_questions:
                lines.append(f"- 未確認: {item.question}")
        return "\n".join(lines)

    def _best_sentence(self, body: str, question_terms: set[str]) -> str:
        sentences = split_sentences(body) or [body]
        best = max(sentences, key=lambda s: len(question_terms & set(tokenize(s))))
        return best


def _ensure_openai_v1_base_url(raw: str) -> str:
    value = (raw or "").strip() or "http://127.0.0.1:1067"
    if "://" not in value:
        value = f"http://{value}"
    value = value.rstrip("/")
    if value.endswith("/v1"):
        return value
    return f"{value}/v1"


def _without_v1_suffix(base_url: str) -> str:
    value = _ensure_openai_v1_base_url(base_url)
    return value[:-3] if value.endswith("/v1") else value


def _get_optional_int(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _get_optional_float(value: str | None) -> float | None:
    if value is None or not value.strip():
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _should_retry(exc: Exception) -> bool:
    try:
        from openai import APIConnectionError, APIStatusError, RateLimitError
    except Exception:
        APIConnectionError = APIStatusError = RateLimitError = None  # type: ignore[assignment]

    if APIConnectionError and isinstance(exc, APIConnectionError):
        return True
    if RateLimitError and isinstance(exc, RateLimitError):
        return True
    if APIStatusError and isinstance(exc, APIStatusError):
        return bool(getattr(exc, "status_code", 0) >= 500)
    if isinstance(exc, ConnectionError):
        return True
    return False


async def call_llama_server(
    client: Any,
    model: str,
    prompt: str,
    *,
    enable_thinking: bool,
    temperature: float | None,
    top_p: float | None,
    top_k: int | None,
    min_p: float | None,
    max_tokens: int,
    timeout_seconds: float,
    max_retries: int,
    retry_base_delay_seconds: float,
    retry_max_delay_seconds: float,
) -> str:
    """OpenAI 互換 llama-server の Responses API を呼び出す。"""

    def _run() -> str:
        payload = {
            "temperature": 1.0 if enable_thinking else 0.7,
            "top_p": 0.95 if enable_thinking else 0.8,
            "top_k": 20,
            "min_p": 0.0,
            "chat_template_kwargs": {"enable_thinking": enable_thinking},
        }
        response = client.responses.create(
            model=model,
            input=prompt,
            temperature=temperature if temperature is not None else payload["temperature"],
            top_p=top_p if top_p is not None else payload["top_p"],
            max_output_tokens=max_tokens,
            extra_body={
                "top_k": top_k if top_k is not None else payload["top_k"],
                "min_p": min_p if min_p is not None else payload["min_p"],
                "chat_template_kwargs": payload["chat_template_kwargs"],
            },
        )
        return response.output_text or ""

    attempts = max(1, max_retries)
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await asyncio.wait_for(asyncio.to_thread(_run), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            last_exc = RuntimeError("llama-server request timeout")
            retryable = True
        except Exception as exc:
            last_exc = exc
            retryable = _should_retry(exc)

        if (not retryable) or attempt >= attempts:
            break
        exp_backoff = retry_base_delay_seconds * (2 ** (attempt - 1))
        await asyncio.sleep(min(exp_backoff, retry_max_delay_seconds) + random.uniform(0, 0.2))

    if last_exc is None:
        raise RuntimeError("llama-server request failed")
    raise RuntimeError(f"llama-server request failed after retries: {last_exc}") from last_exc


@dataclass
class LlamaServerEnvConfig:
    base_url_no_v1: str
    api_key: str
    model: str
    enable_thinking: bool
    temperature: float
    top_p: float | None
    top_k: int | None
    min_p: float | None
    max_tokens: int
    timeout_seconds: float
    max_retries: int
    retry_base_delay_seconds: float
    retry_max_delay_seconds: float

    @classmethod
    def from_env(cls) -> "LlamaServerEnvConfig":
        resolved = os.getenv("LLAMA_SERVER_BASE_URL", "http://127.0.0.1:1067")
        return cls(
            base_url_no_v1=_without_v1_suffix(resolved),
            api_key=os.getenv("LLAMA_SERVER_API_KEY", "sk-local-no-key-required"),
            model=os.getenv("LLM_MODEL", "unsloth/Qwen3.6-27B-MTP-GGUF-UD-Q4_K_XL"),
            enable_thinking=os.getenv("LLAMA_SERVER_ENABLE_THINKING", "false").lower() in ("true", "1", "yes"),
            temperature=float(os.getenv("LLAMA_SERVER_TEMPERATURE", "0.3")),
            top_p=_get_optional_float(os.getenv("LLAMA_SERVER_TOP_P")),
            top_k=_get_optional_int(os.getenv("LLAMA_SERVER_TOP_K")),
            min_p=_get_optional_float(os.getenv("LLAMA_SERVER_MIN_P")),
            max_tokens=int(os.getenv("LLAMA_SERVER_MAX_TOKENS", "130000")),
            timeout_seconds=float(os.getenv("LLAMA_SERVER_TIMEOUT_SECONDS", "180")),
            max_retries=int(os.getenv("LLAMA_SERVER_MAX_RETRIES", "5")),
            retry_base_delay_seconds=float(os.getenv("LLAMA_SERVER_RETRY_BASE_SECONDS", "0.8")),
            retry_max_delay_seconds=float(os.getenv("LLAMA_SERVER_RETRY_MAX_SECONDS", "8.0")),
        )

    def build_openai_client(self) -> Any:
        from openai import OpenAI

        return OpenAI(
            base_url=_ensure_openai_v1_base_url(self.base_url_no_v1),
            api_key=self.api_key,
            timeout=self.timeout_seconds,
            max_retries=0,
        )

    async def complete(self, prompt: str) -> str:
        return await call_llama_server(
            self.build_openai_client(),
            self.model,
            prompt,
            enable_thinking=self.enable_thinking,
            temperature=self.temperature,
            top_p=self.top_p,
            top_k=self.top_k,
            min_p=self.min_p,
            max_tokens=self.max_tokens,
            timeout_seconds=self.timeout_seconds,
            max_retries=self.max_retries,
            retry_base_delay_seconds=self.retry_base_delay_seconds,
            retry_max_delay_seconds=self.retry_max_delay_seconds,
        )


class JsonLlamaPlannerAndWriter(LocalHeuristicLLM):
    def __init__(self, config: LlamaServerEnvConfig | None = None) -> None:
        self.config = config or LlamaServerEnvConfig.from_env()

    def _complete_json(self, prompt: str) -> dict[str, Any]:
        text = asyncio.run(self.config.complete(prompt))
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            extracted = _extract_json_object(text)
            if extracted is None:
                raise RuntimeError(f"llama-server returned invalid JSON: {text}")
            return extracted

    def plan(self, question: str, working_memory: list[MemoryNote], open_questions: list[OpenQuestion], round_number: int, max_actions: int) -> list[dict[str, Any]]:
        prompt = f"""
元の質問:
{question}

現在の作業用メモ:
{json.dumps([note.claim for note in working_memory], ensure_ascii=False)}

まだ足りない情報:
{json.dumps([item.question for item in open_questions], ensure_ascii=False)}

作業:
まだ足りない情報を1〜{max_actions}個の小質問に分けてください。
各小質問について、ベクトル検索向け、全文検索向け、グラフ探索向けの検索クエリを作ってください。
すでに作業用メモにある内容は再検索しないでください。

出力は JSON のみ:
{{
  "sub_questions": [
    {{
      "sub_question": "小質問",
      "why_needed": "この情報が必要な理由",
      "vector_query": "意味検索向けクエリ",
      "text_query": "全文検索向けクエリ",
      "graph_seed_entities": ["Entity名"],
      "priority": 1
    }}
  ]
}}
"""
        data = self._complete_json(prompt)
        return list(data.get("sub_questions", []))[:max_actions]

    def create_atomic_notes(self, question: str, working_memory: list[MemoryNote], evidence: list[Evidence]) -> dict[str, Any]:
        prompt = f"""
あなたは検索拡張生成システムの作業用メモ更新器です。

ユーザーの質問:
{question}

現在の作業用メモ:
{json.dumps([note.claim for note in working_memory], ensure_ascii=False)}

採用済み根拠:
{json.dumps([{"evidence_id": ev.id, "body_excerpt": ev.body_excerpt, "source_uri": ev.source_uri} for ev in evidence], ensure_ascii=False)}

条件:
- 1つの note には1つの事実だけを書く。
- 根拠に書かれていないことを推測しない。
- 一般論ではなく、質問に役立つ具体的な事実を書く。
- 既存メモにある情報は繰り返さない。
- 不確かな内容は assumption として分ける。
- まだ足りない情報は open_question として分ける。
- 出力は JSON のみ。

出力形式:
{{
  "notes": [
    {{
      "claim": "短い事実",
      "note_type": "fact | definition | constraint | intermediate_answer | assumption",
      "supported_by_evidence_ids": ["ev_001"],
      "confidence": 0.0
    }}
  ],
  "open_questions": [
    {{
      "question": "まだ足りない小質問",
      "reason": "なぜ必要か"
    }}
  ]
}}
"""
        data = self._complete_json(prompt)
        data.setdefault("notes", [])
        data.setdefault("open_questions", [])
        return data

    def generate_final_answer(
        self,
        question: str,
        memory_notes: list[MemoryNote],
        evidence_by_note: dict[str, list[Evidence]],
        conflicts: list[tuple[MemoryNote, MemoryNote, float]],
        open_questions: list[OpenQuestion],
    ) -> str:
        payload = {
            "question": question,
            "memory_notes": [
                {
                    "claim": note.claim,
                    "confidence": note.confidence,
                    "evidence_ids": [ev.id for ev in evidence_by_note.get(note.id, [])],
                    "source_uri": sorted({ev.source_uri for ev in evidence_by_note.get(note.id, [])}),
                }
                for note in memory_notes
            ],
            "conflicts": [
                {"note_a": left.claim, "note_b": right.claim, "conflict_score": score}
                for left, right, score in conflicts
            ],
            "open_questions": [item.question for item in open_questions],
        }
        prompt = f"""
あなたは、作業用メモだけを使って回答するアシスタントです。

入力:
{json.dumps(payload, ensure_ascii=False)}

条件:
- 検索結果の原文ではなく、MemoryNote だけを根拠にする。
- MemoryNote にない内容を推測しない。
- 矛盾がある場合は、矛盾があると明記する。
- open_questions が残っている場合は、未確認の点として明記する。
- 根拠が足りない場合は、足りないと答える。
- 出典がある場合は、対応する出典を示す。
"""
        return asyncio.run(self.config.complete(prompt)).strip()


def _extract_json_object(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : index + 1])
    return None
