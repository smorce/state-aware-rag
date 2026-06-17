from __future__ import annotations

from pathlib import Path
from typing import Any

from state_aware_rag.embedding import HashedEmbedder
from state_aware_rag.helix import HelixTypeScriptQueryBuilder
from state_aware_rag.helix_store import HelixBackedRagStore
from state_aware_rag.models import NoteStatus, RetrievalMethod, RoundLog, WorkingMemoryStatus


class FakeHelixClient:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def query(self, request_body: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(request_body)
        encoded = str(request_body)
        if "VectorSearchNodes" in encoded:
            return {"chunks": [{"id": "chunk_1", "body": "vector body", "source_uri": "src", "distance": 0.25}]}
        if "Search" in encoded:
            return {"chunks": [{"id": "chunk_2", "body": "text body", "source_uri": "src", "distance": 0.5}]}
        if "MENTIONS" in encoded:
            return {"chunks": [{"id": "chunk_3", "body": "graph body", "source_uri": "src"}]}
        if "HAS_NOTE" in encoded:
            return {"chunks": [{"id": "chunk_4", "body": "memory graph body", "source_uri": "src"}]}
        return {}


def test_helix_query_builder_supports_parameterized_dynamic_json() -> None:
    builder = HelixTypeScriptQueryBuilder()

    request = builder.build_with_values(
        'readBatch().varAs("chunks", g().textSearchNodesWith("Chunk", "body", params.query, params.k, null).valueMap(null)).returning(["chunks"])',
        "defineParams({query:param.string(), k:param.i64()})",
        {"query": "working memory", "k": 3},
    )

    assert request["request_type"] == "read"
    assert request["parameters"]["query"] == "working memory"
    assert request["parameters"]["k"] == 3


def test_helix_backed_store_uses_helix_for_vector_and_text_search(tmp_path: Path) -> None:
    fake = FakeHelixClient()
    store = HelixBackedRagStore(tmp_path / "mirror.sqlite3", http_client=fake, embedder=HashedEmbedder())

    vector = store.helix_vector_search("memory", 5)
    text = store.helix_text_search("memory", 5)

    assert vector[0].method == RetrievalMethod.VECTOR
    assert vector[0].chunk_id == "chunk_1"
    assert text[0].method == RetrievalMethod.TEXT
    assert text[0].chunk_id == "chunk_2"
    assert len(fake.requests) >= 3


def test_helix_backed_store_writes_required_graph_edges(tmp_path: Path) -> None:
    fake = FakeHelixClient()
    store = HelixBackedRagStore(tmp_path / "mirror.sqlite3", http_client=fake, embedder=HashedEmbedder())
    doc = store.ingest_document(title="Doc", body="State-Aware RAG uses memory notes.", source_uri="memory://doc")
    wm = store.create_working_memory("What does it use?")
    evidence = store.create_evidence(
        wm.id,
        doc.chunks[0].id,
        round_number=1,
        query="memory notes",
        body_excerpt=doc.chunks[0].body,
        retrieval_method=RetrievalMethod.TEXT,
        raw_rank=1,
        relevance_score=0.9,
        memory_value_score=0.9,
        accepted=True,
        source_uri="memory://doc",
    )
    store.create_memory_note(
        wm.id,
        "State-Aware RAG uses memory notes.",
        "fact",
        0.9,
        [evidence.id],
        1,
    )
    store.record_round_log(
        RoundLog(
            working_memory_id=wm.id,
            round=1,
            actions=["act_1"],
            candidate_count=1,
            accepted_evidence_count=1,
            created_note_count=1,
            accepted_note_count=1,
            duplicate_count=0,
            conflict_count=0,
            gain=1.5,
            stop_reason=None,
            accepted_evidence_ids=[evidence.id],
        )
    )

    sent = "\n".join(str(request) for request in fake.requests)
    assert "HAS_CHUNK" in sent
    assert "MENTIONS" in sent
    assert "HAS_MEMORY" in sent
    assert "FROM_CHUNK" in sent
    assert "HAS_NOTE" in sent
    assert "SUPPORTED_BY" in sent
    assert "RELATED_TO" in sent
    assert "SearchRound" in sent
    assert "RETURNED" in sent
    assert "UPDATED" in sent


def test_helix_backed_store_writes_relation_edges_when_present(tmp_path: Path) -> None:
    from state_aware_rag.extraction import EntityResolver, normalize_entity_name
    from state_aware_rag.models import Entity, EntityType, Relation, new_id, now_iso

    fake = FakeHelixClient()
    store = HelixBackedRagStore(tmp_path / "mirror.sqlite3", http_client=fake, embedder=HashedEmbedder())
    doc = store.ingest_document(
        title="雇用",
        body="山田太郎は2020年からABC株式会社で働いている。",
        source_uri="memory://employment",
        chunk_size=200,
        overlap=0,
        extract_entities=False,
    )
    chunk = doc.chunks[0]
    timestamp = now_iso()
    resolver = EntityResolver(store, store.embedder)
    person = resolver.resolve(
        Entity(
            id=new_id("ent"),
            entity_type=EntityType.PERSON,
            canonical_name="山田太郎",
            normalized_name=normalize_entity_name("山田太郎"),
            aliases=(),
            embedding=store.embedder.embed_claim("山田太郎"),
            attributes={},
            created_at=timestamp,
            updated_at=timestamp,
        ),
        surface="山田太郎",
        source_chunk_id=chunk.id,
    )
    company = resolver.resolve(
        Entity(
            id=new_id("ent"),
            entity_type=EntityType.COMPANY,
            canonical_name="ABC株式会社",
            normalized_name=normalize_entity_name("ABC株式会社"),
            aliases=(),
            embedding=store.embedder.embed_claim("ABC株式会社"),
            attributes={},
            created_at=timestamp,
            updated_at=timestamp,
        ),
        surface="ABC株式会社",
        source_chunk_id=chunk.id,
    )
    store.link_chunk_entity(chunk.id, person.id, surface="山田太郎")
    store.link_chunk_entity(chunk.id, company.id, surface="ABC株式会社")
    store.save_relation(
        Relation(
            id=new_id("rel"),
            from_entity_id=person.id,
            to_entity_id=company.id,
            relation_type="WORKS_AT",
            source_chunk_id=chunk.id,
            confidence=0.9,
            evidence_text="山田太郎は2020年からABC株式会社で働いている。",
            attributes={},
            created_at=timestamp,
        )
    )
    store._sync_chunk_graph(chunk.id)

    sent = "\n".join(str(request) for request in fake.requests)
    assert "WORKS_AT" in sent
    assert "Person" in sent
    assert "Company" in sent


def test_helix_backed_store_uses_helix_for_graph_search(tmp_path: Path) -> None:
    fake = FakeHelixClient()
    store = HelixBackedRagStore(tmp_path / "mirror.sqlite3", http_client=fake, embedder=HashedEmbedder())

    graph = store.helix_graph_search(["State-Aware RAG"], "wm_1", 5)

    assert graph[0].method == RetrievalMethod.GRAPH
    assert graph[0].chunk_id == "chunk_3"
    sent = "\n".join(str(request) for request in fake.requests)
    assert "MENTIONS" in sent


def test_helix_backed_store_writes_duplicate_of_edge(tmp_path: Path) -> None:
    fake = FakeHelixClient()
    store = HelixBackedRagStore(tmp_path / "mirror.sqlite3", http_client=fake, embedder=HashedEmbedder())
    doc = store.ingest_document(title="Doc", body="Working memory stores facts.", source_uri="memory://doc")
    wm = store.create_working_memory("What does working memory store?")
    evidence = store.create_evidence(
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
        source_uri="memory://doc",
    )
    canonical = store.create_memory_note(wm.id, "Working memory stores facts.", "fact", 0.9, [evidence.id], 1)
    duplicate = store.create_memory_note(
        wm.id,
        "Working memory stores facts.",
        "fact",
        0.8,
        [evidence.id],
        1,
        status=NoteStatus.DUPLICATE,
    )

    store.add_duplicate_edge(duplicate.id, canonical.id, 0.95)

    sent = "\n".join(str(request) for request in fake.requests)
    assert "DUPLICATE_OF" in sent


def test_helix_graph_search_includes_neighbor_chunks(tmp_path: Path) -> None:
    fake = FakeHelixClient()
    store = HelixBackedRagStore(tmp_path / "mirror.sqlite3", http_client=fake, embedder=HashedEmbedder())
    doc = store.ingest_document(
        title="Doc",
        body=(
            "Alpha introduces working memory. "
            "Beta explains that evidence points to chunks. "
            "Gamma covers final answers."
        ),
        source_uri="memory://neighbors",
        chunk_size=35,
        overlap=0,
        extract_entities=False,
    )
    wm = store.create_working_memory("How does evidence connect?")
    evidence = store.create_evidence(
        wm.id,
        doc.chunks[1].id,
        round_number=1,
        query="evidence chunks",
        body_excerpt=doc.chunks[1].body,
        retrieval_method=RetrievalMethod.TEXT,
        raw_rank=1,
        relevance_score=0.9,
        memory_value_score=0.9,
        accepted=True,
        source_uri="memory://neighbors",
    )
    store.create_memory_note(wm.id, "Evidence points to chunks.", "fact", 0.9, [evidence.id], 1)

    graph = store.helix_graph_search([], wm.id, 10)
    neighbor_candidates = [
        candidate
        for candidate in graph
        if candidate.graph_reason == "採用済み Evidence と同じ Document の前後 Chunk"
    ]

    assert neighbor_candidates
    assert {candidate.chunk_id for candidate in neighbor_candidates} & {chunk.id for chunk in doc.chunks}


def test_helix_update_working_memory_syncs_status(tmp_path: Path) -> None:
    fake = FakeHelixClient()
    store = HelixBackedRagStore(tmp_path / "mirror.sqlite3", http_client=fake, embedder=HashedEmbedder())
    wm = store.create_working_memory("What is stored?")

    updated = store.update_working_memory(wm.id, status=WorkingMemoryStatus.COMPLETED, round_count=2)

    assert updated.status == WorkingMemoryStatus.COMPLETED
    assert updated.round_count == 2
    sent = "\n".join(str(request) for request in fake.requests)
    assert "WorkingMemory" in sent
    assert "SetProperty" in sent
    assert "round_count" in sent
    assert "completed" in sent


def test_search_round_links_exact_evidence_ids(tmp_path: Path) -> None:
    fake = FakeHelixClient()
    store = HelixBackedRagStore(tmp_path / "mirror.sqlite3", http_client=fake, embedder=HashedEmbedder())
    doc = store.ingest_document(title="Doc", body="Alpha evidence. Beta evidence.", source_uri="memory://doc")
    wm = store.create_working_memory("What evidence?")
    first = store.create_evidence(
        wm.id,
        doc.chunks[0].id,
        round_number=1,
        query="alpha",
        body_excerpt=doc.chunks[0].body,
        retrieval_method=RetrievalMethod.TEXT,
        raw_rank=1,
        relevance_score=0.9,
        memory_value_score=0.9,
        accepted=True,
        source_uri="memory://doc",
    )
    second = store.create_evidence(
        wm.id,
        doc.chunks[0].id,
        round_number=2,
        query="beta",
        body_excerpt=doc.chunks[0].body,
        retrieval_method=RetrievalMethod.TEXT,
        raw_rank=1,
        relevance_score=0.9,
        memory_value_score=0.9,
        accepted=True,
        source_uri="memory://doc",
    )

    store.record_round_log(
        RoundLog(
            working_memory_id=wm.id,
            round=1,
            actions=["act_1"],
            candidate_count=2,
            accepted_evidence_count=1,
            created_note_count=0,
            accepted_note_count=0,
            duplicate_count=0,
            conflict_count=0,
            gain=0.5,
            stop_reason=None,
            accepted_evidence_ids=[first.id],
        )
    )

    returned_requests = [request for request in fake.requests if "RETURNED" in str(request)]
    assert len(returned_requests) == 1
    assert returned_requests[0]["parameters"]["to_id"] == first.id
    assert returned_requests[0]["parameters"]["to_id"] != second.id


def test_helix_graph_search_queries_conflicts_with(tmp_path: Path) -> None:
    fake = FakeHelixClient()
    store = HelixBackedRagStore(tmp_path / "mirror.sqlite3", http_client=fake, embedder=HashedEmbedder())

    store.helix_graph_search([], "wm_1", 5)

    sent = "\n".join(str(request) for request in fake.requests)
    assert "CONFLICTS_WITH" in sent


def test_helix_ingest_normalizes_chunk_body_for_text_index(tmp_path: Path) -> None:
    fake = FakeHelixClient()
    store = HelixBackedRagStore(tmp_path / "mirror.sqlite3", http_client=fake, embedder=HashedEmbedder())

    doc = store.ingest_document(
        title="日本語",
        body="作業用メモは質問ごとに事実を保持する。",
        source_uri="memory://jp",
        chunk_size=200,
        overlap=0,
        extract_entities=False,
    )

    chunk_requests = [
        request
        for request in fake.requests
        if request.get("parameters", {}).get("id") == doc.chunks[0].id
        and request.get("parameters", {}).get("document_id") == doc.document.id
    ]
    assert chunk_requests
    assert chunk_requests[0]["parameters"]["body"] != doc.chunks[0].body
    assert " " in chunk_requests[0]["parameters"]["body"]
