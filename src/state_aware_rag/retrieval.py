from __future__ import annotations

from collections import defaultdict

from state_aware_rag.config import RagConfig
from state_aware_rag.models import RetrievalCandidate, RetrievalMethod
from state_aware_rag.store import SQLiteRagStore
from state_aware_rag.text import bm25_like_score, cosine_similarity, normalize_for_fulltext


class Retriever:
    def __init__(self, store: SQLiteRagStore, config: RagConfig | None = None) -> None:
        self.store = store
        self.config = config or store.config

    def vector_search(self, query_text: str, top_k: int | None = None) -> list[RetrievalCandidate]:
        if hasattr(self.store, "helix_vector_search"):
            return self.store.helix_vector_search(query_text, top_k or self.config.vector_top_k)  # type: ignore[attr-defined]
        query_embedding = self.store.embedder.embed_query(query_text)
        scored = [
            (cosine_similarity(query_embedding, chunk.embedding), chunk)
            for chunk in self.store.list_chunks()
        ]
        scored.sort(key=lambda item: item[0], reverse=True)
        candidates: list[RetrievalCandidate] = []
        for rank, (score, chunk) in enumerate(scored[: top_k or self.config.vector_top_k], start=1):
            if score <= 0:
                continue
            candidates.append(
                RetrievalCandidate(
                    chunk_id=chunk.id,
                    body=chunk.body,
                    method=RetrievalMethod.VECTOR,
                    query=query_text,
                    raw_score=score,
                    raw_rank=rank,
                    source_uri=chunk.source_uri,
                    retrieval_methods=(RetrievalMethod.VECTOR,),
                    vector_rank=rank,
                )
            )
        return candidates

    def text_search(self, query_text: str, top_k: int | None = None) -> list[RetrievalCandidate]:
        normalized_query = normalize_for_fulltext(query_text) if self.config.fulltext_normalize else query_text
        if hasattr(self.store, "helix_text_search"):
            return self.store.helix_text_search(normalized_query, top_k or self.config.text_top_k)  # type: ignore[attr-defined]
        scored = [
            (
                bm25_like_score(
                    normalized_query,
                    normalize_for_fulltext(chunk.body) if self.config.fulltext_normalize else chunk.body,
                ),
                chunk,
            )
            for chunk in self.store.list_chunks()
        ]
        scored.sort(key=lambda item: item[0], reverse=True)
        candidates: list[RetrievalCandidate] = []
        for rank, (score, chunk) in enumerate(scored[: top_k or self.config.text_top_k], start=1):
            if score <= 0:
                continue
            candidates.append(
                RetrievalCandidate(
                    chunk_id=chunk.id,
                    body=chunk.body,
                    method=RetrievalMethod.TEXT,
                    query=normalized_query,
                    raw_score=score,
                    raw_rank=rank,
                    source_uri=chunk.source_uri,
                    retrieval_methods=(RetrievalMethod.TEXT,),
                    text_rank=rank,
                )
            )
        return candidates

    def graph_search(self, seed_entities: list[str], working_memory_id: str, top_k: int | None = None) -> list[RetrievalCandidate]:
        if hasattr(self.store, "helix_graph_search"):
            return self.store.helix_graph_search(seed_entities, working_memory_id, top_k or self.config.graph_top_k)  # type: ignore[attr-defined]
        entities = list(dict.fromkeys(seed_entities + self.store.entities_for_memory(working_memory_id)))
        entity_chunks = self.store.chunks_for_entities(entities)
        neighbor_chunks = self.store.neighbor_chunks_for_evidence(working_memory_id)
        conflicted_chunks = self.store.chunks_for_conflicted_notes(working_memory_id)
        chunks = entity_chunks + neighbor_chunks + conflicted_chunks
        seen: set[str] = set()
        candidates: list[RetrievalCandidate] = []
        for chunk in chunks:
            if chunk.id in seen:
                continue
            seen.add(chunk.id)
            reason = (
                "矛盾の可能性がある MemoryNote 経由で発見"
                if chunk in conflicted_chunks
                else "Entity、関係グラフ、または採用済み Evidence の近傍から発見"
            )
            candidates.append(
                RetrievalCandidate(
                    chunk_id=chunk.id,
                    body=chunk.body,
                    method=RetrievalMethod.GRAPH,
                    query="graph:" + ",".join(entities),
                    raw_score=1.0,
                    raw_rank=len(candidates) + 1,
                    source_uri=chunk.source_uri,
                    graph_reason=reason,
                    retrieval_methods=(RetrievalMethod.GRAPH,),
                )
            )
            if len(candidates) >= (top_k or self.config.graph_top_k):
                break
        return candidates

    def merge_candidates(self, candidates: list[RetrievalCandidate]) -> list[RetrievalCandidate]:
        grouped: dict[str, list[RetrievalCandidate]] = defaultdict(list)
        for candidate in candidates:
            grouped[candidate.chunk_id].append(candidate)

        merged: list[RetrievalCandidate] = []
        for chunk_id, group in grouped.items():
            methods = tuple(dict.fromkeys(item.method for item in group))
            best = max(group, key=lambda item: item.raw_score)
            method = RetrievalMethod.HYBRID if len(methods) > 1 else best.method
            query = next((item.query for item in group if item.method == best.method), best.query)
            vector_rank = min(
                (item.vector_rank if item.vector_rank is not None else item.raw_rank for item in group if item.method == RetrievalMethod.VECTOR),
                default=None,
            )
            text_rank = min(
                (item.text_rank if item.text_rank is not None else item.raw_rank for item in group if item.method == RetrievalMethod.TEXT),
                default=None,
            )
            merged.append(
                RetrievalCandidate(
                    chunk_id=chunk_id,
                    body=best.body,
                    method=method,
                    query=query,
                    raw_score=sum(item.raw_score for item in group),
                    raw_rank=min(item.raw_rank for item in group),
                    source_uri=best.source_uri,
                    graph_reason=next((item.graph_reason for item in group if item.graph_reason), None),
                    retrieval_methods=methods,
                    vector_rank=vector_rank,
                    text_rank=text_rank,
                )
            )
        merged.sort(key=lambda item: (len(item.retrieval_methods), item.raw_score), reverse=True)
        return merged
