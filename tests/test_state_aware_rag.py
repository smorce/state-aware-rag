import json
from pathlib import Path

from state_aware_rag import StateAwareRag
from state_aware_rag.config import RagConfig
from state_aware_rag.embedding import HashedEmbedder
from state_aware_rag.llm import LocalHeuristicLLM
from state_aware_rag.models import (
    Evidence,
    MemoryNote,
    NoteStatus,
    OpenQuestion,
    RetrievalMethod,
    SearchAction,
    SearchBudget,
    SearchState,
    WorkingMemoryStatus,
)
from state_aware_rag.store import SQLiteRagStore
from state_aware_rag.strategy import SocraticSearchStrategy
from state_aware_rag.text import MSG_DEMEMOIZATION_FAILED, normalize_for_fulltext


def make_rag(tmp_path: Path) -> StateAwareRag:
    store = SQLiteRagStore(tmp_path / "rag.sqlite3", embedder=HashedEmbedder())
    return StateAwareRag(store=store, config=RagConfig(max_rounds=2, embedding_backend="hashed"))


def test_ingest_and_answer_uses_memory_notes_not_raw_chunks(tmp_path: Path) -> None:
    rag = make_rag(tmp_path)
    rag.ingest_document(
        title="State-Aware RAG memo",
        body=(
            "State-Aware RAG keeps a working memory for each question. "
            "Search results are converted into evidence and atomic memory notes. "
            "The final answer must use memory notes instead of raw retrieval chunks."
        ),
        source_uri="memory://spec",
    )

    result = rag.answer("How does State-Aware RAG use working memory?")

    assert result.working_memory.status in {
        WorkingMemoryStatus.COMPLETED,
        WorkingMemoryStatus.STOPPED_BY_MAX_ROUNDS,
        WorkingMemoryStatus.STOPPED_BY_NO_NEW_NOTES,
        WorkingMemoryStatus.STOPPED_BY_LOW_GAIN,
    }
    assert result.memory_notes
    assert result.evidence
    assert "working memory" in result.answer.lower()
    assert "Search results are converted" not in result.answer


def test_duplicate_notes_merge_evidence_without_creating_extra_active_note(tmp_path: Path) -> None:
    rag = make_rag(tmp_path)
    rag.ingest_document(
        title="First source",
        body="Working memory stores concise atomic facts for a question.",
        source_uri="memory://a",
    )
    rag.ingest_document(
        title="Second source",
        body="For each question, working memory stores concise atomic facts.",
        source_uri="memory://b",
    )

    result = rag.answer("What does working memory store?")
    normalized = [note.normalized_claim for note in result.memory_notes]

    assert len(normalized) == len(set(normalized))
    assert any(note.source_count >= 1 for note in result.memory_notes)


def test_graph_search_expands_from_memory_entities_and_evidence_neighbors(tmp_path: Path) -> None:
    rag = make_rag(tmp_path)
    doc = rag.ingest_document(
        title="HelixDB design",
        body=(
            "HelixDB stores documents and chunks. "
            "Chunks mention Entity nodes. "
            "Evidence can point back to source chunks."
        ),
        source_uri="memory://helix",
        chunk_size=45,
    )
    wm = rag.store.create_working_memory("How is HelixDB connected?")
    rag.store.add_entity("HelixDB")
    rag.store.link_chunk_entity(doc.chunks[0].id, "HelixDB")
    note = rag.store.create_memory_note(
        working_memory_id=wm.id,
        claim="HelixDB stores documents and chunks.",
        note_type="fact",
        confidence=0.9,
        evidence_ids=[],
        created_round=1,
    )
    rag.store.link_note_entity(note.id, "HelixDB")

    candidates = rag.retriever.graph_search(["HelixDB"], wm.id, top_k=10)

    assert candidates
    assert candidates[0].method == RetrievalMethod.GRAPH
    assert "HelixDB" in candidates[0].body


def test_select_actions_penalizes_repeated_queries(tmp_path: Path) -> None:
    strategy = SocraticSearchStrategy(LocalHeuristicLLM(), RagConfig())
    state = SearchState(
        question="What does working memory store?",
        working_memory_id="wm_1",
        round=1,
        notes=[],
        open_questions=[OpenQuestion(question="What facts are stored?", reason="missing")],
        previous_queries=["working memory duplicate query"],
        previous_evidence_ids=[],
    )
    repeated = SearchAction(
        action_id="act_repeated",
        sub_question="What facts are stored?",
        vector_query="working memory duplicate query",
        text_query="working memory duplicate query",
        graph_seed_entities=[],
        expected_gain=1.0,
        cost_estimate=1.0,
        priority=1,
    )
    fresh = SearchAction(
        action_id="act_fresh",
        sub_question="What facts are stored?",
        vector_query="atomic facts storage",
        text_query="atomic facts storage",
        graph_seed_entities=[],
        expected_gain=1.0,
        cost_estimate=1.0,
        priority=1,
    )

    selected = strategy.select_actions([repeated, fresh], SearchBudget(max_actions=1), state)

    assert selected == [fresh]


def test_stops_when_open_questions_resolved(tmp_path: Path) -> None:
    rag = make_rag(tmp_path)

    status = rag._stop_status(
        1,
        no_new_note_rounds=0,
        low_gain_rounds=0,
        open_question_count=0,
        active_note_count=1,
    )

    assert status == WorkingMemoryStatus.COMPLETED


def test_resolve_open_question_matches_claim(tmp_path: Path) -> None:
    rag = make_rag(tmp_path)
    wm = rag.store.create_working_memory("What does working memory store?")
    rag.store.add_open_question(wm.id, "What does working memory store?", "missing")
    rag.store.add_open_question(wm.id, "Who owns the system?", "missing")

    rag._resolve_open_questions_for_claim(wm.id, "Working memory stores concise atomic facts.")

    remaining = rag.store.list_open_questions(wm.id)
    assert remaining == [{"question": "Who owns the system?", "reason": "missing"}]


def test_duplicate_creates_duplicate_shadow_note_and_edge(tmp_path: Path) -> None:
    rag = make_rag(tmp_path)
    doc = rag.ingest_document(
        title="Memory",
        body="Working memory stores concise atomic facts. Working memory stores facts for each question.",
        source_uri="memory://dup",
        chunk_size=80,
    )
    wm = rag.store.create_working_memory("What does working memory store?")
    first = rag.store.create_evidence(
        wm.id,
        doc.chunks[0].id,
        round_number=1,
        query="working memory",
        body_excerpt=doc.chunks[0].body,
        retrieval_method=RetrievalMethod.TEXT,
        raw_rank=1,
        relevance_score=0.9,
        memory_value_score=0.8,
        accepted=True,
        source_uri="memory://dup",
    )
    second = rag.store.create_evidence(
        wm.id,
        doc.chunks[0].id,
        round_number=2,
        query="working memory facts",
        body_excerpt=doc.chunks[0].body,
        retrieval_method=RetrievalMethod.TEXT,
        raw_rank=1,
        relevance_score=0.7,
        memory_value_score=0.6,
        accepted=True,
        source_uri="memory://dup",
    )
    canonical = rag.store.create_memory_note(
        wm.id,
        "Working memory stores concise atomic facts.",
        "fact",
        0.9,
        [first.id],
        1,
    )

    accepted_count, duplicate_count, conflict_count = rag._save_notes(
        wm.id,
        2,
        {
            "notes": [
                {
                    "claim": "Working memory stores concise atomic facts.",
                    "note_type": "fact",
                    "supported_by_evidence_ids": [second.id],
                    "confidence": 0.8,
                }
            ]
        },
    )

    assert (accepted_count, duplicate_count, conflict_count) == (0, 1, 0)
    assert len(rag.store.list_memory_notes(wm.id)) == 1
    all_notes = rag.store.list_memory_notes(wm.id, active_only=False)
    duplicate_notes = [note for note in all_notes if note.status.value == "duplicate"]
    assert len(duplicate_notes) == 1
    edge = rag.store.conn.execute("SELECT * FROM duplicate_edges").fetchone()
    assert edge is not None
    assert edge["duplicate_note_id"] == duplicate_notes[0].id
    assert edge["canonical_note_id"] == canonical.id


class EmptyNoteLLM(LocalHeuristicLLM):
    def create_atomic_notes(self, question: str, working_memory: list[MemoryNote], evidence: list[Evidence]) -> dict[str, object]:
        return {"notes": [], "open_questions": []}

    def generate_final_answer(
        self,
        question: str,
        memory_notes: list[MemoryNote],
        evidence_by_note: dict[str, list[Evidence]],
        conflicts: list[tuple[MemoryNote, MemoryNote, float]],
        open_questions: list[OpenQuestion],
    ) -> str:
        raise AssertionError("§15.2 の固定メッセージでは LLM を呼ばない")


def test_answer_dememoization_failed_message(tmp_path: Path) -> None:
    store = SQLiteRagStore(tmp_path / "rag.sqlite3", embedder=HashedEmbedder())
    rag = StateAwareRag(
        store=store,
        config=RagConfig(max_rounds=1, embedding_backend="hashed", relevance_threshold=0.0, memory_value_threshold=0.0),
        llm=EmptyNoteLLM(),
    )
    rag.ingest_document(
        title="Memo",
        body="Working memory stores concise atomic facts for each question.",
        source_uri="memory://memo",
    )

    result = rag.answer("What does working memory store?")

    assert result.evidence
    assert result.memory_notes == []
    assert result.answer == MSG_DEMEMOIZATION_FAILED


def test_answer_marks_working_memory_failed_on_internal_exception(tmp_path: Path, monkeypatch) -> None:
    rag = make_rag(tmp_path)
    rag.config = RagConfig(max_rounds=1, embedding_backend="hashed", relevance_threshold=0.0, memory_value_threshold=0.0)
    rag.retriever.config = rag.config
    rag.ingest_document(
        title="Memo",
        body="Working memory stores concise atomic facts for each question.",
        source_uri="memory://memo",
    )

    def fail_save_notes(*args, **kwargs):
        raise RuntimeError("forced failure")

    monkeypatch.setattr(rag, "_save_notes", fail_save_notes)

    try:
        rag.answer("What does working memory store?")
    except RuntimeError as exc:
        assert str(exc) == "forced failure"
    else:
        raise AssertionError("例外が再送出されること")

    row = rag.store.conn.execute("SELECT status FROM working_memories ORDER BY rowid DESC LIMIT 1").fetchone()
    assert row["status"] == WorkingMemoryStatus.FAILED.value


def test_graph_search_includes_conflicted_note_chunks(tmp_path: Path) -> None:
    rag = make_rag(tmp_path)
    doc = rag.ingest_document(
        title="Risk",
        body="RiskSignal appears in the audit appendix.",
        source_uri="memory://risk",
        extract_entities=False,
    )
    wm = rag.store.create_working_memory("What risk is unresolved?")
    active = rag.store.create_memory_note(wm.id, "plain claim one", "fact", 0.9, [], 1)
    conflicted = rag.store.create_memory_note(
        wm.id,
        "plain claim two",
        "fact",
        0.8,
        [],
        1,
        status=NoteStatus.DUPLICATE,
    )
    rag.store.add_entity("RiskSignal")
    rag.store.link_note_entity(conflicted.id, "RiskSignal")
    rag.store.link_chunk_entity(doc.chunks[0].id, "RiskSignal")
    rag.store.add_conflict(active.id, conflicted.id, 0.8)

    candidates = rag.retriever.graph_search([], wm.id, top_k=10)

    conflicted_candidates = [
        candidate
        for candidate in candidates
        if candidate.graph_reason == "矛盾の可能性がある MemoryNote 経由で発見"
    ]
    assert conflicted_candidates
    assert conflicted_candidates[0].chunk_id == doc.chunks[0].id


def test_normalize_for_fulltext_japanese() -> None:
    normalized = normalize_for_fulltext("作業用メモ")

    assert " " in normalized
    assert "作業用" in normalized
    assert "メモ" in normalized


def test_round_log_payload_includes_accepted_evidence_ids(tmp_path: Path) -> None:
    rag = make_rag(tmp_path)
    doc = rag.ingest_document(title="Memo", body="Working memory stores facts.", source_uri="memory://memo")
    wm = rag.store.create_working_memory("What is stored?")
    evidence = rag.store.create_evidence(
        wm.id,
        doc.chunks[0].id,
        round_number=1,
        query="working memory",
        body_excerpt=doc.chunks[0].body,
        retrieval_method=RetrievalMethod.TEXT,
        raw_rank=1,
        relevance_score=0.9,
        memory_value_score=0.9,
        accepted=True,
        source_uri="memory://memo",
    )

    rag._record_log(wm.id, 1, ["act_1"], 1, [evidence], 0, 0, 0, 0, 0.5, None)

    row = rag.store.conn.execute("SELECT payload FROM round_logs").fetchone()
    payload = json.loads(row["payload"])
    assert payload["accepted_evidence_ids"] == [evidence.id]
