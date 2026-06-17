import json
from pathlib import Path

from state_aware_rag.config import RagConfig
from state_aware_rag.embedding import HashedEmbedder
from state_aware_rag.llm import LocalHeuristicLLM
from state_aware_rag.models import Evidence, MemoryNote
from state_aware_rag.orchestrator import StateAwareRag
from state_aware_rag.run_log import (
    ROUTE_ANSWER_FINAL_NO_EVIDENCE,
    ROUTE_ROUND_LOOP_CONTINUE,
    ROUTE_ROUND_NO_ACCEPTED_EVIDENCE,
    ROUTE_ROUND_OPEN_QUESTIONS_ADDED,
    ROUTE_ROUND_SEARCH_PLANNED,
    ROUTE_ROUND_STARTED,
    ROUTE_SCORE_CANDIDATE_REJECTED_RELEVANCE,
    RunLogger,
)
from state_aware_rag.store import SQLiteRagStore


from state_aware_rag.bosun import RuleBosunScorer
from state_aware_rag.models import RetrievalCandidate


class RejectAllBosun(RuleBosunScorer):
    def relevance_score(self, question: str, working_memory_summary: str, candidate: RetrievalCandidate) -> float:
        return 0.10

    def memory_value_score(self, question: str, working_memory_summary: str, candidate: RetrievalCandidate) -> float:
        return 0.10


class MultiRoundLLM(LocalHeuristicLLM):
    """1 ラウンド目で open_question を残し、2 ラウンド目で追加検索できるようにする。"""

    def create_atomic_notes(
        self,
        question: str,
        working_memory: list[MemoryNote],
        evidence: list[Evidence],
    ) -> dict[str, object]:
        payload = super().create_atomic_notes(question, working_memory, evidence)
        if working_memory:
            return payload
        payload = dict(payload)
        payload["open_questions"] = [
            {
                "question": "Who owns the system?",
                "reason": "ownership is not covered by the first retrieved document",
            }
        ]
        return payload


def test_run_logger_records_bosun_rejection_reason(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    logger = RunLogger.for_question("What is working memory?", "en", enabled=True)
    logger.log_dir = log_dir
    logger.session_id = "testsession01"

    store = SQLiteRagStore(tmp_path / "rag.sqlite3", embedder=HashedEmbedder())
    rag = StateAwareRag(
        store=store,
        config=RagConfig(
            max_rounds=1,
            embedding_backend="hashed",
            relevance_threshold=0.99,
            memory_value_threshold=0.99,
            run_log_enabled=True,
        ),
        llm=LocalHeuristicLLM(),
        bosun=RejectAllBosun(),
        run_logger=logger,
    )
    rag.ingest_document(
        title="Memo",
        body="Working memory stores concise atomic facts for each question.",
        source_uri="memory://memo",
    )

    result = rag.answer("What does working memory store?")
    xlsx = logger.flush_excel()

    assert result.evidence == []
    assert xlsx is not None
    assert xlsx.exists()

    events = [json.loads(line) for line in (log_dir / "rag_events.jsonl").read_text(encoding="utf-8").splitlines()]
    routes = [event["route"] for event in events]
    assert ROUTE_SCORE_CANDIDATE_REJECTED_RELEVANCE in routes
    assert ROUTE_ROUND_NO_ACCEPTED_EVIDENCE in routes
    assert ROUTE_ANSWER_FINAL_NO_EVIDENCE in routes

    rejection = next(event for event in events if event["route"] == ROUTE_SCORE_CANDIDATE_REJECTED_RELEVANCE)
    assert rejection["reason_code"] == "relevance_below_threshold"
    assert "relevance=" in rejection["reason_detail"]
    assert "< threshold=" in rejection["reason_detail"]


def test_run_logger_records_multi_round_search_plan_and_continue(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    logger = RunLogger.for_question(
        "What does working memory store and who owns the system?",
        "en",
        enabled=True,
    )
    logger.log_dir = log_dir
    logger.session_id = "multiround01"

    store = SQLiteRagStore(tmp_path / "rag.sqlite3", embedder=HashedEmbedder())
    rag = StateAwareRag(
        store=store,
        config=RagConfig(
            max_rounds=3,
            embedding_backend="hashed",
            relevance_threshold=0.0,
            memory_value_threshold=0.0,
            run_log_enabled=True,
        ),
        llm=MultiRoundLLM(),
        run_logger=logger,
    )
    rag.ingest_document(
        title="Memory",
        body="Working memory stores concise atomic facts for each question.",
        source_uri="memory://memory",
    )
    rag.ingest_document(
        title="Ownership",
        body="The system is owned by the RAG team.",
        source_uri="memory://owner",
    )

    result = rag.answer("What does working memory store and who owns the system?")

    assert result.working_memory.round_count >= 2
    events = [json.loads(line) for line in (log_dir / "rag_events.jsonl").read_text(encoding="utf-8").splitlines()]
    routes = [event["route"] for event in events]
    rounds_started = [event["round"] for event in events if event["route"] == ROUTE_ROUND_STARTED]

    assert ROUTE_ROUND_SEARCH_PLANNED in routes
    assert ROUTE_ROUND_OPEN_QUESTIONS_ADDED in routes
    assert ROUTE_ROUND_LOOP_CONTINUE in routes
    assert 2 in rounds_started

    planned_round_2 = next(
        event
        for event in events
        if event["route"] == ROUTE_ROUND_SEARCH_PLANNED and event["round"] == 2
    )
    assert planned_round_2["extra_json"]["selected_count"] >= 1
    assert planned_round_2["extra_json"]["actions"][0]["sub_question"]

    continue_event = next(event for event in events if event["route"] == ROUTE_ROUND_LOOP_CONTINUE)
    assert continue_event["extra_json"]["continue_reason"] == "open_questions_remaining"
    assert continue_event["extra_json"]["next_round"] == 2
