"""Generate Q&A pairs from video transcripts using LLM.

Takes raw transcript text from Whisper, sends to DeepSeek LLM to generate
structured Q&A pairs suitable for knowledge base import.
"""
import json
import logging
import os

from openai import OpenAI

logger = logging.getLogger(__name__)

QA_GENERATION_PROMPT = """你是一个专业的培训内容整理助手。请根据以下视频转录文字，提取出所有有价值的问答对。

要求：
1. 每个问答对包含一个"q"（问题）和"a"（答案）
2. 问题应该是客户或学员可能会问的自然问题
3. 答案要基于转录内容，准确完整
4. 尽可能多地提取有价值的问答对，但不要编造转录中没有的信息
5. 如果转录内容太短或没有实质性信息，返回空列表 []

输出格式（严格JSON数组）：
[
  {{"q": "问题1", "a": "答案1"}},
  {{"q": "问题2", "a": "答案2"}}
]

只输出JSON数组，不要有任何其他文字。

转录文字：
{transcript}"""


def generate_qa_from_transcript(transcript: str) -> list[dict]:
    """Generate Q&A pairs from a video transcript using LLM.

    Args:
        transcript: Raw text from video transcription.

    Returns:
        List of {"q": str, "a": str} dicts. Empty list if transcript
        is empty or LLM fails.
    """
    if not transcript or not transcript.strip():
        return []

    # 走和主程序同一套 LLM 配置解析（LLM_* 优先，回落 DEEPSEEK_*），
    # 不要再自己读 DEEPSEEK_API_KEY —— 设置面板保存时会把旧键清空，
    # 各自读各自的会出现"主程序能跑、这个脚本说没 key"。
    from rag.llm_client import resolve_config
    api_key, base_url, model = resolve_config()
    if not api_key:
        logger.error("还没配置 LLM 接口密钥（设置面板里填，或 .env 里的 LLM_API_KEY）")
        return []

    client = OpenAI(api_key=api_key, base_url=base_url)

    prompt = QA_GENERATION_PROMPT.format(transcript=transcript[:8000])

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=4096,
        )
        content = response.choices[0].message.content or ""

        # Extract JSON array from response (handle markdown code blocks)
        content = content.strip()
        if content.startswith("```"):
            # Remove markdown code block wrapper
            lines = content.split("\n")
            content = "\n".join(lines[1:-1] if lines[-1].strip() == "```"
                                else lines[1:])
            content = content.strip()

        result = json.loads(content)
        if not isinstance(result, list):
            logger.warning("LLM returned non-list, wrapping")
            result = [result] if isinstance(result, dict) else []

        # Validate structure
        valid = []
        for item in result:
            if isinstance(item, dict) and "q" in item and "a" in item:
                valid.append({"q": str(item["q"]).strip(),
                              "a": str(item["a"]).strip()})

        logger.info(f"Generated {len(valid)} Q&A pairs from transcript")
        return valid

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse LLM response as JSON: {e}")
        return []
    except Exception as e:
        logger.error(f"Q&A generation failed: {e}")
        return []
