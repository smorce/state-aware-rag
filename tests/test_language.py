from state_aware_rag.config import RagConfig
from state_aware_rag.language import detect_language


def test_detect_language_ja() -> None:
    assert detect_language("State-Aware RAG が最終回答に使うのは何ですか？") == "ja"


def test_detect_language_en() -> None:
    assert detect_language("0-dimensional biomaterials show inductive properties.") == "en"


def test_detect_language_mixed() -> None:
    assert detect_language("State-Aware RAG と working memory について") == "mixed"


def test_scoring_thresholds_ja() -> None:
    config = RagConfig()
    rel, mem, lang = config.scoring_thresholds("作業用メモとは？")
    assert lang == "ja"
    assert rel == config.relevance_threshold_ja
    assert mem == config.memory_value_threshold_ja


def test_scoring_thresholds_en() -> None:
    config = RagConfig()
    rel, mem, lang = config.scoring_thresholds("What is working memory?")
    assert lang == "en"
    assert rel == config.relevance_threshold
    assert mem == config.memory_value_threshold
