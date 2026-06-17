from __future__ import annotations

import re
from typing import Literal

from state_aware_rag.text import JP_CHAR_RE

LanguageTag = Literal["ja", "en", "mixed", "other"]

_LATIN_RE = re.compile(r"[A-Za-z]")


def detect_language(text: str) -> LanguageTag:
    """Unicode スクリプト比率から質問の主要言語を推定する。

    Bosun XS は英語科学文と日本語文書でスコア分布が異なるため、
    閾値切り替えの入力として使う。
    """
    if not text.strip():
        return "other"
    ja_chars = len(JP_CHAR_RE.findall(text))
    en_chars = len(_LATIN_RE.findall(text))
    total = ja_chars + en_chars
    if total == 0:
        return "other"
    if ja_chars >= 5 or ja_chars / total >= 0.30:
        if en_chars > ja_chars * 2:
            return "mixed"
        return "ja"
    if en_chars > 0:
        return "en"
    return "other"
