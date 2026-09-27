"""TDD: Red dots (new messages) should NOT be filtered by cooldown.

Bug: After replying to a customer, mark_clicked(y) sets a 60-second cooldown.
When the same customer sends a new message (red dot at same y), is_clickable(y)
returns False → new message is ignored for 60 seconds.

Fix: Red dots should bypass the cooldown check. Cooldown is only for non-customer
rows in the fallback scan.
"""

from pathlib import Path
import re


def test_red_dots_not_filtered_by_cooldown():
    """Red dot detection should NOT use is_clickable cooldown filter."""
    source = Path("main.py").read_text(encoding="utf-8")

    # Find the red dot filtering line
    # The bug: clickable_reds = [y for y in reds if detector.is_clickable(y)]
    # Red dots should be processed regardless of cooldown
    assert "is_clickable" not in source or "clickable_reds" not in source, (
        "Red dots should not be filtered by is_clickable cooldown. "
        "New messages (red dots) must always be processed immediately."
    )


def test_fallback_scan_still_uses_cooldown():
    """Fallback scan should still use cooldown to avoid re-clicking same rows."""
    source = Path("main.py").read_text(encoding="utf-8")

    # The fallback scan should still have cooldown
    match = re.search(
        r'def _scan_fallback.*?(?=\ndef |\Z)',
        source, re.DOTALL
    )
    assert match, "_scan_fallback not found"
    body = match.group()

    assert "is_clickable" in body, (
        "Fallback scan must still use is_clickable cooldown"
    )
