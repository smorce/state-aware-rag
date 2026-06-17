from __future__ import annotations

from state_aware_rag.bosun import RuleBosunScorer
from state_aware_rag.config import RagConfig
from state_aware_rag.llm import LocalHeuristicLLM, PlannerAndWriter
from state_aware_rag.models import (
    AnswerResult,
    Evidence,
    IngestedDocument,
    OpenQuestion,
    RetrievalCandidate,
    RoundLog,
    SearchBudget,
    SearchState,
    WorkingMemory,
    WorkingMemoryStatus,
    NoteStatus,
)
from state_aware_rag.retrieval import Retriever
from state_aware_rag.store import SQLiteRagStore
from state_aware_rag.strategy import SearchStrategy, SocraticSearchStrategy
from state_aware_rag.text import MSG_DEMEMOIZATION_FAILED, normalize_claim, overlap_score


class StateAwareRag:
    def __init__(
        self,
        store: SQLiteRagStore,
        *,
        config: RagConfig | None = None,
        llm: PlannerAndWriter | None = None,
        bosun: RuleBosunScorer | None = None,
        search_strategy: SearchStrategy | None = None,
    ) -> None:
        self.config = config or store.config
        self.store = store
        self.retriever = Retriever(store, self.config)
        self.llm = llm or LocalHeuristicLLM()
        self.bosun = bosun or RuleBosunScorer()
        self.search_strategy = search_strategy or SocraticSearchStrategy(self.llm, self.config)

    def ingest_document(
        self,
        *,
        title: str,
        body: str,
        source_uri: str,
        chunk_size: int = 700,
        overlap: int = 80,
        extract_entities: bool = True,
        extractor_backend: str = "rule",
    ) -> IngestedDocument:
        return self.store.ingest_document(
            title=title,
            body=body,
            source_uri=source_uri,
            chunk_size=chunk_size,
            overlap=overlap,
            extract_entities=extract_entities,
            extractor_backend=extractor_backend,
        )

    def answer(self, question: str) -> AnswerResult:
        wm = self.store.create_working_memory(question)
        try:
            return self._answer_with_working_memory(question, wm)
        except Exception:
            try:
                self.store.update_working_memory(wm.id, status=WorkingMemoryStatus.FAILED)
            except Exception:
                pass
            raise

    def _answer_with_working_memory(self, question: str, wm: WorkingMemory) -> AnswerResult:
        no_new_note_rounds = 0
        low_gain_rounds = 0
        previous_queries: list[str] = []
        stop_status: WorkingMemoryStatus | None = None

        for round_number in range(1, self.config.max_rounds + 1):
            notes = self.store.list_memory_notes(wm.id)
            open_questions = self._open_questions(wm.id)
            state = SearchState(
                question=question,
                working_memory_id=wm.id,
                round=round_number,
                notes=notes,
                open_questions=open_questions,
                previous_queries=previous_queries,
                previous_evidence_ids=[ev.id for ev in self.store.list_evidence(wm.id)],
            )
            actions = self.search_strategy.propose_next_actions(state)
            selected = self.search_strategy.select_actions(
                actions,
                SearchBudget(max_actions=self.config.max_sub_questions_per_round),
                state,
            )
            if not selected:
                stop_status = WorkingMemoryStatus.COMPLETED
                self._record_log(wm.id, round_number, [], 0, [], 0, 0, 0, 0, 0.0, stop_status.value)
                break

            all_candidates: list[RetrievalCandidate] = []
            for action in selected:
                previous_queries.extend([action.vector_query, action.text_query])
                all_candidates.extend(self.retriever.vector_search(action.vector_query, self.config.vector_top_k))
                all_candidates.extend(self.retriever.text_search(action.text_query, self.config.text_top_k))
                all_candidates.extend(self.retriever.graph_search(action.graph_seed_entities, wm.id, self.config.graph_top_k))

            merged = self.retriever.merge_candidates(all_candidates)
            accepted_evidence = self._score_and_save_evidence(question, wm, round_number, selected[0].text_query, merged)
            if not accepted_evidence:
                no_new_note_rounds += 1
                low_gain_rounds += 1
                gain = 0.0
                stop_status = self._stop_status(
                    round_number,
                    no_new_note_rounds,
                    low_gain_rounds,
                    open_question_count=len(open_questions),
                    active_note_count=len(notes),
                )
                self._record_log(wm.id, round_number, [a.action_id for a in selected], len(merged), [], 0, 0, 0, 0, gain, stop_status.value if stop_status else None)
                self.store.update_working_memory(wm.id, round_count=round_number)
                if stop_status:
                    break
                continue

            created_notes = self.llm.create_atomic_notes(question, self.store.list_memory_notes(wm.id), accepted_evidence)
            accepted_count, duplicate_count, conflict_count = self._save_notes(wm.id, round_number, created_notes)
            if created_notes.get("open_questions"):
                for item in created_notes["open_questions"]:
                    self.store.add_open_question(wm.id, str(item["question"]), str(item.get("reason", "")))

            if accepted_count > 0:
                no_new_note_rounds = 0
            else:
                no_new_note_rounds += 1

            gain = accepted_count + 0.5 * len(accepted_evidence) - 0.5 * duplicate_count - 0.5 * conflict_count
            current_open_questions = self._open_questions(wm.id)
            current_active_note_count = len(self.store.list_memory_notes(wm.id))
            # 全候補低スコアが続く場合は accepted evidence が空のラウンドと同じ低 gain 停止へ集約する。
            low_gain_rounds = low_gain_rounds + 1 if gain <= 0 else 0
            stop_status = self._stop_status(
                round_number,
                no_new_note_rounds,
                low_gain_rounds,
                open_question_count=len(current_open_questions),
                active_note_count=current_active_note_count,
            )
            self._record_log(
                wm.id,
                round_number,
                [a.action_id for a in selected],
                len(merged),
                accepted_evidence,
                len(created_notes.get("notes", [])),
                accepted_count,
                duplicate_count,
                conflict_count,
                gain,
                stop_status.value if stop_status else None,
            )
            self.store.update_working_memory(wm.id, round_count=round_number)
            if stop_status:
                break

        if stop_status is None:
            stop_status = WorkingMemoryStatus.STOPPED_BY_MAX_ROUNDS
        wm = self.store.update_working_memory(wm.id, status=stop_status)
        notes = self.store.list_memory_notes(wm.id)
        evidence_by_note = {note.id: self.store.evidence_for_note(note.id) for note in notes}
        open_questions = self._open_questions(wm.id)
        conflicts = self.store.list_conflicts(wm.id)
        evidence = self.store.list_evidence(wm.id)
        if evidence and not notes:
            answer = MSG_DEMEMOIZATION_FAILED
        else:
            answer = self.llm.generate_final_answer(question, notes, evidence_by_note, conflicts, open_questions)
        return AnswerResult(
            answer=answer,
            working_memory=wm,
            memory_notes=notes,
            evidence=evidence,
            open_questions=open_questions,
            conflicts=conflicts,
        )

    def _score_and_save_evidence(
        self,
        question: str,
        wm: WorkingMemory,
        round_number: int,
        query: str,
        candidates: list[RetrievalCandidate],
    ) -> list[Evidence]:
        accepted: list[Evidence] = []
        summary = self._memory_summary(wm.id)
        for candidate in candidates:
            relevance = self.bosun.relevance_score(question, summary, candidate)
            memory_value = self.bosun.memory_value_score(question, summary, candidate)
            if relevance < self.config.relevance_threshold or memory_value < self.config.memory_value_threshold:
                continue
            accepted.append(
                self.store.create_evidence(
                    wm.id,
                    candidate.chunk_id,
                    round_number=round_number,
                    query=query,
                    body_excerpt=candidate.body,
                    retrieval_method=candidate.method,
                    raw_rank=candidate.raw_rank,
                    relevance_score=relevance,
                    memory_value_score=memory_value,
                    accepted=True,
                    source_uri=candidate.source_uri,
                )
            )
            if len(accepted) >= self.config.max_accepted_evidence_per_round:
                break
        return accepted

    def _save_notes(self, working_memory_id: str, round_number: int, payload: dict[str, object]) -> tuple[int, int, int]:
        accepted_count = 0
        duplicate_count = 0
        conflict_count = 0
        for item in payload.get("notes", []):
            note_item = dict(item)  # type: ignore[arg-type]
            claim = str(note_item["claim"])
            evidence_ids = [str(value) for value in note_item.get("supported_by_evidence_ids", [])]
            existing_notes = self.store.list_memory_notes(working_memory_id)
            duplicate_note = None
            duplicate_score = 0.0
            for existing in existing_notes:
                score = self.bosun.duplicate_score(existing, claim)
                if score >= self.config.duplicate_threshold:
                    duplicate_note = existing
                    duplicate_score = score
                    break
            if duplicate_note is not None:
                support_score, relevance_score = self._scores_for_evidence(evidence_ids, fallback=float(note_item.get("confidence", 0.75)))
                shadow = self.store.create_memory_note(
                    working_memory_id,
                    claim,
                    str(note_item.get("note_type", "fact")),
                    float(note_item.get("confidence", 0.75)),
                    evidence_ids,
                    round_number,
                    support_score=support_score,
                    relevance_score=relevance_score,
                    novelty_score=max(0.0, 1.0 - duplicate_score),
                    status=NoteStatus.DUPLICATE,
                )
                self.store.merge_duplicate_note(duplicate_note.id, evidence_ids, duplicate_score)
                self.store.add_duplicate_edge(shadow.id, duplicate_note.id, duplicate_score)
                self._resolve_open_questions_for_claim(working_memory_id, claim)
                duplicate_count += 1
                continue

            support_score, relevance_score = self._scores_for_evidence(evidence_ids, fallback=float(note_item.get("confidence", 0.75)))
            new_conflict_score = 0.0
            conflicts: list[tuple[str, float]] = []
            for existing in existing_notes:
                score = self.bosun.conflict_score(existing, claim)
                if score >= self.config.conflict_threshold:
                    conflicts.append((existing.id, score))
                    new_conflict_score = max(new_conflict_score, score)
            note = self.store.create_memory_note(
                working_memory_id,
                claim,
                str(note_item.get("note_type", "fact")),
                float(note_item.get("confidence", 0.75)),
                evidence_ids,
                round_number,
                support_score=support_score,
                relevance_score=relevance_score,
                conflict_score=new_conflict_score,
            )
            self._resolve_open_questions_for_claim(working_memory_id, claim)
            for existing_id, score in conflicts:
                self.store.add_conflict(existing_id, note.id, score)
                conflict_count += 1
            accepted_count += 1
        return accepted_count, duplicate_count, conflict_count

    def _scores_for_evidence(self, evidence_ids: list[str], *, fallback: float) -> tuple[float, float]:
        evidence: list[Evidence] = []
        for evidence_id in evidence_ids:
            try:
                evidence.append(self.store.get_evidence(evidence_id))
            except KeyError:
                continue
        if not evidence:
            return fallback, fallback
        support_score = sum(item.memory_value_score for item in evidence) / len(evidence)
        relevance_score = sum(item.relevance_score for item in evidence) / len(evidence)
        return support_score, relevance_score

    def _resolve_open_questions_for_claim(self, working_memory_id: str, claim: str) -> None:
        normalized_claim = normalize_claim(claim)
        for item in self._open_questions(working_memory_id):
            normalized_question = normalize_claim(item.question)
            if not normalized_question:
                continue
            if normalized_question in normalized_claim or overlap_score(normalized_claim, normalized_question) >= 0.20:
                self.store.resolve_open_question(working_memory_id, item.question)

    def _stop_status(
        self,
        round_number: int,
        no_new_note_rounds: int,
        low_gain_rounds: int,
        *,
        open_question_count: int,
        active_note_count: int,
    ) -> WorkingMemoryStatus | None:
        if no_new_note_rounds >= self.config.no_new_note_limit:
            return WorkingMemoryStatus.STOPPED_BY_NO_NEW_NOTES
        if low_gain_rounds >= self.config.low_gain_limit:
            return WorkingMemoryStatus.STOPPED_BY_LOW_GAIN
        if round_number >= self.config.max_rounds:
            return WorkingMemoryStatus.STOPPED_BY_MAX_ROUNDS
        if active_note_count > 0 and open_question_count == 0:
            return WorkingMemoryStatus.COMPLETED
        return None

    def _memory_summary(self, working_memory_id: str) -> str:
        return "\n".join(note.claim for note in self.store.list_memory_notes(working_memory_id))

    def _open_questions(self, working_memory_id: str) -> list[OpenQuestion]:
        return [OpenQuestion(question=item["question"], reason=item["reason"]) for item in self.store.list_open_questions(working_memory_id)]

    def _record_log(
        self,
        working_memory_id: str,
        round_number: int,
        action_ids: list[str],
        candidate_count: int,
        evidence: list[Evidence],
        created_note_count: int,
        accepted_note_count: int,
        duplicate_count: int,
        conflict_count: int,
        gain: float,
        stop_reason: str | None,
    ) -> None:
        self.store.record_round_log(
            RoundLog(
                working_memory_id=working_memory_id,
                round=round_number,
                actions=action_ids,
                candidate_count=candidate_count,
                accepted_evidence_count=len(evidence),
                created_note_count=created_note_count,
                accepted_note_count=accepted_note_count,
                duplicate_count=duplicate_count,
                conflict_count=conflict_count,
                gain=gain,
                stop_reason=stop_reason,
                accepted_evidence_ids=[ev.id for ev in evidence],
            )
        )
