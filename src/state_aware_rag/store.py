from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from state_aware_rag.chunking import Chunker, build_chunker
from state_aware_rag.config import RagConfig
from state_aware_rag.embedding import Embedder, build_embedder
from state_aware_rag.extraction import EntityResolver, build_entity_extractor, find_entity_seed_match, normalize_entity_name
from state_aware_rag.models import (
    Chunk,
    Document,
    Entity,
    EntityType,
    Evidence,
    ExtractionResult,
    IngestedDocument,
    MemoryNote,
    NoteStatus,
    NoteType,
    Relation,
    RetrievalMethod,
    RoundLog,
    WorkingMemory,
    WorkingMemoryStatus,
    new_id,
    now_iso,
)
from state_aware_rag.text import extract_entities, normalize_claim

SCHEMA_VERSION = 2


class SQLiteRagStore:
    def __init__(
        self,
        path: str | Path,
        config: RagConfig | None = None,
        *,
        embedder: Embedder | None = None,
        chunker: Chunker | None = None,
    ) -> None:
        self.path = Path(path)
        self.config = config or RagConfig()
        self.embedder = embedder or build_embedder(
            self.config.embedding_backend, dimensions=self.config.embedding_dimensions
        )
        self.chunker = chunker or build_chunker(self.config.chunker_backend)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._migrate()

    def close(self) -> None:
        self.conn.close()

    def _migrate(self) -> None:
        self.conn.executescript(
            """
            PRAGMA foreign_keys = ON;

            CREATE TABLE IF NOT EXISTS schema_version (
              version INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS documents (
              id TEXT PRIMARY KEY,
              title TEXT NOT NULL,
              source_uri TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              metadata TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chunks (
              id TEXT PRIMARY KEY,
              document_id TEXT NOT NULL,
              body TEXT NOT NULL,
              embedding TEXT NOT NULL,
              token_count INTEGER NOT NULL,
              section_title TEXT,
              source_uri TEXT NOT NULL,
              position INTEGER NOT NULL,
              metadata TEXT NOT NULL,
              FOREIGN KEY(document_id) REFERENCES documents(id)
            );

            CREATE TABLE IF NOT EXISTS working_memories (
              id TEXT PRIMARY KEY,
              question_id TEXT NOT NULL,
              original_question TEXT NOT NULL,
              status TEXT NOT NULL,
              round_count INTEGER NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS memory_notes (
              id TEXT PRIMARY KEY,
              working_memory_id TEXT NOT NULL,
              claim TEXT NOT NULL,
              normalized_claim TEXT NOT NULL,
              note_type TEXT NOT NULL,
              support_score REAL NOT NULL,
              relevance_score REAL NOT NULL,
              novelty_score REAL NOT NULL,
              conflict_score REAL NOT NULL,
              confidence REAL NOT NULL,
              source_count INTEGER NOT NULL,
              embedding TEXT NOT NULL,
              created_round INTEGER NOT NULL,
              last_updated_round INTEGER NOT NULL,
              status TEXT NOT NULL,
              FOREIGN KEY(working_memory_id) REFERENCES working_memories(id)
            );

            CREATE TABLE IF NOT EXISTS evidence (
              id TEXT PRIMARY KEY,
              chunk_id TEXT NOT NULL,
              working_memory_id TEXT NOT NULL,
              round INTEGER NOT NULL,
              query TEXT NOT NULL,
              body_excerpt TEXT NOT NULL,
              retrieval_method TEXT NOT NULL,
              raw_rank INTEGER NOT NULL,
              relevance_score REAL NOT NULL,
              memory_value_score REAL NOT NULL,
              accepted INTEGER NOT NULL,
              source_uri TEXT NOT NULL,
              FOREIGN KEY(chunk_id) REFERENCES chunks(id),
              FOREIGN KEY(working_memory_id) REFERENCES working_memories(id)
            );

            CREATE TABLE IF NOT EXISTS note_evidence (
              note_id TEXT NOT NULL,
              evidence_id TEXT NOT NULL,
              PRIMARY KEY(note_id, evidence_id)
            );

            CREATE TABLE IF NOT EXISTS conflicts (
              note_a_id TEXT NOT NULL,
              note_b_id TEXT NOT NULL,
              score REAL NOT NULL,
              PRIMARY KEY(note_a_id, note_b_id)
            );

            CREATE TABLE IF NOT EXISTS duplicate_edges (
              duplicate_note_id TEXT NOT NULL,
              canonical_note_id TEXT NOT NULL,
              score REAL NOT NULL,
              PRIMARY KEY(duplicate_note_id, canonical_note_id)
            );

            CREATE TABLE IF NOT EXISTS open_questions (
              working_memory_id TEXT NOT NULL,
              question TEXT NOT NULL,
              reason TEXT NOT NULL,
              resolved INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY(working_memory_id, question)
            );

            CREATE TABLE IF NOT EXISTS round_logs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              working_memory_id TEXT NOT NULL,
              round INTEGER NOT NULL,
              payload TEXT NOT NULL
            );
            """
        )
        version_row = self.conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        current_version = int(version_row["version"]) if version_row else 0
        if current_version < SCHEMA_VERSION:
            self._migrate_entities_schema()
            self.conn.execute("DELETE FROM schema_version")
            self.conn.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,))
        self.conn.commit()

    def _migrate_entities_schema(self) -> None:
        self.conn.executescript(
            """
            DROP TABLE IF EXISTS chunk_entities;
            DROP TABLE IF EXISTS note_entities;
            DROP TABLE IF EXISTS entity_aliases;
            DROP TABLE IF EXISTS relations;
            DROP TABLE IF EXISTS entities;

            CREATE TABLE entities (
              id TEXT PRIMARY KEY,
              entity_type TEXT NOT NULL,
              canonical_name TEXT NOT NULL,
              normalized_name TEXT NOT NULL,
              embedding TEXT NOT NULL,
              attributes TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE INDEX idx_entities_type_norm ON entities(entity_type, normalized_name);

            CREATE TABLE entity_aliases (
              entity_id TEXT NOT NULL,
              alias TEXT NOT NULL,
              normalized_alias TEXT NOT NULL,
              source_chunk_id TEXT,
              PRIMARY KEY(entity_id, normalized_alias),
              FOREIGN KEY(entity_id) REFERENCES entities(id)
            );
            CREATE INDEX idx_aliases_norm ON entity_aliases(normalized_alias);

            CREATE TABLE chunk_entities (
              chunk_id TEXT NOT NULL,
              entity_id TEXT NOT NULL,
              surface TEXT NOT NULL,
              PRIMARY KEY(chunk_id, entity_id, surface),
              FOREIGN KEY(chunk_id) REFERENCES chunks(id),
              FOREIGN KEY(entity_id) REFERENCES entities(id)
            );

            CREATE TABLE note_entities (
              note_id TEXT NOT NULL,
              entity_id TEXT NOT NULL,
              PRIMARY KEY(note_id, entity_id),
              FOREIGN KEY(entity_id) REFERENCES entities(id)
            );

            CREATE TABLE relations (
              id TEXT PRIMARY KEY,
              from_entity_id TEXT NOT NULL,
              to_entity_id TEXT NOT NULL,
              relation_type TEXT NOT NULL,
              source_chunk_id TEXT NOT NULL,
              confidence REAL NOT NULL,
              evidence_text TEXT NOT NULL,
              attributes TEXT NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY(from_entity_id) REFERENCES entities(id),
              FOREIGN KEY(to_entity_id) REFERENCES entities(id),
              FOREIGN KEY(source_chunk_id) REFERENCES chunks(id)
            );
            CREATE INDEX idx_relations_from ON relations(from_entity_id);
            CREATE INDEX idx_relations_to ON relations(to_entity_id);
            CREATE INDEX idx_relations_type ON relations(relation_type);
            """
        )

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
        doc = Document(
            id=new_id("doc"),
            title=title,
            source_uri=source_uri,
            created_at=now_iso(),
            updated_at=now_iso(),
            metadata=metadata or {},
        )
        self.conn.execute(
            "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?)",
            (doc.id, doc.title, doc.source_uri, doc.created_at, doc.updated_at, json.dumps(doc.metadata)),
        )
        chunk_bodies = self._chunk_text(body, chunk_size, overlap)
        chunk_embeddings = self.embedder.embed_documents(chunk_bodies)
        extractor = build_entity_extractor(extractor_backend, self.embedder) if extract_entities else None
        resolver = EntityResolver(self, self.embedder) if extract_entities else None
        chunks: list[Chunk] = []
        for position, (chunk_body, chunk_embedding) in enumerate(zip(chunk_bodies, chunk_embeddings)):
            chunk = Chunk(
                id=new_id("chunk"),
                document_id=doc.id,
                body=chunk_body,
                embedding=chunk_embedding,
                token_count=len(chunk_body.split()),
                position=position,
                section_title=title,
                source_uri=source_uri,
                metadata={"extraction_status": "pending" if extract_entities else "skipped"},
            )
            self.conn.execute(
                "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    chunk.id,
                    chunk.document_id,
                    chunk.body,
                    json.dumps(chunk.embedding),
                    chunk.token_count,
                    chunk.section_title,
                    chunk.source_uri,
                    chunk.position,
                    json.dumps(chunk.metadata),
                ),
            )
            if extractor is not None and resolver is not None:
                self._apply_extraction(chunk, extractor.extract(chunk), resolver)
            chunks.append(chunk)
        self.conn.commit()
        return IngestedDocument(document=doc, chunks=chunks)

    def extract_chunks(
        self,
        chunk_ids: list[str] | None = None,
        *,
        extractor_backend: str = "rule",
    ) -> int:
        extractor = build_entity_extractor(extractor_backend, self.embedder)
        resolver = EntityResolver(self, self.embedder)
        if chunk_ids is None:
            rows = self.conn.execute("SELECT id FROM chunks ORDER BY rowid").fetchall()
            chunk_ids = [str(row["id"]) for row in rows]
        processed = 0
        for chunk_id in chunk_ids:
            chunk = self.get_chunk(chunk_id)
            self.conn.execute("DELETE FROM chunk_entities WHERE chunk_id = ?", (chunk_id,))
            self.conn.execute("DELETE FROM relations WHERE source_chunk_id = ?", (chunk_id,))
            self._apply_extraction(chunk, extractor.extract(chunk), resolver)
            processed += 1
        self.conn.commit()
        return processed

    def _apply_extraction(self, chunk: Chunk, result: ExtractionResult, resolver: EntityResolver) -> None:
        try:
            entity_id_map: dict[str, str] = {}
            for entity in result.entities:
                resolved = resolver.resolve(entity, surface=entity.canonical_name, source_chunk_id=chunk.id)
                entity_id_map[entity.id] = resolved.id
                self.link_chunk_entity(chunk.id, resolved.id, surface=entity.canonical_name)
            for relation in result.relations:
                from_id = entity_id_map.get(relation.from_entity_id)
                to_id = entity_id_map.get(relation.to_entity_id)
                if not from_id or not to_id:
                    continue
                self.save_relation(
                    Relation(
                        id=relation.id,
                        from_entity_id=from_id,
                        to_entity_id=to_id,
                        relation_type=relation.relation_type,
                        source_chunk_id=chunk.id,
                        confidence=relation.confidence,
                        evidence_text=relation.evidence_text,
                        attributes=relation.attributes,
                        created_at=relation.created_at,
                    )
                )
            status = "ok" if result.entities or result.relations else "empty"
            self._set_chunk_metadata(chunk.id, {"extraction_status": status})
        except Exception:
            self._set_chunk_metadata(chunk.id, {"extraction_status": "failed"})

    def _set_chunk_metadata(self, chunk_id: str, updates: dict[str, Any]) -> None:
        row = self.conn.execute("SELECT metadata FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
        if row is None:
            return
        metadata = json.loads(str(row["metadata"]))
        metadata.update(updates)
        self.conn.execute("UPDATE chunks SET metadata = ? WHERE id = ?", (json.dumps(metadata), chunk_id))

    def _chunk_text(self, body: str, chunk_size: int, overlap: int) -> list[str]:
        return self.chunker.chunk(body, chunk_size, overlap)

    def list_chunks(self) -> list[Chunk]:
        return [self._row_to_chunk(row) for row in self.conn.execute("SELECT * FROM chunks ORDER BY rowid")]

    def get_chunk(self, chunk_id: str) -> Chunk:
        row = self.conn.execute("SELECT * FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
        if row is None:
            raise KeyError(f"Chunk not found: {chunk_id}")
        return self._row_to_chunk(row)

    def create_working_memory(self, question: str) -> WorkingMemory:
        wm = WorkingMemory(
            id=new_id("wm"),
            question_id=new_id("q"),
            original_question=question,
            status=WorkingMemoryStatus.RUNNING,
            round_count=0,
            created_at=now_iso(),
            updated_at=now_iso(),
        )
        self.conn.execute(
            "INSERT INTO working_memories VALUES (?, ?, ?, ?, ?, ?, ?)",
            (wm.id, wm.question_id, wm.original_question, wm.status.value, wm.round_count, wm.created_at, wm.updated_at),
        )
        self.conn.commit()
        return wm

    def get_working_memory(self, working_memory_id: str) -> WorkingMemory:
        row = self.conn.execute("SELECT * FROM working_memories WHERE id = ?", (working_memory_id,)).fetchone()
        if row is None:
            raise KeyError(f"Working memory not found: {working_memory_id}")
        return self._row_to_wm(row)

    def update_working_memory(self, working_memory_id: str, *, status: WorkingMemoryStatus | None = None, round_count: int | None = None) -> WorkingMemory:
        current = self.get_working_memory(working_memory_id)
        next_status = status or current.status
        next_round = current.round_count if round_count is None else round_count
        self.conn.execute(
            "UPDATE working_memories SET status = ?, round_count = ?, updated_at = ? WHERE id = ?",
            (next_status.value, next_round, now_iso(), working_memory_id),
        )
        self.conn.commit()
        return self.get_working_memory(working_memory_id)

    def create_evidence(
        self,
        working_memory_id: str,
        chunk_id: str,
        *,
        round_number: int,
        query: str,
        body_excerpt: str,
        retrieval_method: RetrievalMethod,
        raw_rank: int,
        relevance_score: float,
        memory_value_score: float,
        accepted: bool,
        source_uri: str,
    ) -> Evidence:
        ev = Evidence(
            id=new_id("ev"),
            chunk_id=chunk_id,
            working_memory_id=working_memory_id,
            round=round_number,
            query=query,
            body_excerpt=body_excerpt,
            retrieval_method=retrieval_method,
            raw_rank=raw_rank,
            relevance_score=relevance_score,
            memory_value_score=memory_value_score,
            accepted=accepted,
            source_uri=source_uri,
        )
        self.conn.execute(
            "INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ev.id,
                ev.chunk_id,
                working_memory_id,
                ev.round,
                ev.query,
                ev.body_excerpt,
                ev.retrieval_method.value,
                ev.raw_rank,
                ev.relevance_score,
                ev.memory_value_score,
                1 if ev.accepted else 0,
                ev.source_uri,
            ),
        )
        self.conn.commit()
        return ev

    def list_evidence(self, working_memory_id: str) -> list[Evidence]:
        rows = self.conn.execute("SELECT * FROM evidence WHERE working_memory_id = ? ORDER BY rowid", (working_memory_id,))
        return [self._row_to_evidence(row) for row in rows]

    def get_evidence(self, evidence_id: str) -> Evidence:
        row = self.conn.execute("SELECT * FROM evidence WHERE id = ?", (evidence_id,)).fetchone()
        if row is None:
            raise KeyError(f"Evidence not found: {evidence_id}")
        return self._row_to_evidence(row)

    def create_memory_note(
        self,
        working_memory_id: str,
        claim: str,
        note_type: str | NoteType,
        confidence: float,
        evidence_ids: list[str],
        created_round: int,
        *,
        support_score: float | None = None,
        relevance_score: float | None = None,
        novelty_score: float = 1.0,
        conflict_score: float = 0.0,
        status: NoteStatus = NoteStatus.ACTIVE,
    ) -> MemoryNote:
        normalized = normalize_claim(claim)
        note = MemoryNote(
            id=new_id("note"),
            working_memory_id=working_memory_id,
            claim=claim,
            normalized_claim=normalized,
            note_type=NoteType(note_type),
            support_score=confidence if support_score is None else support_score,
            relevance_score=confidence if relevance_score is None else relevance_score,
            novelty_score=novelty_score,
            conflict_score=conflict_score,
            confidence=confidence,
            source_count=max(1, len(set(evidence_ids))) if evidence_ids else 1,
            embedding=self.embedder.embed_claim(claim),
            created_round=created_round,
            last_updated_round=created_round,
            status=status,
        )
        self.conn.execute(
            "INSERT INTO memory_notes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                note.id,
                note.working_memory_id,
                note.claim,
                note.normalized_claim,
                note.note_type.value,
                note.support_score,
                note.relevance_score,
                note.novelty_score,
                note.conflict_score,
                note.confidence,
                note.source_count,
                json.dumps(note.embedding),
                note.created_round,
                note.last_updated_round,
                note.status.value,
            ),
        )
        for evidence_id in evidence_ids:
            self.link_note_evidence(note.id, evidence_id)
        for entity_name in extract_entities(note.claim):
            entity_id = self.add_entity(entity_name)
            self.link_note_entity(note.id, entity_id)
        self.conn.commit()
        return note

    def list_memory_notes(self, working_memory_id: str, *, active_only: bool = True) -> list[MemoryNote]:
        if active_only:
            rows = self.conn.execute(
                "SELECT * FROM memory_notes WHERE working_memory_id = ? AND status = ? ORDER BY rowid",
                (working_memory_id, NoteStatus.ACTIVE.value),
            )
        else:
            rows = self.conn.execute("SELECT * FROM memory_notes WHERE working_memory_id = ? ORDER BY rowid", (working_memory_id,))
        return [self._row_to_note(row) for row in rows]

    def merge_duplicate_note(self, canonical_note_id: str, evidence_ids: list[str], duplicate_score: float) -> None:
        for evidence_id in evidence_ids:
            self.link_note_evidence(canonical_note_id, evidence_id)
        self.conn.execute(
            """
            UPDATE memory_notes
            SET source_count = (
                SELECT COUNT(*) FROM note_evidence WHERE note_id = ?
            ), last_updated_round = last_updated_round + 1
            WHERE id = ?
            """,
            (canonical_note_id, canonical_note_id),
        )
        self.conn.commit()

    def add_duplicate_edge(self, duplicate_note_id: str, canonical_note_id: str, score: float) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO duplicate_edges VALUES (?, ?, ?)",
            (duplicate_note_id, canonical_note_id, score),
        )
        self.conn.commit()

    def add_conflict(self, note_a_id: str, note_b_id: str, score: float) -> None:
        left, right = sorted([note_a_id, note_b_id])
        self.conn.execute("INSERT OR IGNORE INTO conflicts VALUES (?, ?, ?)", (left, right, score))
        self.conn.commit()

    def list_conflicts(self, working_memory_id: str) -> list[tuple[MemoryNote, MemoryNote, float]]:
        rows = self.conn.execute(
            """
            SELECT c.note_a_id, c.note_b_id, c.score
            FROM conflicts c
            JOIN memory_notes a ON a.id = c.note_a_id
            WHERE a.working_memory_id = ?
            """,
            (working_memory_id,),
        ).fetchall()
        return [(self.get_note(row["note_a_id"]), self.get_note(row["note_b_id"]), float(row["score"])) for row in rows]

    def get_note(self, note_id: str) -> MemoryNote:
        row = self.conn.execute("SELECT * FROM memory_notes WHERE id = ?", (note_id,)).fetchone()
        if row is None:
            raise KeyError(f"Memory note not found: {note_id}")
        return self._row_to_note(row)

    def link_note_evidence(self, note_id: str, evidence_id: str) -> None:
        self.conn.execute("INSERT OR IGNORE INTO note_evidence VALUES (?, ?)", (note_id, evidence_id))

    def evidence_for_note(self, note_id: str) -> list[Evidence]:
        rows = self.conn.execute(
            """
            SELECT e.* FROM evidence e
            JOIN note_evidence ne ON ne.evidence_id = e.id
            WHERE ne.note_id = ?
            ORDER BY e.rowid
            """,
            (note_id,),
        )
        return [self._row_to_evidence(row) for row in rows]

    def insert_entity(self, entity: Entity, *, source_chunk_id: str | None = None) -> Entity:
        self.conn.execute(
            """
            INSERT INTO entities (id, entity_type, canonical_name, normalized_name, embedding, attributes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entity.id,
                entity.entity_type.value,
                entity.canonical_name,
                entity.normalized_name,
                json.dumps(entity.embedding),
                json.dumps(entity.attributes, ensure_ascii=False),
                entity.created_at,
                entity.updated_at,
            ),
        )
        for alias in entity.aliases:
            self._insert_alias(entity.id, alias, source_chunk_id)
        self.conn.commit()
        return self.get_entity(entity.id)

    def add_entity(self, name: str, entity_type: EntityType = EntityType.OTHER) -> str:
        normalized = normalize_entity_name(name)
        existing = self.find_entity_by_type_and_normalized(entity_type, normalized)
        if existing is not None:
            return existing.id
        by_alias = self.find_entity_by_alias(normalized)
        if by_alias is not None:
            return by_alias.id
        timestamp = now_iso()
        entity = Entity(
            id=new_id("ent"),
            entity_type=entity_type,
            canonical_name=name,
            normalized_name=normalized,
            aliases=(),
            embedding=self.embedder.embed_claim(name),
            attributes={},
            created_at=timestamp,
            updated_at=timestamp,
        )
        return self.insert_entity(entity).id

    def add_entity_alias(self, entity_id: str, alias: str, source_chunk_id: str | None = None) -> Entity:
        self._insert_alias(entity_id, alias, source_chunk_id)
        self.conn.execute("UPDATE entities SET updated_at = ? WHERE id = ?", (now_iso(), entity_id))
        self.conn.commit()
        return self.get_entity(entity_id)

    def _insert_alias(self, entity_id: str, alias: str, source_chunk_id: str | None) -> None:
        normalized_alias = normalize_entity_name(alias)
        if not normalized_alias:
            return
        self.conn.execute(
            """
            INSERT OR IGNORE INTO entity_aliases (entity_id, alias, normalized_alias, source_chunk_id)
            VALUES (?, ?, ?, ?)
            """,
            (entity_id, alias, normalized_alias, source_chunk_id),
        )

    def update_entity_type(self, entity_id: str, entity_type: EntityType) -> Entity:
        self.conn.execute(
            "UPDATE entities SET entity_type = ?, updated_at = ? WHERE id = ?",
            (entity_type.value, now_iso(), entity_id),
        )
        self.conn.commit()
        return self.get_entity(entity_id)

    def get_entity(self, entity_id: str) -> Entity:
        row = self.conn.execute("SELECT * FROM entities WHERE id = ?", (entity_id,)).fetchone()
        if row is None:
            raise KeyError(f"Entity not found: {entity_id}")
        return self._row_to_entity(row)

    def find_entity_by_type_and_normalized(self, entity_type: EntityType, normalized_name: str) -> Entity | None:
        row = self.conn.execute(
            "SELECT * FROM entities WHERE entity_type = ? AND normalized_name = ? LIMIT 1",
            (entity_type.value, normalized_name),
        ).fetchone()
        return self._row_to_entity(row) if row else None

    def find_entity_by_alias(self, normalized_alias: str) -> Entity | None:
        row = self.conn.execute(
            """
            SELECT e.* FROM entities e
            JOIN entity_aliases a ON a.entity_id = e.id
            WHERE a.normalized_alias = ?
            LIMIT 1
            """,
            (normalized_alias,),
        ).fetchone()
        return self._row_to_entity(row) if row else None

    def find_entity_by_name(self, name: str) -> Entity | None:
        normalized = normalize_entity_name(name)
        row = self.conn.execute(
            "SELECT * FROM entities WHERE normalized_name = ? OR canonical_name = ? LIMIT 1",
            (normalized, name),
        ).fetchone()
        if row:
            return self._row_to_entity(row)
        return self.find_entity_by_alias(normalized)

    def find_entity_seed(self, name: str) -> Entity | None:
        """グラフ検索 seed 用: 完全一致のあと部分一致・名寄せ・embedding で既存 Entity を探す。"""
        seed = name.strip()
        if not seed:
            return None
        exact = self.find_entity_by_name(seed)
        if exact is not None:
            return exact
        return find_entity_seed_match(seed, self.list_entities(), self.embedder)

    def resolve_entity_ref(self, entity_ref: str) -> str:
        if self.conn.execute("SELECT 1 FROM entities WHERE id = ?", (entity_ref,)).fetchone():
            return entity_ref
        entity = self.find_entity_by_name(entity_ref)
        if entity is None:
            return self.add_entity(entity_ref)
        return entity.id

    def list_entities(self) -> list[Entity]:
        return [self._row_to_entity(row) for row in self.conn.execute("SELECT * FROM entities ORDER BY created_at, rowid")]

    def list_entities_by_type(self, entity_type: EntityType) -> list[Entity]:
        rows = self.conn.execute(
            "SELECT * FROM entities WHERE entity_type = ? ORDER BY created_at, rowid",
            (entity_type.value,),
        )
        return [self._row_to_entity(row) for row in rows]

    def save_relation(self, relation: Relation) -> Relation:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO relations
            (id, from_entity_id, to_entity_id, relation_type, source_chunk_id, confidence, evidence_text, attributes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                relation.id,
                relation.from_entity_id,
                relation.to_entity_id,
                relation.relation_type,
                relation.source_chunk_id,
                relation.confidence,
                relation.evidence_text,
                json.dumps(relation.attributes, ensure_ascii=False),
                relation.created_at,
            ),
        )
        self.conn.commit()
        return relation

    def get_relation(self, relation_id: str) -> Relation:
        row = self.conn.execute("SELECT * FROM relations WHERE id = ?", (relation_id,)).fetchone()
        if row is None:
            raise KeyError(f"Relation not found: {relation_id}")
        return self._row_to_relation(row)

    def list_relations_for_chunk(self, chunk_id: str) -> list[Relation]:
        rows = self.conn.execute("SELECT * FROM relations WHERE source_chunk_id = ? ORDER BY rowid", (chunk_id,))
        return [self._row_to_relation(row) for row in rows]

    def related_entity_ids(self, seed_entity_ids: list[str], *, max_hops: int = 2) -> set[str]:
        frontier = set(seed_entity_ids)
        visited = set(seed_entity_ids)
        for _ in range(max_hops):
            if not frontier:
                break
            placeholders = ",".join("?" for _ in frontier)
            rows = self.conn.execute(
                f"""
                SELECT from_entity_id AS entity_id FROM relations WHERE to_entity_id IN ({placeholders})
                UNION
                SELECT to_entity_id AS entity_id FROM relations WHERE from_entity_id IN ({placeholders})
                """,
                (*frontier, *frontier),
            ).fetchall()
            next_frontier: set[str] = set()
            for row in rows:
                entity_id = str(row["entity_id"])
                if entity_id not in visited:
                    visited.add(entity_id)
                    next_frontier.add(entity_id)
            frontier = next_frontier
        return visited

    def link_chunk_entity(self, chunk_id: str, entity_ref: str, *, surface: str | None = None) -> None:
        entity_id = self.resolve_entity_ref(entity_ref)
        entity = self.get_entity(entity_id)
        display_surface = surface or entity.canonical_name
        self.conn.execute(
            "INSERT OR IGNORE INTO chunk_entities VALUES (?, ?, ?)",
            (chunk_id, entity_id, display_surface),
        )

    def link_note_entity(self, note_id: str, entity_ref: str) -> None:
        entity_id = self.resolve_entity_ref(entity_ref)
        self.conn.execute("INSERT OR IGNORE INTO note_entities VALUES (?, ?)", (note_id, entity_id))

    def entities_for_memory(self, working_memory_id: str) -> list[str]:
        rows = self.conn.execute(
            """
            SELECT DISTINCT e.canonical_name
            FROM note_entities ne
            JOIN memory_notes mn ON mn.id = ne.note_id
            JOIN entities e ON e.id = ne.entity_id
            WHERE mn.working_memory_id = ? AND mn.status = ?
            """,
            (working_memory_id, NoteStatus.ACTIVE.value),
        )
        return [str(row["canonical_name"]) for row in rows]

    def chunks_for_entities(self, entities: list[str]) -> list[Chunk]:
        if not entities:
            return []
        entity_ids: list[str] = []
        for name in entities:
            entity = self.find_entity_seed(name)
            if entity is not None:
                entity_ids.append(entity.id)
        expanded_ids = self.related_entity_ids(entity_ids, max_hops=2)
        if not expanded_ids:
            return []
        placeholders = ",".join("?" for _ in expanded_ids)
        rows = self.conn.execute(
            f"""
            SELECT DISTINCT c.*
            FROM chunks c
            JOIN chunk_entities ce ON ce.chunk_id = c.id
            WHERE ce.entity_id IN ({placeholders})
            ORDER BY c.rowid
            """,
            tuple(expanded_ids),
        )
        return [self._row_to_chunk(row) for row in rows]

    def neighbor_chunks_for_evidence(self, working_memory_id: str) -> list[Chunk]:
        rows = self.conn.execute(
            """
            SELECT DISTINCT n.*
            FROM evidence e
            JOIN chunks c ON c.id = e.chunk_id
            JOIN chunks n ON n.document_id = c.document_id
            WHERE e.working_memory_id = ?
              AND ABS(n.position - c.position) <= 1
            ORDER BY n.rowid
            """,
            (working_memory_id,),
        )
        return [self._row_to_chunk(row) for row in rows]

    def chunks_for_conflicted_notes(self, working_memory_id: str) -> list[Chunk]:
        rows = self.conn.execute(
            """
            WITH conflicted_notes AS (
              SELECT c.note_b_id AS note_id
              FROM conflicts c
              JOIN memory_notes a ON a.id = c.note_a_id
              WHERE a.working_memory_id = ?
              UNION
              SELECT c.note_a_id AS note_id
              FROM conflicts c
              JOIN memory_notes b ON b.id = c.note_b_id
              WHERE b.working_memory_id = ?
            ),
            direct_chunks AS (
              SELECT ch.*
              FROM conflicted_notes cn
              JOIN note_evidence ne ON ne.note_id = cn.note_id
              JOIN evidence e ON e.id = ne.evidence_id
              JOIN chunks ch ON ch.id = e.chunk_id
            ),
            entity_chunks AS (
              SELECT ch.*
              FROM conflicted_notes cn
              JOIN note_entities ne ON ne.note_id = cn.note_id
              JOIN chunk_entities ce ON ce.entity_id = ne.entity_id
              JOIN chunks ch ON ch.id = ce.chunk_id
            )
            SELECT DISTINCT *
            FROM (
              SELECT * FROM direct_chunks
              UNION
              SELECT * FROM entity_chunks
            )
            ORDER BY id
            """,
            (working_memory_id, working_memory_id),
        )
        chunks = [self._row_to_chunk(row) for row in rows]
        return sorted(chunks, key=lambda chunk: self._chunk_position_key(chunk.id))

    def _chunk_position_key(self, chunk_id: str) -> tuple[int, str]:
        row = self.conn.execute("SELECT rowid FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
        return (int(row["rowid"]) if row is not None else 0, chunk_id)

    def add_open_question(self, working_memory_id: str, question: str, reason: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO open_questions VALUES (?, ?, ?, 0)",
            (working_memory_id, question, reason),
        )
        self.conn.commit()

    def list_open_questions(self, working_memory_id: str) -> list[dict[str, str]]:
        rows = self.conn.execute(
            "SELECT question, reason FROM open_questions WHERE working_memory_id = ? AND resolved = 0 ORDER BY rowid",
            (working_memory_id,),
        )
        return [{"question": str(row["question"]), "reason": str(row["reason"])} for row in rows]

    def resolve_open_questions(self, working_memory_id: str) -> None:
        self.conn.execute("UPDATE open_questions SET resolved = 1 WHERE working_memory_id = ?", (working_memory_id,))
        self.conn.commit()

    def resolve_open_question(self, working_memory_id: str, question: str) -> None:
        self.conn.execute(
            "UPDATE open_questions SET resolved = 1 WHERE working_memory_id = ? AND question = ?",
            (working_memory_id, question),
        )
        self.conn.commit()

    def record_round_log(self, log: RoundLog) -> None:
        self.conn.execute(
            "INSERT INTO round_logs (working_memory_id, round, payload) VALUES (?, ?, ?)",
            (
                log.working_memory_id,
                log.round,
                json.dumps(
                    {
                        "working_memory_id": log.working_memory_id,
                        "round": log.round,
                        "actions": log.actions,
                        "action_details": log.action_details,
                        "candidate_count": log.candidate_count,
                        "accepted_evidence_count": log.accepted_evidence_count,
                        "accepted_evidence_ids": log.accepted_evidence_ids,
                        "created_note_count": log.created_note_count,
                        "accepted_note_count": log.accepted_note_count,
                        "duplicate_count": log.duplicate_count,
                        "conflict_count": log.conflict_count,
                        "gain": log.gain,
                        "stop_reason": log.stop_reason,
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        self.conn.commit()

    def _aliases_for_entity(self, entity_id: str) -> tuple[str, ...]:
        rows = self.conn.execute(
            "SELECT alias FROM entity_aliases WHERE entity_id = ? ORDER BY alias",
            (entity_id,),
        )
        return tuple(str(row["alias"]) for row in rows)

    def _row_to_entity(self, row: sqlite3.Row) -> Entity:
        return Entity(
            id=str(row["id"]),
            entity_type=EntityType(str(row["entity_type"])),
            canonical_name=str(row["canonical_name"]),
            normalized_name=str(row["normalized_name"]),
            aliases=self._aliases_for_entity(str(row["id"])),
            embedding=json.loads(str(row["embedding"])),
            attributes=json.loads(str(row["attributes"])),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def _row_to_relation(self, row: sqlite3.Row) -> Relation:
        return Relation(
            id=str(row["id"]),
            from_entity_id=str(row["from_entity_id"]),
            to_entity_id=str(row["to_entity_id"]),
            relation_type=str(row["relation_type"]),
            source_chunk_id=str(row["source_chunk_id"]),
            confidence=float(row["confidence"]),
            evidence_text=str(row["evidence_text"]),
            attributes=json.loads(str(row["attributes"])),
            created_at=str(row["created_at"]),
        )

    def _row_to_chunk(self, row: sqlite3.Row) -> Chunk:
        return Chunk(
            id=str(row["id"]),
            document_id=str(row["document_id"]),
            body=str(row["body"]),
            embedding=json.loads(str(row["embedding"])),
            token_count=int(row["token_count"]),
            position=int(row["position"]),
            section_title=row["section_title"],
            source_uri=str(row["source_uri"]),
            metadata=json.loads(str(row["metadata"])),
        )

    def _row_to_wm(self, row: sqlite3.Row) -> WorkingMemory:
        return WorkingMemory(
            id=str(row["id"]),
            question_id=str(row["question_id"]),
            original_question=str(row["original_question"]),
            status=WorkingMemoryStatus(str(row["status"])),
            round_count=int(row["round_count"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def _row_to_note(self, row: sqlite3.Row) -> MemoryNote:
        return MemoryNote(
            id=str(row["id"]),
            working_memory_id=str(row["working_memory_id"]),
            claim=str(row["claim"]),
            normalized_claim=str(row["normalized_claim"]),
            note_type=NoteType(str(row["note_type"])),
            support_score=float(row["support_score"]),
            relevance_score=float(row["relevance_score"]),
            novelty_score=float(row["novelty_score"]),
            conflict_score=float(row["conflict_score"]),
            confidence=float(row["confidence"]),
            source_count=int(row["source_count"]),
            embedding=json.loads(str(row["embedding"])),
            created_round=int(row["created_round"]),
            last_updated_round=int(row["last_updated_round"]),
            status=NoteStatus(str(row["status"])),
        )

    def _row_to_evidence(self, row: sqlite3.Row) -> Evidence:
        return Evidence(
            id=str(row["id"]),
            chunk_id=str(row["chunk_id"]),
            working_memory_id=str(row["working_memory_id"]),
            round=int(row["round"]),
            query=str(row["query"]),
            body_excerpt=str(row["body_excerpt"]),
            retrieval_method=RetrievalMethod(str(row["retrieval_method"])),
            raw_rank=int(row["raw_rank"]),
            relevance_score=float(row["relevance_score"]),
            memory_value_score=float(row["memory_value_score"]),
            accepted=bool(row["accepted"]),
            source_uri=str(row["source_uri"]),
        )
