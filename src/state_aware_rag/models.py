from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkingMemoryStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    STOPPED_BY_MAX_ROUNDS = "stopped_by_max_rounds"
    STOPPED_BY_NO_NEW_NOTES = "stopped_by_no_new_notes"
    STOPPED_BY_LOW_GAIN = "stopped_by_low_gain"
    FAILED = "failed"


class NoteType(StrEnum):
    FACT = "fact"
    DEFINITION = "definition"
    CONSTRAINT = "constraint"
    OPEN_QUESTION = "open_question"
    INTERMEDIATE_ANSWER = "intermediate_answer"
    ASSUMPTION = "assumption"


class NoteStatus(StrEnum):
    ACTIVE = "active"
    DUPLICATE = "duplicate"
    CONFLICTED = "conflicted"
    DEPRECATED = "deprecated"
    REJECTED = "rejected"


class RetrievalMethod(StrEnum):
    VECTOR = "vector"
    TEXT = "text"
    GRAPH = "graph"
    HYBRID = "hybrid"


class EntityType(StrEnum):
    PERSON = "Person"
    COMPANY = "Company"
    PRODUCT = "Product"
    CONTRACT = "Contract"
    DATE = "Date"
    CONCEPT = "Concept"
    OTHER = "Other"


ENTITY_TYPE_LABELS: tuple[EntityType, ...] = tuple(EntityType)


@dataclass(frozen=True)
class Document:
    id: str
    title: str
    source_uri: str
    created_at: str
    updated_at: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Chunk:
    id: str
    document_id: str
    body: str
    embedding: list[float]
    token_count: int
    section_title: str | None
    source_uri: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IngestedDocument:
    document: Document
    chunks: list[Chunk]


@dataclass(frozen=True)
class WorkingMemory:
    id: str
    question_id: str
    original_question: str
    status: WorkingMemoryStatus
    round_count: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Evidence:
    id: str
    chunk_id: str
    round: int
    query: str
    body_excerpt: str
    retrieval_method: RetrievalMethod
    raw_rank: int
    relevance_score: float
    memory_value_score: float
    accepted: bool
    source_uri: str


@dataclass(frozen=True)
class MemoryNote:
    id: str
    working_memory_id: str
    claim: str
    normalized_claim: str
    note_type: NoteType
    support_score: float
    relevance_score: float
    novelty_score: float
    conflict_score: float
    confidence: float
    source_count: int
    embedding: list[float]
    created_round: int
    last_updated_round: int
    status: NoteStatus


@dataclass(frozen=True)
class OpenQuestion:
    question: str
    reason: str


@dataclass(frozen=True)
class RetrievalCandidate:
    chunk_id: str
    body: str
    method: RetrievalMethod
    raw_score: float
    raw_rank: int
    source_uri: str
    graph_reason: str | None = None
    retrieval_methods: tuple[RetrievalMethod, ...] = field(default_factory=tuple)
    vector_rank: int | None = None
    text_rank: int | None = None


@dataclass(frozen=True)
class SearchAction:
    action_id: str
    sub_question: str
    vector_query: str
    text_query: str
    graph_seed_entities: list[str]
    expected_gain: float
    cost_estimate: float
    priority: int = 1


@dataclass(frozen=True)
class SearchBudget:
    max_actions: int


@dataclass(frozen=True)
class SearchState:
    question: str
    working_memory_id: str
    round: int
    notes: list[MemoryNote]
    open_questions: list[OpenQuestion]
    previous_queries: list[str]
    previous_evidence_ids: list[str]


@dataclass(frozen=True)
class RoundLog:
    working_memory_id: str
    round: int
    actions: list[str]
    candidate_count: int
    accepted_evidence_count: int
    created_note_count: int
    accepted_note_count: int
    duplicate_count: int
    conflict_count: int
    gain: float
    stop_reason: str | None
    accepted_evidence_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AnswerResult:
    answer: str
    working_memory: WorkingMemory
    memory_notes: list[MemoryNote]
    evidence: list[Evidence]
    open_questions: list[OpenQuestion]
    conflicts: list[tuple[MemoryNote, MemoryNote, float]]


@dataclass(frozen=True)
class Entity:
    id: str
    entity_type: EntityType
    canonical_name: str
    normalized_name: str
    aliases: tuple[str, ...]
    embedding: list[float]
    attributes: dict[str, Any]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Relation:
    id: str
    from_entity_id: str
    to_entity_id: str
    relation_type: str
    source_chunk_id: str
    confidence: float
    evidence_text: str
    attributes: dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class ExtractionResult:
    chunk_id: str
    entities: tuple[Entity, ...]
    relations: tuple[Relation, ...]
