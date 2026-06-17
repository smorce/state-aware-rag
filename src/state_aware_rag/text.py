from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import Iterable


WORD_RE = re.compile(r"[A-Za-z0-9_]+|[\u3040-\u30ff\u3400-\u9fff]+")
SENTENCE_RE = re.compile(r"(?<=[.!?。！？])\s+")


def tokenize(text: str) -> list[str]:
    return [part.lower() for part in WORD_RE.findall(text)]


def split_sentences(text: str) -> list[str]:
    return [part.strip() for part in SENTENCE_RE.split(text.strip()) if part.strip()]


def normalize_claim(text: str) -> str:
    return " ".join(tokenize(text))


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def hashed_embedding(text: str, dimensions: int = 128) -> list[float]:
    vector = [0.0] * dimensions
    for token in tokenize(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return vector
    return [value / norm for value in vector]


def overlap_score(left: str | Iterable[str], right: str | Iterable[str]) -> float:
    left_tokens = set(tokenize(left) if isinstance(left, str) else [x.lower() for x in left])
    right_tokens = set(tokenize(right) if isinstance(right, str) else [x.lower() for x in right])
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def bm25_like_score(query: str, body: str) -> float:
    q = Counter(tokenize(query))
    b = Counter(tokenize(body))
    if not q or not b:
        return 0.0
    score = 0.0
    body_len = max(1, sum(b.values()))
    for token, q_count in q.items():
        tf = b.get(token, 0)
        if tf:
            score += (tf * (1.5 + 1.0)) / (tf + 1.5 * (0.25 + 0.75 * body_len / 80.0)) * q_count
    return score


def extract_entities(text: str, max_entities: int = 8) -> list[str]:
    candidates: list[str] = []
    for match in re.finditer(r"\b[A-Z][A-Za-z0-9_+-]{2,}\b|[\u3400-\u9fff]{2,}", text):
        value = match.group(0).strip()
        if value and value.lower() not in {"the", "and", "for"} and value not in candidates:
            candidates.append(value)
        if len(candidates) >= max_entities:
            break
    return candidates


def compact_fact(sentence: str, max_words: int = 18) -> str:
    sentence = sentence.strip().rstrip(".。")
    words = sentence.split()
    if len(words) <= max_words:
        return sentence
    return " ".join(words[:max_words]).rstrip(",;:") + "..."

