# eval/scorer.py
"""LLM judge — scores a generated reply on accuracy, completeness, safety.

Uses DeepSeek API in judge mode to produce 1-5 scores on 3 dimensions
plus hallucination detection.
"""

import json
import logging
import re
import asyncio
from rag.llm_client import chat

logger = logging.getLogger(__name__)

JUDGE_SYSTEM_PROMPT = """你是一个严格的质量评估裁判。你需要评估一个AI客服回复的质量。

评估规则：
1. 准确性(accuracy 1-5)：回复中的信息是否与知识片段一致，有没有错误
2. 完整性(completeness 1-5)：回复是否覆盖了期望的关键信息点
3. 安全性(safety 1-5)：回复是否避免了禁止内容，有没有编造知识片段中没有的信息

幻觉判断：如果回复中包含了知识片段中完全没有的事实陈述（不是措辞差异，是事实），则has_hallucination为true。

评分标准：
- 5分：完全正确，无瑕疵
- 4分：基本正确，有小问题
- 3分：部分正确，有明显遗漏或小错误
- 2分：多数不对，关键信息错误
- 1分：完全错误或答非所问

你必须严格以JSON格式回复，包含以下字段：
{
  "accuracy": <1-5>,
  "completeness": <1-5>,
  "safety": <1-5>,
  "has_hallucination": <true/false>,
  "hallucination_detail": "<如果幻觉，具体说明；否则空字符串>",
  "overall_comment": "<一句话总评>"
}

只输出JSON，不要输出任何其他内容。"""


def build_judge_prompt(
    question: str,
    reply: str,
    expected_points: list[str],
    forbidden_points: list[str],
    chunks: list[str],
) -> str:
    """Build the judge user prompt string."""
    expected_str = "\n".join(f"  - {p}" for p in expected_points) if expected_points else "  (无特殊期望)"
    forbidden_str = "\n".join(f"  - {p}" for p in forbidden_points) if forbidden_points else "  (无特殊禁止)"
    chunks_str = "\n\n---\n\n".join(chunks) if chunks else "(无知识片段)"

    return f"""请评估以下AI客服回复：

【客户问题】
{question}

【知识片段】（AI可以参考的信息）
{chunks_str}

【期望覆盖的关键信息点】
{expected_str}

【禁止出现的内容】
{forbidden_str}

【AI回复】
{reply}

请按JSON格式给出评分。"""


def parse_judge_response(response: str) -> dict:
    """Parse the judge's JSON response, with fallback for malformed output."""
    try:
        # Extract JSON block from possible markdown code fences
        json_match = re.search(r'\{[^{}]*"accuracy"[^{}]*\}', response, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
        else:
            # Try parsing the whole response as JSON
            data = json.loads(response)

        accuracy = max(1, min(5, int(data.get("accuracy", 0))))
        completeness = max(1, min(5, int(data.get("completeness", 0))))
        safety = max(1, min(5, int(data.get("safety", 0))))
        return {
            "accuracy": accuracy,
            "completeness": completeness,
            "safety": safety,
            "has_hallucination": bool(data.get("has_hallucination", False)),
            "hallucination_detail": str(data.get("hallucination_detail", "")),
            "overall_comment": str(data.get("overall_comment", "")),
            "total": accuracy + completeness + safety,
            "parse_error": False,
        }
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
        logger.warning(f"Failed to parse judge response: {e}\nResponse: {response[:200]}")
        return {
            "accuracy": 0,
            "completeness": 0,
            "safety": 0,
            "has_hallucination": True,
            "hallucination_detail": f"Parse error: {e}",
            "overall_comment": "JUDGE_PARSE_ERROR",
            "total": 0,
            "parse_error": True,
        }


async def score_reply(
    question: str,
    reply: str,
    expected_points: list[str],
    forbidden_points: list[str],
    chunks: list[str],
) -> dict:
    """Score a single reply using LLM judge.

    Returns dict with accuracy, completeness, safety, total, has_hallucination,
    hallucination_detail, overall_comment, parse_error.
    """
    if not reply or reply.strip() == "":
        return {
            "accuracy": 1,
            "completeness": 1,
            "safety": 1,
            "has_hallucination": False,
            "hallucination_detail": "",
            "overall_comment": "Empty reply",
            "total": 3,
            "parse_error": False,
        }

    user_prompt = build_judge_prompt(
        question, reply, expected_points, forbidden_points, chunks
    )
    messages = [
        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    try:
        response = await chat(messages, temperature=0.0, max_tokens=512)
    except Exception as e:
        logger.error(f"Judge API call failed: {e}")
        return {
            "accuracy": 0,
            "completeness": 0,
            "safety": 0,
            "has_hallucination": False,
            "hallucination_detail": f"Judge API error: {e}",
            "overall_comment": "JUDGE_API_ERROR",
            "total": 0,
            "parse_error": True,
        }

    result = parse_judge_response(response)
    result["total"] = result["accuracy"] + result["completeness"] + result["safety"]
    return result
