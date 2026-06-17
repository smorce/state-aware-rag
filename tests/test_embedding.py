from __future__ import annotations

import importlib.util
import math

import pytest

from state_aware_rag.embedding import (
    HashedEmbedder,
    RuriEmbedder,
    build_embedder,
    default_embedder,
)


def test_hashed_embedder_dimensions_match_request() -> None:
    embedder = HashedEmbedder(dimensions=64)

    assert embedder.dimensions == 64
    assert len(embedder.embed_query("hello")) == 64
    assert len(embedder.embed_documents(["a", "b"])) == 2
    assert len(embedder.embed_documents(["a"])[0]) == 64


def test_hashed_embedder_is_deterministic_and_unit_length() -> None:
    embedder = HashedEmbedder(dimensions=128)

    a = embedder.embed_query("State-Aware RAG keeps a working memory.")
    b = embedder.embed_query("State-Aware RAG keeps a working memory.")
    norm = math.sqrt(sum(value * value for value in a))

    assert a == b
    assert math.isclose(norm, 1.0, abs_tol=1e-9)


def test_hashed_embedder_rejects_invalid_dimensions() -> None:
    with pytest.raises(ValueError):
        HashedEmbedder(dimensions=0)


def test_default_embedder_honors_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBEDDING_BACKEND", "hashed")
    embedder = default_embedder(dimensions=32)

    assert isinstance(embedder, HashedEmbedder)
    assert embedder.dimensions == 32


def test_default_embedder_uses_ruri_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EMBEDDING_BACKEND", raising=False)
    embedder = default_embedder()

    assert isinstance(embedder, RuriEmbedder)


def test_build_embedder_dispatches_by_name() -> None:
    assert isinstance(build_embedder("hashed", dimensions=128), HashedEmbedder)
    assert isinstance(build_embedder("auto", dimensions=128), RuriEmbedder)
    assert isinstance(build_embedder("ruri", dimensions=128), RuriEmbedder)


def test_build_embedder_rejects_unknown_backend() -> None:
    with pytest.raises(ValueError):
        build_embedder("totally_unknown")


@pytest.mark.skipif(
    importlib.util.find_spec("sentence_transformers") is None,
    reason="sentence-transformers is an optional dependency; install with .[ruri] to run.",
)
def test_ruri_embedder_loads_when_dependency_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RURI_DEVICE", "cpu")
    embedder = RuriEmbedder()
    vector = embedder.embed_query("State-Aware RAG")

    assert isinstance(vector, list)
    assert embedder.dimensions == len(vector)
    assert all(isinstance(value, float) for value in vector)
