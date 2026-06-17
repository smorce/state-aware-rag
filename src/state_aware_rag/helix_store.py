from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from state_aware_rag.chunking import Chunker
from state_aware_rag.config import RagConfig
from state_aware_rag.embedding import Embedder
from state_aware_rag.helix import HelixConfig, HelixHttpClient, HelixTypeScriptQueryBuilder, extract_returned_rows
from state_aware_rag.models import Chunk, Entity, Evidence, IngestedDocument, MemoryNote, RetrievalCandidate, RetrievalMethod, RoundLog, WorkingMemory
from state_aware_rag.store import SQLiteRagStore
from state_aware_rag.text import extract_entities


class HelixBackedRagStore(SQLiteRagStore):
    """HelixDB を主検索 backend とし、Python 側の型復元用に SQLite mirror も保持する。"""

    def __init__(
        self,
        path: str | Path,
        config: RagConfig | None = None,
        *,
        helix_config: HelixConfig | None = None,
        query_builder: HelixTypeScriptQueryBuilder | None = None,
        http_client: HelixHttpClient | None = None,
        embedder: Embedder | None = None,
        chunker: Chunker | None = None,
    ) -> None:
        super().__init__(path, config, embedder=embedder, chunker=chunker)
        self.helix = http_client or HelixHttpClient(helix_config)
        self.query_builder = query_builder or HelixTypeScriptQueryBuilder()
        self.ensure_indexes()

    def ensure_indexes(self) -> None:
        expression = (
            'writeBatch()'
            '.varAs("chunk_text", g().createTextIndexNodes("Chunk", "body", null))'
            '.varAs("chunk_vector", g().createVectorIndexNodes("Chunk", "embedding", null))'
            '.returning(["chunk_text", "chunk_vector"])'
        )
        self._query(expression)

    def ingest_document(
        self,
        title: str,
        body: str,
        source_uri: str,
        *,
        metadata: dict[str, Any] | None = None,
        chunk_size: int = 700,
        overlap: int = 80,
        extract_entities: bool = True,
        extractor_backend: str = "rule",
    ) -> IngestedDocument:
        result = super().ingest_document(
            title=title,
            body=body,
            source_uri=source_uri,
            metadata=metadata,
            chunk_size=chunk_size,
            overlap=overlap,
            extract_entities=extract_entities,
            extractor_backend=extractor_backend,
        )
        self._add_document_node(result.document.id, title, source_uri, result.document.created_at, result.document.updated_at, metadata or {})
        for position, chunk in enumerate(result.chunks):
            self._add_chunk_node(chunk, position)
            self._link_nodes("Document", result.document.id, "HAS_CHUNK", "Chunk", chunk.id)
            self._sync_chunk_graph(chunk.id)
        return result

    def create_working_memory(self, question: str) -> WorkingMemory:
        wm = super().create_working_memory(question)
        params = (
            "defineParams({"
            "id:param.string(), question_id:param.string(), original_question:param.string(), "
            "status:param.string(), round_count:param.i64(), created_at:param.string(), updated_at:param.string()"
            "})"
        )
        expression = (
            'writeBatch()'
            '.varAs("question", g().addN("Question", {id:PropertyInput.param("question_id"), body:PropertyInput.param("original_question")}).valueMap(null))'
            '.varAs("wm", g().addN("WorkingMemory", {'
            'id:PropertyInput.param("id"), question_id:PropertyInput.param("question_id"), '
            'original_question:PropertyInput.param("original_question"), status:PropertyInput.param("status"), '
            'round_count:PropertyInput.param("round_count"), created_at:PropertyInput.param("created_at"), '
            'updated_at:PropertyInput.param("updated_at")}).valueMap(null))'
            '.varAs("has_memory", g().n(NodeRef.var("question")).addE("HAS_MEMORY", NodeRef.var("wm")).valueMap(null))'
            '.returning(["question", "wm", "has_memory"])'
        )
        self._query_with_values(
            expression,
            params,
            {
                "id": wm.id,
                "question_id": wm.question_id,
                "original_question": wm.original_question,
                "status": wm.status.value,
                "round_count": wm.round_count,
                "created_at": wm.created_at,
                "updated_at": wm.updated_at,
            },
        )
        return wm

    def create_evidence(self, *args, **kwargs) -> Evidence:
        ev = super().create_evidence(*args, **kwargs)
        self._add_evidence_node(ev)
        self._link_nodes("Evidence", ev.id, "FROM_CHUNK", "Chunk", ev.chunk_id)
        return ev

    def create_memory_note(self, *args, **kwargs) -> MemoryNote:
        note = super().create_memory_note(*args, **kwargs)
        self._add_memory_note_node(note)
        self._link_nodes("WorkingMemory", note.working_memory_id, "HAS_NOTE", "MemoryNote", note.id)
        for evidence in self.evidence_for_note(note.id):
            self._link_nodes("MemoryNote", note.id, "SUPPORTED_BY", "Evidence", evidence.id)
        for entity_name in extract_entities(note.claim):
            entity_id = self.add_entity(entity_name)
            entity = self.get_entity(entity_id)
            self._add_typed_entity_node(entity)
            self._link_nodes("MemoryNote", note.id, "RELATED_TO", entity.entity_type.value, entity.id)
        return note

    def add_conflict(self, note_a_id: str, note_b_id: str, score: float) -> None:
        super().add_conflict(note_a_id, note_b_id, score)
        self._link_nodes("MemoryNote", note_a_id, "CONFLICTS_WITH", "MemoryNote", note_b_id, {"score": score})

    def merge_duplicate_note(self, canonical_note_id: str, evidence_ids: list[str], duplicate_score: float) -> None:
        super().merge_duplicate_note(canonical_note_id, evidence_ids, duplicate_score)
        for evidence_id in evidence_ids:
            self._link_nodes("MemoryNote", canonical_note_id, "SUPPORTED_BY", "Evidence", evidence_id)

    def record_round_log(self, log: RoundLog) -> None:
        super().record_round_log(log)
        self._add_search_round_node(log)
        self._link_nodes("SearchRound", self._round_id(log), "UPDATED", "WorkingMemory", log.working_memory_id)
        for evidence in self.list_evidence(log.working_memory_id)[-log.accepted_evidence_count:]:
            self._link_nodes("SearchRound", self._round_id(log), "RETURNED", "Evidence", evidence.id)

    def helix_vector_search(self, query_text: str, top_k: int) -> list[RetrievalCandidate]:
        params = "defineParams({embedding:param.array(param.f64()), k:param.i64()})"
        expression = (
            'readBatch().varAs("chunks", '
            'g().vectorSearchNodesWith("Chunk", "embedding", params.embedding, params.k, null)'
            '.project([PropertyProjection.new("id"), PropertyProjection.new("body"), '
            'PropertyProjection.new("source_uri"), PropertyProjection.renamed("$distance", "distance")]))'
            '.returning(["chunks"])'
        )
        response = self._query_with_values(
            expression,
            params,
            {"embedding": self.embedder.embed_query(query_text), "k": top_k},
        )
        return self._rows_to_candidates(extract_returned_rows(response, "chunks"), RetrievalMethod.VECTOR)

    def helix_text_search(self, query_text: str, top_k: int) -> list[RetrievalCandidate]:
        params = "defineParams({query:param.string(), k:param.i64()})"
        expression = (
            'readBatch().varAs("chunks", '
            'g().textSearchNodesWith("Chunk", "body", params.query, params.k, null)'
            '.project([PropertyProjection.new("id"), PropertyProjection.new("body"), '
            'PropertyProjection.new("source_uri"), PropertyProjection.renamed("$distance", "distance")]))'
            '.returning(["chunks"])'
        )
        response = self._query_with_values(expression, params, {"query": query_text, "k": top_k})
        return self._rows_to_candidates(extract_returned_rows(response, "chunks"), RetrievalMethod.TEXT)

    def helix_graph_search(self, seed_entities: list[str], working_memory_id: str, top_k: int) -> list[RetrievalCandidate]:
        names = list(dict.fromkeys(seed_entities + self.entities_for_memory(working_memory_id)))
        seed_ids: list[str] = []
        for name in names:
            entity = self.find_entity_by_name(name)
            if entity is not None:
                seed_ids.append(entity.id)
        expanded_ids = self.related_entity_ids(seed_ids, max_hops=2)
        for entity_id in expanded_ids:
            entity = self.get_entity(entity_id)
            names.append(entity.canonical_name)
        entities = list(dict.fromkeys(names))
        candidates: list[RetrievalCandidate] = []
        seen: set[str] = set()
        for entity_name in entities:
            entity = self.find_entity_by_name(entity_name)
            label = entity.entity_type.value if entity is not None else "Other"
            canonical_name = entity.canonical_name if entity is not None else entity_name
            response = self._query_with_values(
                (
                    'readBatch().varAs("chunks", '
                    f'g().nWithLabel("{label}").where(Predicate.eqParam("canonical_name", "entity"))'
                    '.in("MENTIONS")'
                    '.project([PropertyProjection.new("id"), PropertyProjection.new("body"), '
                    'PropertyProjection.new("source_uri")]).limit(params.k))'
                    '.returning(["chunks"])'
                ),
                "defineParams({entity:param.string(), k:param.i64()})",
                {"entity": canonical_name, "k": top_k},
            )
            for candidate in self._rows_to_candidates(extract_returned_rows(response, "chunks"), RetrievalMethod.GRAPH):
                if candidate.chunk_id in seen:
                    continue
                seen.add(candidate.chunk_id)
                candidates.append(candidate)
                if len(candidates) >= top_k:
                    return candidates

        response = self._query_with_values(
            (
                'readBatch().varAs("chunks", '
                'g().nWithLabel("WorkingMemory").where(Predicate.eqParam("id", "working_memory_id"))'
                '.out("HAS_NOTE").out("SUPPORTED_BY").out("FROM_CHUNK")'
                '.project([PropertyProjection.new("id"), PropertyProjection.new("body"), '
                'PropertyProjection.new("source_uri")]).limit(params.k))'
                '.returning(["chunks"])'
            ),
            "defineParams({working_memory_id:param.string(), k:param.i64()})",
            {"working_memory_id": working_memory_id, "k": top_k},
        )
        for candidate in self._rows_to_candidates(extract_returned_rows(response, "chunks"), RetrievalMethod.GRAPH):
            if candidate.chunk_id in seen:
                continue
            candidates.append(candidate)
            if len(candidates) >= top_k:
                break
        return candidates

    def _sync_chunk_graph(self, chunk_id: str) -> None:
        rows = self.conn.execute(
            "SELECT entity_id, surface FROM chunk_entities WHERE chunk_id = ?",
            (chunk_id,),
        ).fetchall()
        for row in rows:
            entity = self.get_entity(str(row["entity_id"]))
            self._add_typed_entity_node(entity)
            self._link_nodes("Chunk", chunk_id, "MENTIONS", entity.entity_type.value, entity.id)
        for relation in self.list_relations_for_chunk(chunk_id):
            from_entity = self.get_entity(relation.from_entity_id)
            to_entity = self.get_entity(relation.to_entity_id)
            self._add_typed_entity_node(from_entity)
            self._add_typed_entity_node(to_entity)
            self._link_nodes(
                from_entity.entity_type.value,
                from_entity.id,
                relation.relation_type,
                to_entity.entity_type.value,
                to_entity.id,
            )

    def _add_document_node(self, doc_id: str, title: str, source_uri: str, created_at: str, updated_at: str, metadata: dict[str, Any]) -> None:
        params = (
            "defineParams({id:param.string(), title:param.string(), source_uri:param.string(), "
            "created_at:param.string(), updated_at:param.string(), metadata:param.string()})"
        )
        expression = (
            'writeBatch().varAs("doc", g().addN("Document", {'
            'id:PropertyInput.param("id"), title:PropertyInput.param("title"), source_uri:PropertyInput.param("source_uri"), '
            'created_at:PropertyInput.param("created_at"), updated_at:PropertyInput.param("updated_at"), '
            'metadata:PropertyInput.param("metadata")}).valueMap(null)).returning(["doc"])'
        )
        self._query_with_values(
            expression,
            params,
            {
                "id": doc_id,
                "title": title,
                "source_uri": source_uri,
                "created_at": created_at,
                "updated_at": updated_at,
                "metadata": json.dumps(metadata, ensure_ascii=False),
            },
        )

    def _add_chunk_node(self, chunk: Chunk, position: int) -> None:
        params = (
            "defineParams({id:param.string(), document_id:param.string(), body:param.string(), "
            "embedding:param.array(param.f64()), token_count:param.i64(), section_title:param.string(), "
            "source_uri:param.string(), position:param.i64(), metadata:param.string()})"
        )
        expression = (
            'writeBatch().varAs("chunk", g().addN("Chunk", {'
            'id:PropertyInput.param("id"), document_id:PropertyInput.param("document_id"), '
            'body:PropertyInput.param("body"), embedding:PropertyInput.param("embedding"), '
            'token_count:PropertyInput.param("token_count"), section_title:PropertyInput.param("section_title"), '
            'source_uri:PropertyInput.param("source_uri"), position:PropertyInput.param("position"), '
            'metadata:PropertyInput.param("metadata")}).valueMap(null)).returning(["chunk"])'
        )
        self._query_with_values(
            expression,
            params,
            {
                "id": chunk.id,
                "document_id": chunk.document_id,
                "body": chunk.body,
                "embedding": chunk.embedding,
                "token_count": chunk.token_count,
                "section_title": chunk.section_title or "",
                "source_uri": chunk.source_uri,
                "position": position,
                "metadata": json.dumps(chunk.metadata, ensure_ascii=False),
            },
        )

    def _add_typed_entity_node(self, entity: Entity) -> None:
        params = (
            "defineParams({id:param.string(), canonical_name:param.string(), "
            "normalized_name:param.string(), entity_type:param.string()})"
        )
        expression = (
            f'writeBatch().varAs("entity", g().addN("{entity.entity_type.value}", {{'
            'id:PropertyInput.param("id"), canonical_name:PropertyInput.param("canonical_name"), '
            'normalized_name:PropertyInput.param("normalized_name"), '
            'entity_type:PropertyInput.param("entity_type")}).valueMap(null)).returning(["entity"])'
        )
        self._query_with_values(
            expression,
            params,
            {
                "id": entity.id,
                "canonical_name": entity.canonical_name,
                "normalized_name": entity.normalized_name,
                "entity_type": entity.entity_type.value,
            },
        )

    def _add_evidence_node(self, ev: Evidence) -> None:
        params = (
            "defineParams({id:param.string(), chunk_id:param.string(), round:param.i64(), query:param.string(), "
            "body_excerpt:param.string(), retrieval_method:param.string(), raw_rank:param.i64(), "
            "relevance_score:param.f64(), memory_value_score:param.f64(), accepted:param.bool(), source_uri:param.string()})"
        )
        expression = (
            'writeBatch().varAs("evidence", g().addN("Evidence", {'
            'id:PropertyInput.param("id"), chunk_id:PropertyInput.param("chunk_id"), round:PropertyInput.param("round"), '
            'query:PropertyInput.param("query"), body_excerpt:PropertyInput.param("body_excerpt"), '
            'retrieval_method:PropertyInput.param("retrieval_method"), raw_rank:PropertyInput.param("raw_rank"), '
            'relevance_score:PropertyInput.param("relevance_score"), memory_value_score:PropertyInput.param("memory_value_score"), '
            'accepted:PropertyInput.param("accepted"), source_uri:PropertyInput.param("source_uri")}).valueMap(null))'
            '.returning(["evidence"])'
        )
        self._query_with_values(
            expression,
            params,
            {
                "id": ev.id,
                "chunk_id": ev.chunk_id,
                "round": ev.round,
                "query": ev.query,
                "body_excerpt": ev.body_excerpt,
                "retrieval_method": ev.retrieval_method.value,
                "raw_rank": ev.raw_rank,
                "relevance_score": ev.relevance_score,
                "memory_value_score": ev.memory_value_score,
                "accepted": ev.accepted,
                "source_uri": ev.source_uri,
            },
        )

    def _add_memory_note_node(self, note: MemoryNote) -> None:
        params = (
            "defineParams({id:param.string(), working_memory_id:param.string(), claim:param.string(), "
            "normalized_claim:param.string(), note_type:param.string(), confidence:param.f64(), "
            "source_count:param.i64(), embedding:param.array(param.f64()), status:param.string()})"
        )
        expression = (
            'writeBatch().varAs("note", g().addN("MemoryNote", {'
            'id:PropertyInput.param("id"), working_memory_id:PropertyInput.param("working_memory_id"), '
            'claim:PropertyInput.param("claim"), normalized_claim:PropertyInput.param("normalized_claim"), '
            'note_type:PropertyInput.param("note_type"), confidence:PropertyInput.param("confidence"), '
            'source_count:PropertyInput.param("source_count"), embedding:PropertyInput.param("embedding"), '
            'status:PropertyInput.param("status")}).valueMap(null)).returning(["note"])'
        )
        self._query_with_values(
            expression,
            params,
            {
                "id": note.id,
                "working_memory_id": note.working_memory_id,
                "claim": note.claim,
                "normalized_claim": note.normalized_claim,
                "note_type": note.note_type.value,
                "confidence": note.confidence,
                "source_count": note.source_count,
                "embedding": note.embedding,
                "status": note.status.value,
            },
        )

    def _add_search_round_node(self, log: RoundLog) -> None:
        params = (
            "defineParams({id:param.string(), working_memory_id:param.string(), round:param.i64(), "
            "candidate_count:param.i64(), accepted_evidence_count:param.i64(), created_note_count:param.i64(), "
            "accepted_note_count:param.i64(), duplicate_count:param.i64(), conflict_count:param.i64(), "
            "gain:param.f64(), stop_reason:param.string(), actions:param.string()})"
        )
        expression = (
            'writeBatch().varAs("round", g().addN("SearchRound", {'
            'id:PropertyInput.param("id"), working_memory_id:PropertyInput.param("working_memory_id"), '
            'round:PropertyInput.param("round"), candidate_count:PropertyInput.param("candidate_count"), '
            'accepted_evidence_count:PropertyInput.param("accepted_evidence_count"), '
            'created_note_count:PropertyInput.param("created_note_count"), accepted_note_count:PropertyInput.param("accepted_note_count"), '
            'duplicate_count:PropertyInput.param("duplicate_count"), conflict_count:PropertyInput.param("conflict_count"), '
            'gain:PropertyInput.param("gain"), stop_reason:PropertyInput.param("stop_reason"), '
            'actions:PropertyInput.param("actions")}).valueMap(null)).returning(["round"])'
        )
        self._query_with_values(
            expression,
            params,
            {
                "id": self._round_id(log),
                "working_memory_id": log.working_memory_id,
                "round": log.round,
                "candidate_count": log.candidate_count,
                "accepted_evidence_count": log.accepted_evidence_count,
                "created_note_count": log.created_note_count,
                "accepted_note_count": log.accepted_note_count,
                "duplicate_count": log.duplicate_count,
                "conflict_count": log.conflict_count,
                "gain": log.gain,
                "stop_reason": log.stop_reason or "",
                "actions": json.dumps(log.actions, ensure_ascii=False),
            },
        )

    def _round_id(self, log: RoundLog) -> str:
        return f"round_{log.working_memory_id}_{log.round}"

    def _link_nodes(
        self,
        from_label: str,
        from_id: str,
        edge_label: str,
        to_label: str,
        to_id: str,
        properties: dict[str, Any] | None = None,
    ) -> None:
        params = (
            "defineParams({from_id:param.string(), to_id:param.string(), properties:param.object()})"
        )
        props = "{score:PropertyInput.param(\"score\")}" if properties and "score" in properties else "{}"
        values = {"from_id": from_id, "to_id": to_id, "properties": properties or {}}
        if properties and "score" in properties:
            params = "defineParams({from_id:param.string(), to_id:param.string(), score:param.f64()})"
            values = {"from_id": from_id, "to_id": to_id, "score": float(properties["score"])}
        expression = (
            'writeBatch()'
            f'.varAs("from_node", g().nWithLabel("{from_label}").where(Predicate.eqParam("id", "from_id")))'
            f'.varAs("to_node", g().nWithLabel("{to_label}").where(Predicate.eqParam("id", "to_id")))'
            f'.varAs("edge", g().n(NodeRef.var("from_node")).addE("{edge_label}", NodeRef.var("to_node"), {props}).valueMap(null))'
            '.returning(["edge"])'
        )
        self._query_with_values(expression, params, values)

    def _rows_to_candidates(self, rows: list[dict[str, Any]], method: RetrievalMethod) -> list[RetrievalCandidate]:
        candidates: list[RetrievalCandidate] = []
        for rank, row in enumerate(rows, start=1):
            chunk_id = str(row.get("id") or row.get("properties", {}).get("id") or "")
            body = str(row.get("body") or row.get("properties", {}).get("body") or "")
            source_uri = str(row.get("source_uri") or row.get("properties", {}).get("source_uri") or "")
            distance = row.get("distance", row.get("$distance", 0.0))
            try:
                raw_score = 1.0 / (1.0 + float(distance))
            except (TypeError, ValueError):
                raw_score = 1.0
            if not chunk_id or not body:
                continue
            candidates.append(
                RetrievalCandidate(
                    chunk_id=chunk_id,
                    body=body,
                    method=method,
                    raw_score=raw_score,
                    raw_rank=rank,
                    source_uri=source_uri,
                    retrieval_methods=(method,),
                )
            )
        return candidates

    def _query(self, expression: str) -> dict[str, Any]:
        return self.helix.query(self.query_builder.build(expression))

    def _query_with_values(self, expression: str, params_source: str, values: dict[str, Any]) -> dict[str, Any]:
        return self.helix.query(self.query_builder.build_with_values(expression, params_source, values))
