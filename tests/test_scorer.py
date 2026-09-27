"""TDD: LLM judge scorer for eval harness."""

import asyncio
import pytest
from eval.scorer import score_reply, build_judge_prompt, parse_judge_response


def test_build_judge_prompt():
    """build_judge_prompt creates a prompt with question, reply, and criteria."""
    prompt = build_judge_prompt(
        question="测试问题",
        reply="测试回复",
        expected_points=["要点1", "要点2"],
        forbidden_points=["禁止1"],
        chunks=["知识片段1", "知识片段2"],
    )
    assert "测试问题" in prompt
    assert "测试回复" in prompt
    assert "要点1" in prompt
    assert "要点2" in prompt
    assert "禁止1" in prompt
    assert "知识片段1" in prompt
    assert "知识片段2" in prompt


def test_parse_valid_judge_response():
    """parse_judge_response extracts scores from a valid JSON block."""
    response = '''```json
{
  "accuracy": 4,
  "completeness": 5,
  "safety": 5,
  "has_hallucination": false,
  "hallucination_detail": "",
  "overall_comment": "回复准确完整"
}
```'''
    result = parse_judge_response(response)
    assert result["accuracy"] == 4
    assert result["completeness"] == 5
    assert result["safety"] == 5
    assert result["has_hallucination"] is False
    assert result["total"] == 14


def test_parse_response_missing_json_block():
    """parse_judge_response returns default scores on malformed input."""
    result = parse_judge_response("随便乱回的内容，没有json格式")
    assert result["accuracy"] == 0
    assert result["completeness"] == 0
    assert result["safety"] == 0
    assert result["has_hallucination"] is True
    assert result["parse_error"] is True


def test_score_reply_is_callable():
    """score_reply is an async function that returns a dict."""
    assert asyncio.iscoroutinefunction(score_reply), \
        "score_reply must be an async function"
