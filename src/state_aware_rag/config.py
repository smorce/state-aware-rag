from dataclasses import dataclass


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
    duplicate_threshold: float = 0.80
    conflict_threshold: float = 0.70
    no_new_note_limit: int = 1
    low_gain_limit: int = 2
    embedding_dimensions: int = 128
    embedding_backend: str = "ruri"
    chunker_backend: str = "auto"
    fulltext_normalize: bool = True
