from state_aware_rag.bosun import (
    BosunXSServingConfig,
    NativeBosunXSConfig,
    NativeBosunXSScorer,
    build_pair_document,
)


def test_bosun_xs_pair_document_uses_ordered_findings() -> None:
    document = build_pair_document("MemoryNote A", "Candidate B")

    assert document == "FINDING A:\nMemoryNote A\n\nFINDING B:\nCandidate B"


def test_native_bosun_xs_prompt_uses_serving_template() -> None:
    serving = BosunXSServingConfig(prefix="PREFIX:", suffix=":SUFFIX", yes_id=9693, no_id=2152, max_len=3072)
    scorer = NativeBosunXSScorer(NativeBosunXSConfig(serving=serving))

    prompt = scorer._prompt("rule", "query", "doc")

    assert prompt == "PREFIX:<Instruct>: rule\n<Query>: query\n<Document>: doc:SUFFIX"
    assert "<Instruct>: rule\n<Query>: query\n<Document>: doc" in prompt


def test_native_bosun_xs_uses_official_yes_no_ids_by_default() -> None:
    scorer = NativeBosunXSScorer()

    assert scorer.serving.yes_id == 9693
    assert scorer.serving.no_id == 2152
    assert scorer.serving.max_len == 3072


def test_native_bosun_xs_default_repos() -> None:
    config = NativeBosunXSConfig()

    assert config.repo == "Hanno-Labs/bosun-xs"
    assert config.base_model == "Qwen/Qwen3-Reranker-0.6B"
