import json

import pytest

from state_aware_rag.json_parse import extract_json_object, parse_json_object


def test_parse_valid_plan_json() -> None:
    text = """
{
  "sub_questions": [
    {
      "sub_question": "What is X?",
      "why_needed": "needed",
      "vector_query": "meaning of X",
      "text_query": "X definition",
      "graph_seed_entities": ["Entity"],
      "priority": 1
    }
  ]
}
"""
    data = parse_json_object(text)
    assert len(data["sub_questions"]) == 1
    assert data["sub_questions"][0]["sub_question"] == "What is X?"


def test_parse_trailing_comma() -> None:
    text = '{"notes": [{"claim": "fact", "confidence": 0.8,}],}'
    data = parse_json_object(text)
    assert data["notes"][0]["claim"] == "fact"


def test_parse_broken_string_with_internal_quote() -> None:
    # 文字列内の未エスケープ引用符で json.loads が失敗するケース
    text = """
{
  "sub_questions": [
    {
      "sub_question": "Does "alpha" thalassemia increase risk?",
      "why_needed": "clarify mechanism",
      "vector_query": "alpha thalassemia anemia risk",
      "text_query": "alpha thalassemia",
      "graph_seed_entities": ["alpha thalassemia"],
      "priority": 1
    }
  ]
}
"""
    data = parse_json_object(text)
    assert data["sub_questions"][0]["vector_query"] == "alpha thalassemia anemia risk"


def test_parse_notes_and_open_questions_heuristic() -> None:
    text = """
{
  "notes": [
    {
      "claim": "ADAR1 forms a complex with Dicer",
      "note_type": "fact",
      "supported_by_evidence_ids": ["ev_001"],
      "confidence": 0.85,
    }
  ],
  "open_questions": [
    {
      "question": "Does ADAR1 cleave pre-miRNA?",
      "reason": "binding alone is insufficient",
    }
  ]
}
"""
    data = parse_json_object(text)
    assert data["notes"][0]["claim"] == "ADAR1 forms a complex with Dicer"
    assert data["open_questions"][0]["question"] == "Does ADAR1 cleave pre-miRNA?"


def test_parse_code_fence() -> None:
    text = """```json
{"notes": [], "open_questions": []}
```"""
    data = parse_json_object(text)
    assert data == {"notes": [], "open_questions": []}


def test_extract_json_object_returns_none_on_garbage() -> None:
    assert extract_json_object("not json at all") is None


def test_parse_raises_on_empty() -> None:
    with pytest.raises(json.JSONDecodeError):
        parse_json_object("")
