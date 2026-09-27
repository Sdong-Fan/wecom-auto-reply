"""TDD: send_message_via_keyboard must use direct clipboard (ctypes).

Bug: PowerShell subprocess timed out, causing sends to fail.
Fix: Use ctypes for direct clipboard access (no subprocess).
"""

from pathlib import Path


def test_send_uses_direct_clipboard():
    """send_message_via_keyboard must use ctypes clipboard, not subprocess."""
    source = Path("wxbot/scanner.py").read_text(encoding="utf-8")

    import re
    match = re.search(
        r'def send_message_via_keyboard\(.*?\n(?:.*?\n)*?.*?(?=\n    def |\Z)',
        source, re.DOTALL
    )
    assert match, "Could not find send_message_via_keyboard function"
    func = match.group()

    # Must NOT use subprocess for clipboard
    assert "subprocess.run" not in func, \
        "send_message_via_keyboard must not use subprocess for clipboard"
    # Must use _set_clipboard (ctypes)
    assert "_set_clipboard" in func, \
        "send_message_via_keyboard must use _set_clipboard (ctypes)"
