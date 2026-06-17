import json
from pathlib import Path

from state_aware_rag.config import RagConfig
from state_aware_rag.embedding import HashedEmbedder
from state_aware_rag.llm import LocalHeuristicLLM
from state_aware_rag.orchestrator import StateAwareRag
from state_aware_rag.run_log import (
    ROUTE_ANSWER_FINAL_NO_EVIDENCE,
    ROUTE_ROUND_NO_ACCEPTED_EVIDENCE,
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
