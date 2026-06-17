from __future__ import annotations

import re
from typing import Protocol


_JP_CHAR_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uff00-\uffef]")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？\.\!\?])")


class Chunker(Protocol):
    def chunk(self, body: str, chunk_chars: int, overlap_chars: int) -> list[str]: ...


def _has_japanese(text: str) -> bool:
    return bool(_JP_CHAR_RE.search(text))


def _char_chunk(text: str, chunk_chars: int, overlap_chars: int) -> list[str]:
    collapsed = " ".join(text.split())
    if not collapsed:
        raise ValueError("Document body must not be empty")
    if chunk_chars <= 0 or len(collapsed) <= chunk_chars:
        return [collapsed]
    overlap = max(0, min(overlap_chars, chunk_chars - 1))
    chunks: list[str] = []
    start = 0
    while start < len(collapsed):
        end = min(len(collapsed), start + chunk_chars)
        piece = collapsed[start:end].strip()
        if piece:
            chunks.append(piece)
        if end == len(collapsed):
            break
        start = max(end - overlap, start + 1)
    return chunks


def _split_sentences_jp(text: str) -> list[str]:
    parts: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        for sentence in _SENTENCE_SPLIT_RE.split(stripped):
            sentence = sentence.strip()
            if sentence:
                parts.append(sentence)
    return parts


def _group_phrases(
    phrases: list[str],
    chunk_chars: int,
    overlap_chars: int,
) -> list[str]:
    if not phrases:
        return []
    overlap = max(0, min(overlap_chars, max(chunk_chars - 1, 0)))
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for phrase in phrases:
        phrase_len = len(phrase)
        if current and current_len + phrase_len > chunk_chars:
            chunks.append("".join(current).strip())
            tail: list[str] = []
            tail_len = 0
            for previous in reversed(current):
                if tail_len + len(previous) > overlap:
                    break
                tail.insert(0, previous)
                tail_len += len(previous)
            current = tail
            current_len = tail_len
        current.append(phrase)
        current_len += phrase_len
    if current:
        tail_text = "".join(current).strip()
        if tail_text:
            chunks.append(tail_text)
    return chunks


class CharChunker:
    def chunk(self, body: str, chunk_chars: int, overlap_chars: int) -> list[str]:
        return _char_chunk(body, chunk_chars, overlap_chars)


class BudouxChunker:
    """budoux で日本語を文節単位に切り、greedy にチャンクへまとめる。

    - 日本語を含まない本文は CharChunker と同じ文字数ベース分割に委譲する。
    - チャンク末尾の文節を `overlap_chars` 分まで次チャンク先頭に引き継ぐ。
    """

    def __init__(self) -> None:
        self._parser: object | None = None

    def _ensure_parser(self) -> object:
        if self._parser is None:
            import budoux

            self._parser = budoux.load_default_japanese_parser()
        return self._parser

    def chunk(self, body: str, chunk_chars: int, overlap_chars: int) -> list[str]:
        if not body or not body.strip():
            raise ValueError("Document body must not be empty")
        if chunk_chars <= 0:
            return [body.strip()]
        if not _has_japanese(body):
            return _char_chunk(body, chunk_chars, overlap_chars)

        normalized = re.sub(r"[ \t]+", " ", body).strip()
        if len(normalized) <= chunk_chars:
            return [normalized]

        sentences = _split_sentences_jp(normalized) or [normalized]
        parser = self._ensure_parser()
        phrases: list[str] = []
        for sentence in sentences:
            phrases.extend(parser.parse(sentence))  # type: ignore[attr-defined]

        chunks = _group_phrases(phrases, chunk_chars, overlap_chars)
        return chunks or [normalized]


def default_chunker() -> Chunker:
    try:
        import budoux  # noqa: F401

        return BudouxChunker()
    except Exception:
        return CharChunker()


def build_chunker(backend: str) -> Chunker:
    name = (backend or "").strip().lower()
    if name in {"", "auto", "default"}:
        return default_chunker()
    if name == "budoux":
        return BudouxChunker()
    if name in {"char", "char_window", "fixed"}:
        return CharChunker()
    raise ValueError(f"Unknown chunker backend: {backend}")
