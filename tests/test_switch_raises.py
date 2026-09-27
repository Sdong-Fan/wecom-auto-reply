"""TDD: _switch_to_wecom_and_back must propagate failures.

Bug: _switch_to_wecom_and_back catches exceptions from func() and
logs them, but doesn't re-raise. _do_send never knows send failed.

Fix: Re-raise after logging so callers can handle failures.
"""

from pathlib import Path


def test_switch_raises_on_func_failure():
    """_switch_to_wecom_and_back must re-raise exceptions from func()."""
    source = Path("wxbot/scanner.py").read_text(encoding="utf-8")

    import re
    match = re.search(
        r'def _switch_to_wecom_and_back\(.*?\n(?:.*?\n)*?.*?(?=\n    def |\Z)',
        source, re.DOTALL
    )
    assert match, "Could not find _switch_to_wecom_and_back function"
    func = match.group()

    # Find the try block that calls func()
    lines = func.split("\n")
    in_func_try = False
    has_finally_or_raise = False
    for line in lines:
        stripped = line.strip()
        if "func()" in stripped:
            in_func_try = True
        if in_func_try and ("finally" in stripped or "raise" in stripped):
            has_finally_or_raise = True
            break
        # Stop at next def
        if in_func_try and stripped.startswith("def "):
            break

    assert has_finally_or_raise, \
        "_switch_to_wecom_and_back must use try/finally or re-raise after func(). " \
        "Otherwise _do_send logs success even when send failed."
