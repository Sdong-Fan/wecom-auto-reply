"""TDD: _on_close must only log stack trace after user confirms exit.

Bug: Stack trace is logged BEFORE askyesno dialog. User clicking "No"
still writes a trace to close_trace.log, polluting the log.

Fix: Move trace logging after the askyesno check.
"""

from pathlib import Path


def test_on_close_logs_after_confirmation():
    """_on_close must write stack trace only after user confirms exit."""
    source = Path("gui/main_window.py").read_text(encoding="utf-8")

    import re
    match = re.search(
        r'def _on_close\(self\).*?(?=\n    def |\Z)',
        source, re.DOTALL
    )
    assert match, "Could not find _on_close function"
    func = match.group()

    # Find positions of key elements
    askyesno_pos = func.find("askyesno")
    trace_pos = func.find("format_stack")

    assert askyesno_pos >= 0, "_on_close must have askyesno dialog"
    assert trace_pos >= 0, "_on_close must have stack trace logging"

    # Stack trace must come AFTER askyesno
    assert trace_pos > askyesno_pos, \
        "Stack trace logging must come AFTER askyesno confirmation, not before"
