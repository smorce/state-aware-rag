from __future__ import annotations

from typing import Protocol

from state_aware_rag.config import RagConfig
from state_aware_rag.language import detect_language
from state_aware_rag.llm import PlannerAndWriter
from state_aware_rag.models import SearchAction, SearchBudget, SearchState, new_id
from state_aware_rag.text import overlap_score, tokenize


def _english_claim_templates(question: str) -> tuple[str, str, str]:
    cleaned = question.strip().rstrip("?.!")
    if not cleaned:
        cleaned = "the claim in the original question"
    sub_question = f"Is it true that {cleaned}?"
    vector_query = f"evidence for and against {cleaned}"
    text_query = f"\"{cleaned}\" true false evidence"
    return sub_question, vector_query, text_query


def _english_graph_seeds(question: str, sub_question: str) -> list[str]:
    text = f"{sub_question} {question}".lower()
    seeds: list[str] = []
    for token in tokenize(text):
        if not token.isascii() or not token.isalpha() or len(token) < 4:
            continue
        if token in {"that", "with", "from", "this", "true", "false", "evidence", "claim"}:
            continue
        if token not in seeds:
            seeds.append(token)
        if len(seeds) >= 3:
            break
    return seeds or ["claim", "evidence"]


def _coerce_action_language(item: dict[str, object], question: str, question_language: str) -> dict[str, object]:
    """質問言語と検索アクション言語のズレを最小限で補正する。"""
    if question_language != "en":
        return item
    coerced = dict(item)
    sub_question, vector_query, text_query = _english_claim_templates(question)
    field_fallbacks = {
        "sub_question": sub_question,
        "vector_query": vector_query,
        "text_query": text_query,
    }
    for key, fallback in field_fallbacks.items():
        value = str(coerced.get(key, "")).strip()
        if (not value) or detect_language(value) == "ja" or not any(ch.isascii() and ch.isalpha() for ch in value):
            coerced[key] = fallback
    if not str(coerced.get("text_query", "")).strip():
        # 形式が崩れた場合でも全文検索用の最小クエリを維持する。
        coerced["text_query"] = " ".join(tokenize(question)[:8]) or text_query
    raw_entities = coerced.get("graph_seed_entities")
    entities: list[str] = []
    if isinstance(raw_entities, list):
        for entity in raw_entities:
            value = str(entity).strip()
            if not value:
                continue
            if detect_language(value) == "ja":
                continue
            if not any(ch.isascii() and ch.isalpha() for ch in value):
                continue
            entities.append(value)
    if not entities:
        entities = _english_graph_seeds(question, str(coerced.get("sub_question", sub_question)))
    coerced["graph_seed_entities"] = entities[:5]
    return coerced


class SearchStrategy(Protocol):
    def propose_next_actions(self, state: SearchState) -> list[SearchAction]:
        ...

    def score_action(self, action: SearchAction, state: SearchState) -> float:
        ...

    def select_actions(self, actions: list[SearchAction], budget: SearchBudget, state: SearchState) -> list[SearchAction]:
        ...


class SocraticSearchStrategy:
    def __init__(self, llm: PlannerAndWriter, config: RagConfig) -> None:
        self.llm = llm
        self.config = config

    def propose_next_actions(self, state: SearchState) -> list[SearchAction]:
        planned = self.llm.plan(
            state.question,
            state.notes,
            state.open_questions,
            state.round,
            self.config.max_sub_questions_per_round,
        )
        question_language = detect_language(state.question)
        actions: list[SearchAction] = []
        for item in planned:
            item = _coerce_action_language(item, state.question, question_language)
            actions.append(
                SearchAction(
                    action_id=new_id("act"),
                    sub_question=str(item["sub_question"]),
                    vector_query=str(item["vector_query"]),
                    text_query=str(item["text_query"]),
                    graph_seed_entities=list(item.get("graph_seed_entities", [])),
                    expected_gain=float(item.get("priority", 1)),
                    cost_estimate=1.0,
                    priority=int(item.get("priority", 1)),
                )
            )
        return actions

    def score_action(self, action: SearchAction, state: SearchState) -> float:
        previous_penalty = max((overlap_score(action.text_query, query) for query in state.previous_queries), default=0.0)
        open_question_bonus = max((overlap_score(action.sub_question, item.question) for item in state.open_questions), default=0.2)
        return action.priority + open_question_bonus + action.expected_gain - previous_penalty - action.cost_estimate * 0.05

    def select_actions(self, actions: list[SearchAction], budget: SearchBudget, state: SearchState) -> list[SearchAction]:
        return sorted(actions, key=lambda action: self.score_action(action, state), reverse=True)[: budget.max_actions]


class MctsSearchStrategy(SocraticSearchStrategy):
    def score_action(self, action: SearchAction, state: SearchState) -> float:
        base = super().score_action(action, state)
        diversity = 1.0 - max((overlap_score(action.text_query, query) for query in state.previous_queries), default=0.0)
        evidence_cost = min(1.0, action.cost_estimate / 10.0)
        return base + 0.10 * diversity - 0.10 * evidence_cost
