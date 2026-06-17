from __future__ import annotations

from state_aware_rag.bosun import RuleBosunScorer
from state_aware_rag.config import RagConfig
from state_aware_rag.language import detect_language
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
from state_aware_rag.run_log import (
    ROUTE_ANSWER_FINAL_DEMEMOIZATION_FAILED,
    ROUTE_ANSWER_FINAL_NO_EVIDENCE,
    ROUTE_ANSWER_FINAL_STOPPED,
    ROUTE_ANSWER_FINAL_SUCCESS,
    ROUTE_ANSWER_SESSION_START,
    ROUTE_ANSWER_UNHANDLED_EXCEPTION,
    ROUTE_ROUND_ATOMIC_NOTES_CREATED,
    ROUTE_ROUND_ATOMIC_NOTES_EMPTY,
    ROUTE_ROUND_NO_ACCEPTED_EVIDENCE,
    ROUTE_ROUND_NO_ACTIONS,
    ROUTE_ROUND_NOTE_ACCEPTED,
    ROUTE_ROUND_NOTE_CONFLICT,
    ROUTE_ROUND_NOTE_DUPLICATE,
    ROUTE_ROUND_RETRIEVAL_EMPTY,
    ROUTE_SCORE_CANDIDATE_ACCEPTED,
    ROUTE_SCORE_CANDIDATE_REJECTED_MEMORY_VALUE,
    ROUTE_SCORE_CANDIDATE_REJECTED_RELEVANCE,
    RunLogger,
)
from state_aware_rag.store import SQLiteRagStore
from state_aware_rag.strategy import SearchStrategy, SocraticSearchStrategy
from state_aware_rag.text import MSG_DEMEMOIZATION_FAILED, MSG_NO_EVIDENCE, normalize_claim, overlap_score


class StateAwareRag:
    def __init__(
        self,
        store: SQLiteRagStore,
        *,
        config: RagConfig | None = None,
        llm: PlannerAndWriter | None = None,
        bosun: RuleBosunScorer | None = None,
        search_strategy: SearchStrategy | None = None,
        run_logger: RunLogger | None = None,
    ) -> None:
        self.config = config or store.config
        self.store = store
        self.retriever = Retriever(store, self.config)
        self.llm = llm or LocalHeuristicLLM()
        self.bosun = bosun or RuleBosunScorer()
        self.search_strategy = search_strategy or SocraticSearchStrategy(self.llm, self.config)
        self._run_logger = run_logger

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
        language = detect_language(question)
        logger = self._run_logger or RunLogger.for_question(
            question,
            language,
            enabled=self.config.run_log_enabled,
        )
        logger.log(
            ROUTE_ANSWER_SESSION_START,
            component="orchestrator",
            outcome="success",
            reason_code="session_started",
            reason_detail="answer() started",
            extra={"bosun": type(self.bosun).__name__, "llm": type(self.llm).__name__},
        )
        wm = self.store.create_working_memory(question)
        try:
            result = self._answer_with_working_memory(question, wm, logger)
            logger.flush_excel()
            return result
        except Exception as exc:
            logger.log(
                ROUTE_ANSWER_UNHANDLED_EXCEPTION,
                component="orchestrator",
                outcome="failure",
                reason_code="unhandled_exception",
                reason_detail=str(exc),
                working_memory_id=wm.id,
                extra={"exception_type": type(exc).__name__},
            )
            try:
                self.store.update_working_memory(wm.id, status=WorkingMemoryStatus.FAILED)
            except Exception:
                pass
            logger.flush_excel()
            raise

    def _answer_with_working_memory(self, question: str, wm: WorkingMemory, logger: RunLogger) -> AnswerResult:
        no_new_note_rounds = 0
        low_gain_rounds = 0
        previous_queries: list[str] = []
        stop_status: WorkingMemoryStatus | None = None
        relevance_threshold, memory_value_threshold, question_language = self.config.scoring_thresholds(question)

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
                logger.log(
                    ROUTE_ROUND_NO_ACTIONS,
                    component="strategy",
                    outcome="success",
                    reason_code="no_actions_selected",
                    reason_detail="Search strategy returned no further actions; treating as completed.",
                    working_memory_id=wm.id,
                    round_number=round_number,
                    stop_status=stop_status.value,
                )
                self._record_log(wm.id, round_number, [], 0, [], 0, 0, 0, 0, 0.0, stop_status.value, action_details=[])
                break

            all_candidates: list[RetrievalCandidate] = []
            for action in selected:
                previous_queries.extend([action.vector_query, action.text_query])
                all_candidates.extend(self.retriever.vector_search(action.vector_query, self.config.vector_top_k))
                all_candidates.extend(self.retriever.text_search(action.text_query, self.config.text_top_k))
                all_candidates.extend(self.retriever.graph_search(action.graph_seed_entities, wm.id, self.config.graph_top_k))

            merged = self.retriever.merge_candidates(all_candidates)
            if not merged:
                logger.log(
                    ROUTE_ROUND_RETRIEVAL_EMPTY,
                    component="retrieval",
                    outcome="failure",
                    reason_code="no_candidates",
                    reason_detail="vector/text/graph search returned no mergeable candidates",
                    working_memory_id=wm.id,
                    round_number=round_number,
                    candidate_count=0,
                    relevance_threshold=relevance_threshold,
                    memory_value_threshold=memory_value_threshold,
                )
            accepted_evidence = self._score_and_save_evidence(
                question,
                wm,
                round_number,
                merged,
                logger,
                relevance_threshold=relevance_threshold,
                memory_value_threshold=memory_value_threshold,
                question_language=question_language,
            )
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
                logger.log(
                    ROUTE_ROUND_NO_ACCEPTED_EVIDENCE,
                    component="orchestrator",
                    outcome="failure",
                    reason_code="all_candidates_rejected",
                    reason_detail=(
                        f"No evidence accepted in round {round_number}. "
                        f"See score.candidate_rejected.* rows for per-candidate reasons."
                    ),
                    working_memory_id=wm.id,
                    round_number=round_number,
                    candidate_count=len(merged),
                    accepted_evidence_count=0,
                    relevance_threshold=relevance_threshold,
                    memory_value_threshold=memory_value_threshold,
                    stop_status=stop_status.value if stop_status else None,
                    extra={"question_language": question_language},
                )
                self._record_log(
                    wm.id,
                    round_number,
                    [a.action_id for a in selected],
                    len(merged),
                    [],
                    0,
                    0,
                    0,
                    0,
                    gain,
                    stop_status.value if stop_status else None,
                    action_details=[
                        {
                            "action_id": a.action_id,
                            "sub_question": a.sub_question,
                            "vector_query": a.vector_query,
                            "text_query": a.text_query,
                            "graph_seed_entities": a.graph_seed_entities,
                            "priority": a.priority,
                        }
                        for a in selected
                    ],
                )
                self.store.update_working_memory(wm.id, round_count=round_number)
                if stop_status:
                    break
                continue

            created_notes = self.llm.create_atomic_notes(question, self.store.list_memory_notes(wm.id), accepted_evidence)
            note_items = list(created_notes.get("notes", []))
            if not note_items:
                logger.log(
                    ROUTE_ROUND_ATOMIC_NOTES_EMPTY,
                    component="llm",
                    outcome="failure",
                    reason_code="atomic_notes_empty",
                    reason_detail="LLM returned zero atomic notes for accepted evidence",
                    working_memory_id=wm.id,
                    round_number=round_number,
                    accepted_evidence_count=len(accepted_evidence),
                    created_note_count=0,
                )
            else:
                logger.log(
                    ROUTE_ROUND_ATOMIC_NOTES_CREATED,
                    component="llm",
                    outcome="success",
                    reason_code="atomic_notes_created",
                    reason_detail=f"LLM returned {len(note_items)} note candidate(s)",
                    working_memory_id=wm.id,
                    round_number=round_number,
                    accepted_evidence_count=len(accepted_evidence),
                    created_note_count=len(note_items),
                )
            accepted_count, duplicate_count, conflict_count = self._save_notes(
                wm.id,
                round_number,
                created_notes,
                logger,
            )
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
                len(note_items),
                accepted_count,
                duplicate_count,
                conflict_count,
                gain,
                stop_status.value if stop_status else None,
                action_details=[
                    {
                        "action_id": a.action_id,
                        "sub_question": a.sub_question,
                        "vector_query": a.vector_query,
                        "text_query": a.text_query,
                        "graph_seed_entities": a.graph_seed_entities,
                        "priority": a.priority,
                    }
                    for a in selected
                ],
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
            logger.log(
                ROUTE_ANSWER_FINAL_DEMEMOIZATION_FAILED,
                component="orchestrator",
                outcome="failure",
                reason_code="dememoization_failed",
                reason_detail=MSG_DEMEMOIZATION_FAILED,
                working_memory_id=wm.id,
                accepted_evidence_count=len(evidence),
                created_note_count=0,
                stop_status=stop_status.value,
            )
        elif not evidence:
            answer = MSG_NO_EVIDENCE
            logger.log(
                ROUTE_ANSWER_FINAL_NO_EVIDENCE,
                component="orchestrator",
                outcome="failure",
                reason_code="no_evidence",
                reason_detail=MSG_NO_EVIDENCE,
                working_memory_id=wm.id,
                accepted_evidence_count=0,
                stop_status=stop_status.value,
            )
        else:
            answer = self.llm.generate_final_answer(question, notes, evidence_by_note, conflicts, open_questions)
            logger.log(
                ROUTE_ANSWER_FINAL_SUCCESS,
                component="llm",
                outcome="success",
                reason_code="final_answer_generated",
                reason_detail="Final answer generated from memory notes",
                working_memory_id=wm.id,
                accepted_evidence_count=len(evidence),
                accepted_note_count=len(notes),
                stop_status=stop_status.value,
            )
        if stop_status != WorkingMemoryStatus.COMPLETED:
            logger.log(
                ROUTE_ANSWER_FINAL_STOPPED,
                component="orchestrator",
                outcome="skipped",
                reason_code=stop_status.value,
                reason_detail=f"Loop stopped with status={stop_status.value}",
                working_memory_id=wm.id,
                stop_status=stop_status.value,
            )
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
        candidates: list[RetrievalCandidate],
        logger: RunLogger,
        *,
        relevance_threshold: float,
        memory_value_threshold: float,
        question_language: str,
    ) -> list[Evidence]:
        accepted: list[Evidence] = []
        summary = self._memory_summary(wm.id)
        for candidate in candidates:
            relevance = self.bosun.relevance_score(question, summary, candidate)
            memory_value = self.bosun.memory_value_score(question, summary, candidate)
            if relevance < relevance_threshold:
                logger.log(
                    ROUTE_SCORE_CANDIDATE_REJECTED_RELEVANCE,
                    component="bosun",
                    outcome="failure",
                    reason_code="relevance_below_threshold",
                    reason_detail=(
                        f"relevance={relevance:.4f} < threshold={relevance_threshold:.4f} "
                        f"(language={question_language})"
                    ),
                    working_memory_id=wm.id,
                    round_number=round_number,
                    chunk_id=candidate.chunk_id,
                    relevance_score=relevance,
                    memory_value_score=memory_value,
                    relevance_threshold=relevance_threshold,
                    memory_value_threshold=memory_value_threshold,
                    extra={
                        "question_language": question_language,
                        "retrieval_methods": [m.value for m in candidate.retrieval_methods],
                        "source_uri": candidate.source_uri,
                    },
                )
                continue
            if memory_value < memory_value_threshold:
                logger.log(
                    ROUTE_SCORE_CANDIDATE_REJECTED_MEMORY_VALUE,
                    component="bosun",
                    outcome="failure",
                    reason_code="memory_value_below_threshold",
                    reason_detail=(
                        f"memory_value={memory_value:.4f} < threshold={memory_value_threshold:.4f} "
                        f"(language={question_language})"
                    ),
                    working_memory_id=wm.id,
                    round_number=round_number,
                    chunk_id=candidate.chunk_id,
                    relevance_score=relevance,
                    memory_value_score=memory_value,
                    relevance_threshold=relevance_threshold,
                    memory_value_threshold=memory_value_threshold,
                    extra={
                        "question_language": question_language,
                        "retrieval_methods": [m.value for m in candidate.retrieval_methods],
                        "source_uri": candidate.source_uri,
                    },
                )
                continue
            evidence = self.store.create_evidence(
                wm.id,
                candidate.chunk_id,
                round_number=round_number,
                query=candidate.query,
                body_excerpt=candidate.body,
                retrieval_method=candidate.method,
                raw_rank=candidate.raw_rank,
                relevance_score=relevance,
                memory_value_score=memory_value,
                accepted=True,
                source_uri=candidate.source_uri,
            )
            accepted.append(evidence)
            logger.log(
                ROUTE_SCORE_CANDIDATE_ACCEPTED,
                component="bosun",
                outcome="success",
                reason_code="candidate_accepted",
                reason_detail=(
                    f"relevance={relevance:.4f}, memory_value={memory_value:.4f} "
                    f"(language={question_language})"
                ),
                working_memory_id=wm.id,
                round_number=round_number,
                chunk_id=candidate.chunk_id,
                relevance_score=relevance,
                memory_value_score=memory_value,
                relevance_threshold=relevance_threshold,
                memory_value_threshold=memory_value_threshold,
                extra={"evidence_id": evidence.id, "source_uri": candidate.source_uri},
            )
            if len(accepted) >= self.config.max_accepted_evidence_per_round:
                break
        return accepted

    def _save_notes(
        self,
        working_memory_id: str,
        round_number: int,
        payload: dict[str, object],
        logger: RunLogger | None = None,
    ) -> tuple[int, int, int]:
        log = logger or RunLogger(enabled=False)
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
                log.log(
                    ROUTE_ROUND_NOTE_DUPLICATE,
                    component="bosun",
                    outcome="skipped",
                    reason_code="duplicate_note",
                    reason_detail=f"duplicate_score={duplicate_score:.4f} >= {self.config.duplicate_threshold:.4f}",
                    working_memory_id=working_memory_id,
                    round_number=round_number,
                    extra={"shadow_note_id": shadow.id, "canonical_note_id": duplicate_note.id, "claim": claim},
                )
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
                log.log(
                    ROUTE_ROUND_NOTE_CONFLICT,
                    component="bosun",
                    outcome="skipped",
                    reason_code="note_conflict",
                    reason_detail=f"conflict_score={score:.4f} >= {self.config.conflict_threshold:.4f}",
                    working_memory_id=working_memory_id,
                    round_number=round_number,
                    extra={"note_id": note.id, "conflicting_note_id": existing_id, "claim": claim},
                )
            accepted_count += 1
            log.log(
                ROUTE_ROUND_NOTE_ACCEPTED,
                component="orchestrator",
                outcome="success",
                reason_code="note_accepted",
                reason_detail="New active memory note created",
                working_memory_id=working_memory_id,
                round_number=round_number,
                extra={"note_id": note.id, "claim": claim},
            )
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
        *,
        action_details: list[dict[str, object]] | None = None,
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
                action_details=list(action_details or []),
            )
        )
