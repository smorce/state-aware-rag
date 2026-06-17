"""llama-server 由来の壊れた JSON を段階的に復元する。"""

from __future__ import annotations

import json
import re
from typing import Any, Final

# LLM 応答でよく使うキー
_STRING_KEYS: Final[frozenset[str]] = frozenset(
    {
        "sub_question",
        "why_needed",
        "vector_query",
        "text_query",
        "claim",
        "note_type",
        "question",
        "reason",
        "from",
        "to",
        "relation_type",
        "evidence_text",
        "canonical_name",
        "entity_type",
    }
)
_STRING_ARRAY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "graph_seed_entities",
        "supported_by_evidence_ids",
        "aliases",
    }
)
_OBJECT_ARRAY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "sub_questions",
        "notes",
        "open_questions",
        "entities",
        "relations",
    }
)
_NUMBER_KEYS: Final[frozenset[str]] = frozenset({"priority", "confidence"})
_OBJECT_KEYS: Final[frozenset[str]] = frozenset({"attributes"})
_KNOWN_KEYS: Final[frozenset[str]] = _STRING_KEYS | _STRING_ARRAY_KEYS | _NUMBER_KEYS | _OBJECT_KEYS | _OBJECT_ARRAY_KEYS

_FIELD_PATTERN: Final[re.Pattern[str]] = re.compile(
    r'"(?P<key>' + "|".join(sorted(_KNOWN_KEYS, key=len, reverse=True)) + r')"\s*:'
)
_KEY_PATTERN: Final[re.Pattern[str]] = _FIELD_PATTERN


def skip_ws(s: str, i: int) -> int:
    """空白文字を読み飛ばし、次の文字位置を返す。"""
    while i < len(s) and s[i].isspace():
        i += 1
    return i


def is_escaped(s: str, index: int, floor: int = 0) -> bool:
    """s[index] の文字が、直前のバックスラッシュによってエスケープされているか判定する。"""
    backslash_count = 0
    j = index - 1
    while j >= floor and s[j] == "\\":
        backslash_count += 1
        j -= 1
    return backslash_count % 2 == 1


def find_string_end_heuristic(
    s: str,
    start_index: int,
    end_followers: frozenset[str] = frozenset({",", "}", "]", ":"}),
) -> int:
    """
    壊れた JSON 風文字列の中で、文字列の終了とみなせるダブルクォート位置を返す。

    終了条件:
    - ダブルクォートがエスケープされていない
    - その直後の空白を飛ばした先が、カンマ・閉じ括弧・文字列末尾のいずれか
    """
    if start_index >= len(s) or s[start_index] != '"':
        return -1

    i = start_index + 1
    while i < len(s):
        if s[i] == '"' and not is_escaped(s, i, start_index):
            k = skip_ws(s, i + 1)
            if k >= len(s) or s[k] in end_followers:
                return i
        i += 1
    return -1


def previous_non_ws(s: str, i: int) -> str | None:
    """指定位置の直前にある、空白以外の文字を返す。"""
    j = i - 1
    while j >= 0 and s[j].isspace():
        j -= 1
    return s[j] if j >= 0 else None


def looks_like_object_key(s: str, key_start: int) -> bool:
    """
    見つかったキーがオブジェクトのキーらしいか判定する。

    直前の非空白文字が { または , の場合だけキー候補にする。
    """
    return previous_non_ws(s, key_start) in (None, "{", ",")


def extract_string_value(s: str, value_start: int) -> tuple[str, int] | None:
    """value_start 以降から、ダブルクォートで始まる文字列値を抽出する。"""
    i = skip_ws(s, value_start)
    if i >= len(s) or s[i] != '"':
        return None
    end = find_string_end_heuristic(s, i)
    if end == -1:
        return None
    return s[i + 1 : end], end + 1


def extract_string_array_values(s: str, value_start: int) -> tuple[list[str], int] | None:
    """
    value_start 以降から、文字列配列の中身を抽出する。

    単純に s.find("]") は使わず、文字列内の ] は配列の終わりとして扱わない。
    """
    i = skip_ws(s, value_start)
    if i >= len(s) or s[i] != "[":
        return None

    i += 1
    values: list[str] = []
    while i < len(s):
        i = skip_ws(s, i)
        if i >= len(s):
            return values, i
        if s[i] == "]":
            return values, i + 1
        if s[i] == '"':
            end = find_string_end_heuristic(s, i)
            if end == -1:
                return values, i + 1
            values.append(s[i + 1 : end])
            i = end + 1
            continue
        i += 1
    return values, i


def extract_number_value(s: str, value_start: int) -> tuple[float, int] | None:
    """value_start 以降から数値リテラルを抽出する。"""
    i = skip_ws(s, value_start)
    if i >= len(s):
        return None
    match = re.match(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", s[i:])
    if match is None:
        return None
    raw = match.group(0)
    return float(raw), i + len(raw)


def extract_object_value(s: str, value_start: int) -> tuple[dict[str, Any], int] | None:
    """value_start 以降からネストしたオブジェクトを抽出する。"""
    i = skip_ws(s, value_start)
    if i >= len(s) or s[i] != "{":
        return None
    end = _find_balanced_brace_end(s, i)
    if end == -1:
        return None
    fragment = s[i : end + 1]
    try:
        parsed = json.loads(_repair_trailing_commas(fragment))
    except json.JSONDecodeError:
        parsed = _heuristic_extract_fields(fragment)
    if not isinstance(parsed, dict):
        return None
    return parsed, end + 1


def extract_object_array_values(s: str, value_start: int) -> tuple[list[dict[str, Any]], int] | None:
    """value_start 以降からオブジェクト配列を抽出する。"""
    i = skip_ws(s, value_start)
    if i >= len(s) or s[i] != "[":
        return None

    i += 1
    objects: list[dict[str, Any]] = []
    while i < len(s):
        i = skip_ws(s, i)
        if i >= len(s):
            return objects, i
        if s[i] == "]":
            return objects, i + 1
        if s[i] == "{":
            end = _find_balanced_brace_end(s, i)
            if end == -1:
                return objects, i + 1
            fragment = s[i : end + 1]
            try:
                parsed = json.loads(_repair_trailing_commas(fragment))
            except json.JSONDecodeError:
                parsed = _heuristic_extract_fields(fragment)
            if isinstance(parsed, dict) and parsed:
                objects.append(parsed)
            i = end + 1
            continue
        i += 1
    return objects, i


def _heuristic_extract_fields(s: str) -> dict[str, Any]:
    """JSON 風文字列から既知キーの値を抽出してオブジェクトを組み立てる。"""
    result: dict[str, Any] = {}
    i = 0
    while True:
        match = _FIELD_PATTERN.search(s, i)
        if match is None:
            break
        key = match.group("key")
        if not looks_like_object_key(s, match.start()):
            i = match.end()
            continue

        if key in _STRING_KEYS:
            extracted = extract_string_value(s, match.end())
            if extracted is not None:
                value, next_i = extracted
                result[key] = value
                i = max(next_i, match.end())
            else:
                i = match.end()
        elif key in _STRING_ARRAY_KEYS:
            extracted = extract_string_array_values(s, match.end())
            if extracted is not None:
                values, next_i = extracted
                result[key] = values
                i = max(next_i, match.end())
            else:
                i = match.end()
        elif key in _NUMBER_KEYS:
            extracted = extract_number_value(s, match.end())
            if extracted is not None:
                value, next_i = extracted
                result[key] = value
                i = max(next_i, match.end())
            else:
                i = match.end()
        elif key in _OBJECT_KEYS:
            extracted = extract_object_value(s, match.end())
            if extracted is not None:
                value, next_i = extracted
                result[key] = value
                i = max(next_i, match.end())
            else:
                i = match.end()
        elif key in _OBJECT_ARRAY_KEYS:
            extracted = extract_object_array_values(s, match.end())
            if extracted is not None:
                values, next_i = extracted
                result[key] = values
                i = max(next_i, match.end())
            else:
                i = match.end()
        else:
            i = match.end()
    return result


def _heuristic_extract_object(s: str) -> dict[str, Any]:
    """トップレベルオブジェクトをヒューリスティックに復元する。"""
    return _heuristic_extract_fields(s)


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) < 2:
        return stripped
    end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
    return "\n".join(lines[1:end]).strip()


def _repair_trailing_commas(text: str) -> str:
    return re.sub(r",\s*([}\]])", r"\1", text)


def _find_balanced_brace_end(s: str, start: int) -> int:
    """{ から始まるオブジェクトの閉じ } 位置を返す（文字列内の括弧は無視）。"""
    if start >= len(s) or s[start] != "{":
        return -1
    depth = 0
    i = start
    while i < len(s):
        if s[i] == '"':
            end = find_string_end_heuristic(s, i)
            if end == -1:
                return -1
            i = end + 1
            continue
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _extract_balanced_object(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    if start == -1:
        return None
    end = _find_balanced_brace_end(text, start)
    if end == -1:
        return None
    raw = text[start : end + 1]
    repaired = _repair_trailing_commas(raw)
    try:
        parsed = json.loads(repaired)
    except json.JSONDecodeError:
        parsed = _heuristic_extract_fields(repaired)
    return parsed if isinstance(parsed, dict) else None


def parse_json_object(text: str) -> dict[str, Any]:
    """厳密パース → 括弧抽出 → ヒューリスティック復元の順で JSON オブジェクトを得る。"""
    cleaned = _strip_code_fence(text)
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    repaired = _repair_trailing_commas(cleaned)
    try:
        parsed = json.loads(repaired)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    extracted = _extract_balanced_object(cleaned)
    if extracted:
        return extracted

    heuristic = _heuristic_extract_object(cleaned)
    if heuristic:
        return heuristic

    raise json.JSONDecodeError("Could not parse JSON object", cleaned, 0)


def extract_json_object(text: str) -> dict[str, Any] | None:
    """壊れた JSON からオブジェクトを取り出す。失敗時は None。"""
    try:
        return parse_json_object(text)
    except json.JSONDecodeError:
        return None
