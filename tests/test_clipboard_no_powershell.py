"""TDD: Clipboard must not depend on PowerShell subprocess.

Bug: PowerShell subprocess consistently times out (5s), causing
send_message_via_keyboard to fail. PowerShell is slow to start
and can hang.

Fix: Use ctypes for direct clipboard access on Windows (no subprocess).
"""

from pathlib import Path


def test_send_does_not_use_powershell():
    """send_message_via_keyboard must NOT use PowerShell for clipboard."""
    source = Path("wxbot/scanner.py").read_text(encoding="utf-8")

    import re
    match = re.search(
        r'def send_message_via_keyboard\(self.*?\n(?:.*?\n)*?.*?(?=\n    def |\Z)',
        source, re.DOTALL
    )
    assert match, "Could not find send_message_via_keyboard function"
    func = match.group()

    # Must NOT use PowerShell subprocess for clipboard
    assert "powershell" not in func.lower(), \
        "send_message_via_keyboard must not use PowerShell for clipboard. " \
        "Use ctypes or pyperclip instead."
