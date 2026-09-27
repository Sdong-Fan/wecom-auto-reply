"""TDD: UI window should be wide enough to show content behind it."""

from pathlib import Path


def test_window_width_at_least_700():
    """Window width should be at least 700px to show content."""
    source = Path("gui/main_window.py").read_text(encoding="utf-8")
    # Find geometry call
    import re
    match = re.search(r"geometry.*?(\d+)x(\d+)", source)
    assert match, "Could not find geometry call"
    width = int(match.group(1))
    assert width >= 700, f"Window width {width}px is too narrow, need >= 700px"
