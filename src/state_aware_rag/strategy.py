from __future__ import annotations

from typing import Protocol

from state_aware_rag.config import RagConfig
from state_aware_rag.llm import PlannerAndWriter
from state_aware_rag.models import SearchAction, SearchBudget, SearchState, new_id
from state_aware_rag.text import overlap_score


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
        actions: list[SearchAction] = []
        for item in planned:
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
