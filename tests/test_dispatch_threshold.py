"""TDD: Three-tier dispatch must check confidence score, not just guard decision.

Design:
- cosine > 0.7 + guard pass → auto_send
- 0.4 ≤ cosine ≤ 0.7 + guard pass → human_confirm
- cosine < 0.4 or guard block → human_handle

Bug: responder.py auto-sends all guard-pass replies regardless of confidence.
A reply with confidence 0.527 should go to human_confirm, not auto_send.
"""

from pathlib import Path
import re


def test_auto_send_requires_high_confidence():
    """auto_send must only be set when top_score > high_threshold (0.7).

    The current code sets auto_send for ALL guard-pass replies.
    It must check top_score against the high confidence threshold.
    """
    source = Path("rag/responder.py").read_text(encoding="utf-8")

    # Find the dispatch logic block (after guard_result is computed)
    match = re.search(
        r'guard_result = classify_and_dispatch.*?return ReplyResult',
        source, re.DOTALL
    )
    assert match, "Could not find dispatch logic block"
    block = match.group()

    # The block must reference top_score for the dispatch decision
    has_score_check = (
        "top_score" in block and (
            "0.7" in block or
            "high" in block or
            "HIGH" in block or
            "high_confidence" in block or
            "high_thresh" in block or
            "DEFAULT_HIGH_THRESHOLD" in block
        )
    )

    assert has_score_check, (
        "Dispatch logic must check top_score against high threshold (0.7) "
        "before setting auto_send. Currently it only checks guard_result.decision."
    )
