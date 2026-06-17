from dataclasses import dataclass

from state_aware_rag.language import LanguageTag, detect_language


@dataclass(frozen=True)
class RagConfig:
    max_rounds: int = 3
    max_sub_questions_per_round: int = 3
    vector_top_k: int = 20
    text_top_k: int = 20
    graph_top_k: int = 20
    max_accepted_evidence_per_round: int = 10
    relevance_threshold: float = 0.70
    memory_value_threshold: float = 0.60
    relevance_threshold_ja: float = 0.35
    memory_value_threshold_ja: float = 0.55
    relevance_threshold_mixed: float = 0.50
    memory_value_threshold_mixed: float = 0.55
    relevance_threshold_other: float = 0.55
    memory_value_threshold_other: float = 0.55
    duplicate_threshold: float = 0.80
    conflict_threshold: float = 0.70
    no_new_note_limit: int = 1
    low_gain_limit: int = 2
    embedding_dimensions: int = 128
    embedding_backend: str = "ruri"
    chunker_backend: str = "auto"
    fulltext_normalize: bool = True
    run_log_enabled: bool = True

    def scoring_thresholds(self, question: str) -> tuple[float, float, LanguageTag]:
        """質問の主要言語に応じた Bosun 採用閾値を返す。"""
        language = detect_language(question)
        if language == "ja":
            return self.relevance_threshold_ja, self.memory_value_threshold_ja, language
        if language == "mixed":
            return self.relevance_threshold_mixed, self.memory_value_threshold_mixed, language
        if language == "other":
            return self.relevance_threshold_other, self.memory_value_threshold_other, language
        return self.relevance_threshold, self.memory_value_threshold, language
