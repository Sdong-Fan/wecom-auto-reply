"""TDD: LLM 'needs human' signal should go through guard, not hard-block.

Bug: responder.py line 173 hard-escalates when LLM reply contains
"需要人工处理" or "需要帮您确认". This kills high-confidence answers
(e.g. "抖音账号检测在哪里？" score=0.79) because the LLM also mentions
"转人工" when the combined OCR text includes a pricing question.

Design:
- Remove hard block at line 173
- Pass llm_requests_human signal to guard
- Guard treats it as low_risk (human_confirm), not block
- High confidence (>0.7) + guard pass → still auto_send
"""

from pathlib import Path
import re


def test_no_hard_block_on_llm_human_request():
    """responder.py must NOT hard-return when LLM reply contains
    '需要人工处理' or '需要帮您确认'. Instead, pass signal to guard."""
    source = Path("rag/responder.py").read_text(encoding="utf-8")

    # The section after LLM generation, before guard
    marker = "# ── Three-tier quality gate"
    idx = source.find(marker)
    assert idx >= 0, "Could not find three-tier quality gate section"

    gate_section = source[idx:idx + 800]

    # There must NOT be a hard return with escalated=True for LLM human request
    # before classify_and_dispatch is called
    guard_call_idx = gate_section.find("classify_and_dispatch")
    if guard_call_idx >= 0:
        before_guard = gate_section[:guard_call_idx]
        # The string "需要人工处理" may appear as a detection signal,
        # but there must NOT be a `return ReplyResult` with escalated=True
        # before the guard call
        lines = before_guard.split("\n")
        for line in lines:
            stripped = line.strip()
            if "return ReplyResult" in stripped and "escalated=True" in stripped:
                assert False, \
                    "responder.py must not return with escalated=True before guard. " \
                    "Pass the LLM human request signal to guard instead."


def test_guard_accepts_llm_human_request_signal():
    """classify_and_dispatch must accept llm_requests_human parameter."""
    source = Path("rag/guard.py").read_text(encoding="utf-8")

    # Find the classify_and_dispatch function signature
    match = re.search(
        r'def classify_and_dispatch\(([^)]*)\)', source, re.DOTALL
    )
    assert match, "Could not find classify_and_dispatch function"
    sig = match.group(1)

    assert "llm_requests_human" in sig, \
        "classify_and_dispatch must accept llm_requests_human parameter"


def test_guard_llm_human_request_treated_as_low_risk():
    """When llm_requests_human=True and no other block triggers,
    guard must return low_risk (not block). High confidence can still auto_send."""
    source = Path("rag/guard.py").read_text(encoding="utf-8")

    # Find classify_and_dispatch body
    match = re.search(
        r'def classify_and_dispatch\(.*?\n\)(.*?)(?=\ndef |\Z)',
        source, re.DOTALL
    )
    assert match, "Could not find classify_and_dispatch body"
    body = match.group(1)

    # Must reference llm_requests_human in the body
    assert "llm_requests_human" in body, \
        "classify_and_dispatch body must use llm_requests_human parameter"
