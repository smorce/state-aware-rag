from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

Outcome = Literal["success", "failure", "skipped"]

ROUTE_ANSWER_SESSION_START = "answer.session_start"
ROUTE_ANSWER_UNHANDLED_EXCEPTION = "answer.unhandled_exception"
ROUTE_ROUND_NO_ACTIONS = "round.no_actions_selected"
ROUTE_ROUND_RETRIEVAL_EMPTY = "round.retrieval_empty"
ROUTE_SCORE_CANDIDATE_REJECTED_RELEVANCE = "score.candidate_rejected.relevance_below_threshold"
ROUTE_SCORE_CANDIDATE_REJECTED_MEMORY_VALUE = "score.candidate_rejected.memory_value_below_threshold"
ROUTE_SCORE_CANDIDATE_ACCEPTED = "score.candidate_accepted"
ROUTE_ROUND_NO_ACCEPTED_EVIDENCE = "round.no_accepted_evidence"
ROUTE_ROUND_ATOMIC_NOTES_EMPTY = "round.atomic_notes_empty"
ROUTE_ROUND_ATOMIC_NOTES_CREATED = "round.atomic_notes_created"
ROUTE_ROUND_NOTE_ACCEPTED = "round.note_accepted"
ROUTE_ROUND_NOTE_DUPLICATE = "round.note_duplicate"
ROUTE_ROUND_NOTE_CONFLICT = "round.note_conflict"
ROUTE_ANSWER_FINAL_DEMEMOIZATION_FAILED = "answer.final_dememoization_failed"
ROUTE_ANSWER_FINAL_NO_EVIDENCE = "answer.final_no_evidence"
ROUTE_ANSWER_FINAL_SUCCESS = "answer.final_success"
ROUTE_ANSWER_FINAL_STOPPED = "answer.final_stopped"

EXCEL_COLUMNS = [
    "timestamp_utc",
    "session_id",
    "working_memory_id",
    "round",
    "route",
    "component",
    "outcome",
    "reason_code",
    "reason_detail",
    "question",
    "question_language",
    "relevance_threshold",
    "memory_value_threshold",
    "chunk_id",
    "relevance_score",
    "memory_value_score",
    "candidate_count",
    "accepted_evidence_count",
    "created_note_count",
    "accepted_note_count",
    "stop_status",
    "extra_json",
]


@dataclass
class LogEvent:
    timestamp_utc: str
    session_id: str
    route: str
    component: str
    outcome: Outcome
    reason_code: str
    reason_detail: str
    working_memory_id: str | None = None
    round: int | None = None
    question: str | None = None
    question_language: str | None = None
    relevance_threshold: float | None = None
    memory_value_threshold: float | None = None
    chunk_id: str | None = None
    relevance_score: float | None = None
    memory_value_score: float | None = None
    candidate_count: int | None = None
    accepted_evidence_count: int | None = None
    created_note_count: int | None = None
    accepted_note_count: int | None = None
    stop_status: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> dict[str, Any]:
        return {
            "timestamp_utc": self.timestamp_utc,
            "session_id": self.session_id,
            "working_memory_id": self.working_memory_id,
            "round": self.round,
            "route": self.route,
            "component": self.component,
            "outcome": self.outcome,
            "reason_code": self.reason_code,
            "reason_detail": self.reason_detail,
            "question": self.question,
            "question_language": self.question_language,
            "relevance_threshold": self.relevance_threshold,
            "memory_value_threshold": self.memory_value_threshold,
            "chunk_id": self.chunk_id,
            "relevance_score": self.relevance_score,
            "memory_value_score": self.memory_value_score,
            "candidate_count": self.candidate_count,
            "accepted_evidence_count": self.accepted_evidence_count,
            "created_note_count": self.created_note_count,
            "accepted_note_count": self.accepted_note_count,
            "stop_status": self.stop_status,
            "extra_json": json.dumps(self.extra, ensure_ascii=False) if self.extra else "",
        }

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["extra_json"] = payload.pop("extra")
        return payload


class RunLogger:
    """RAG 実行の成功/失敗ルートを JSONL と Excel に記録する。"""

    def __init__(
        self,
        *,
        log_dir: str | Path | None = None,
        enabled: bool | None = None,
        session_id: str | None = None,
        question: str | None = None,
        question_language: str | None = None,
    ) -> None:
        self.enabled = enabled if enabled is not None else os.getenv("RAG_RUN_LOG", "1").lower() not in ("0", "false", "no")
        root = Path(log_dir or os.getenv("RAG_LOG_DIR", "logs"))
        self.log_dir = root
        self.session_id = session_id or uuid4().hex[:16]
        self.question = question
        self.question_language = question_language
        self._events: list[LogEvent] = []
        if self.enabled:
            self.log_dir.mkdir(parents=True, exist_ok=True)

    def log(
        self,
        route: str,
        *,
        component: str,
        outcome: Outcome,
        reason_code: str,
        reason_detail: str,
        working_memory_id: str | None = None,
        round_number: int | None = None,
        relevance_threshold: float | None = None,
        memory_value_threshold: float | None = None,
        chunk_id: str | None = None,
        relevance_score: float | None = None,
        memory_value_score: float | None = None,
        candidate_count: int | None = None,
        accepted_evidence_count: int | None = None,
        created_note_count: int | None = None,
        accepted_note_count: int | None = None,
        stop_status: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        if not self.enabled:
            return
        event = LogEvent(
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            session_id=self.session_id,
            route=route,
            component=component,
            outcome=outcome,
            reason_code=reason_code,
            reason_detail=reason_detail,
            working_memory_id=working_memory_id,
            round=round_number,
            question=self.question,
            question_language=self.question_language,
            relevance_threshold=relevance_threshold,
            memory_value_threshold=memory_value_threshold,
            chunk_id=chunk_id,
            relevance_score=relevance_score,
            memory_value_score=memory_value_score,
            candidate_count=candidate_count,
            accepted_evidence_count=accepted_evidence_count,
            created_note_count=created_note_count,
            accepted_note_count=accepted_note_count,
            stop_status=stop_status,
            extra=extra or {},
        )
        self._events.append(event)
        if not self.enabled:
            return
        self.log_dir.mkdir(parents=True, exist_ok=True)
        jsonl_path = self.log_dir / "rag_events.jsonl"
        with jsonl_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_json(), ensure_ascii=False) + "\n")

    def flush_excel(self) -> Path | None:
        if not self.enabled or not self._events:
            return None
        xlsx_path = self.log_dir / f"rag_session_{self.session_id}.xlsx"
        try:
            from openpyxl import Workbook
        except ImportError as exc:
            raise RuntimeError(
                "Excel logging requires openpyxl. Run `uv add openpyxl`."
            ) from exc
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "events"
        sheet.append(EXCEL_COLUMNS)
        for event in self._events:
            row = event.to_row()
            sheet.append([row[column] for column in EXCEL_COLUMNS])
        workbook.save(xlsx_path)
        return xlsx_path

    @classmethod
    def for_question(cls, question: str, question_language: str, *, enabled: bool | None = None) -> "RunLogger":
        return cls(question=question, question_language=question_language, enabled=enabled)
