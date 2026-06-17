from __future__ import annotations

import pytest

from state_aware_rag.chunking import (
    BudouxChunker,
    CharChunker,
    build_chunker,
    default_chunker,
)


def test_default_chunker_returns_budoux_when_available() -> None:
    chunker = default_chunker()

    assert isinstance(chunker, BudouxChunker)


def test_char_chunker_collapses_whitespace_and_respects_overlap() -> None:
    chunker = CharChunker()
    body = "abcdefghij " * 20

    chunks = chunker.chunk(body, chunk_chars=50, overlap_chars=10)

    assert len(chunks) >= 4
    assert all(len(chunk) <= 50 for chunk in chunks)


def test_char_chunker_rejects_empty_body() -> None:
    chunker = CharChunker()

    with pytest.raises(ValueError):
        chunker.chunk("   ", chunk_chars=50, overlap_chars=10)


def test_budoux_chunker_splits_japanese_at_phrase_boundaries() -> None:
    import budoux

    chunker = BudouxChunker()
    body = (
        "山田太郎は2020年からABC株式会社で働いている。"
        "ABC株式会社は東京に本社を構えている。"
        "山田太郎は営業部の部長を務めている。"
    )

    chunks = chunker.chunk(body, chunk_chars=30, overlap_chars=8)

    assert len(chunks) >= 2
    assert all(len(chunk) <= 30 + 8 for chunk in chunks), chunks
    rejoined = "".join(chunks)
    assert "山田太郎" in rejoined
    assert "ABC株式会社" in rejoined

    parser = budoux.load_default_japanese_parser()
    sentences = body.replace("。", "。\n").splitlines()
    valid_starts = set()
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        valid_starts.update(parser.parse(sentence))
    for chunk in chunks:
        assert any(chunk.startswith(phrase) for phrase in valid_starts), chunk


def test_budoux_chunker_returns_whole_text_when_short() -> None:
    chunker = BudouxChunker()
    body = "短い日本語の文。"

    chunks = chunker.chunk(body, chunk_chars=200, overlap_chars=10)

    assert chunks == [body]


def test_budoux_chunker_falls_back_to_char_chunking_for_english() -> None:
    chunker = BudouxChunker()
    body = "State-Aware RAG keeps a working memory for each question. " * 10

    chunks = chunker.chunk(body, chunk_chars=80, overlap_chars=20)

    assert len(chunks) >= 2
    assert all(len(chunk) <= 80 for chunk in chunks)


def test_build_chunker_dispatches_by_name() -> None:
    assert isinstance(build_chunker("budoux"), BudouxChunker)
    assert isinstance(build_chunker("char"), CharChunker)
    assert isinstance(build_chunker("auto"), BudouxChunker)


def test_build_chunker_rejects_unknown_backend() -> None:
    with pytest.raises(ValueError):
        build_chunker("totally_unknown")
