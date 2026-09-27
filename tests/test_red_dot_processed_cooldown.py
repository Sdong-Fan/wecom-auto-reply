"""TDD: Red dot must have short cooldown after processing to avoid re-OCR.

Bug: After processing a red dot (send succeeds or fails), the same red dot
at the same y-position is detected every 3 seconds. The dedup catches it
eventually, but the full pipeline (click → screenshot → OCR) runs each time,
wasting CPU and switching windows unnecessarily.

Fix: After processing a red dot, apply a SHORT cooldown (10-15 seconds)
to that y-position. This prevents the 3-second cycle while allowing genuine
new messages from the same customer (which take 20+ seconds to type).
"""

from pathlib import Path
import re


def test_red_dot_loop_has_short_cooldown():
    """Red dot loop must check short cooldown before processing.

    The check should use is_clickable with a short timeout (10-15s)
    to prevent re-processing the same red dot every 3 seconds.
    """
    source = Path("main.py").read_text(encoding="utf-8")

    # Find the red dot processing loop
    pattern = r'if reds:.*?(?=\n\s{12}# ══ 5\.|\n\s{12}if customer_red_y|\Z)'
    match = re.search(pattern, source, re.DOTALL)
    assert match, "Could not find red dot processing block"

    block = match.group()

    # The loop must have a cooldown check for already-processed red dots
    # Either is_clickable with short timeout, or a similar mechanism
    has_cooldown = "is_clickable" in block or "not _recently_processed" in block
    assert has_cooldown, \
        "Red dot loop must check cooldown for recently processed y-positions. " \
        "Without this, the same red dot triggers full OCR pipeline every 3 " \
        "seconds after a send failure."


def test_cooldown_timeout_is_short():
    """Red dot cooldown timeout must be short (10-15s), not the default 60s.

    A 60-second cooldown would block genuine new messages from the same
    customer. 10-15 seconds is enough to prevent the 3-second cycle while
    allowing follow-up messages.
    """
    source = Path("main.py").read_text(encoding="utf-8")

    # Find is_clickable calls in the red dot section
    # The timeout should be ≤ 20 seconds
    pattern = r'is_clickable\([^)]*timeout\s*=\s*(\d+)'
    matches = re.findall(pattern, source)

    # At least one is_clickable call with timeout in the red dot section
    short_timeouts = [int(m) for m in matches if int(m) <= 20]
    assert len(short_timeouts) >= 1, \
        f"Red dot cooldown must use a short timeout (≤20s). Found timeouts: {matches}"


def test_red_dot_cooldown_existing_test_still_passes():
    """Existing test_red_dots_not_filtered_by_cooldown must still pass.

    The new short cooldown uses is_clickable inline (not via clickable_reds
    variable), so the existing assertion still holds.
    """
    source = Path("main.py").read_text(encoding="utf-8")

    # "clickable_reds" must NOT appear in the red dot path
    # (this is the variable name the old bug used)
    assert "clickable_reds" not in source, \
        "Must not reintroduce clickable_reds variable. " \
        "Use inline is_clickable check instead."
