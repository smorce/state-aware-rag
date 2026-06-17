from pathlib import Path

from state_aware_rag import StateAwareRag
from state_aware_rag.config import RagConfig
from state_aware_rag.embedding import HashedEmbedder
from state_aware_rag.models import RetrievalMethod, WorkingMemoryStatus
from state_aware_rag.store import SQLiteRagStore


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

