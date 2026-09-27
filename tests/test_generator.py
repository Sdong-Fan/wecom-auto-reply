"""TDD: generator.py prompt must strongly enforce '需要人工处理' rule."""

from rag.generator import SYSTEM_PROMPT


def test_prompt_requires_need_human_when_no_knowledge():
    """SYSTEM_PROMPT must instruct LLM to say '需要人工处理' when knowledge
    doesn't cover the question."""
    assert "需要人工处理" in SYSTEM_PROMPT, \
        "SYSTEM_PROMPT must include the required fallback phrase"

    # Must explicitly state that knowledge gaps → fallback
    has_gap_rule = (
        "不足以" in SYSTEM_PROMPT
        or "不包含" in SYSTEM_PROMPT
        or "没有涉及" in SYSTEM_PROMPT
        or "未涵盖" in SYSTEM_PROMPT
    )
    assert has_gap_rule, \
        "SYSTEM_PROMPT must state what to do when knowledge is insufficient"


def test_prompt_forbids_fabrication():
    """SYSTEM_PROMPT must explicitly forbid making up information."""
    assert "不得编造" in SYSTEM_PROMPT, \
        "SYSTEM_PROMPT must forbid fabrication"
    assert "知识片段" in SYSTEM_PROMPT, \
        "SYSTEM_PROMPT must reference '知识片段' (the context chunks)"


def test_prompt_forbids_evasive_replies():
    """SYSTEM_PROMPT must forbid evasive phrases like '我没有相关信息'.

    This is the key enhancement: the old prompt allowed LLMs to say
    '我没有相关信息' or '我不太清楚' instead of '需要人工处理'.
    The enhanced prompt must explicitly ban these.
    """
    # The enhanced prompt must explicitly ban common evasive patterns
    assert "我没有相关信息" in SYSTEM_PROMPT, \
        "SYSTEM_PROMPT must explicitly forbid '我没有相关信息' as an evasive reply"
    assert "我不太清楚" in SYSTEM_PROMPT, \
        "SYSTEM_PROMPT must explicitly forbid '我不太清楚' as an evasive reply"
    assert "建议您咨询" in SYSTEM_PROMPT, \
        "SYSTEM_PROMPT must explicitly forbid '建议您咨询' as an evasive reply"
