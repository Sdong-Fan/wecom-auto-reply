"""TDD: Fix shell injection and wire up real send.

Bug 1: send_message_via_keyboard uses f-string in PowerShell command.
        Single quotes in text break the command. Shell injection possible.

Bug 2: _do_send is a stub — logs but doesn't actually send.
"""

from pathlib import Path
import re


def test_send_message_escapes_quotes():
    """send_message_via_keyboard must escape single quotes in text."""
    source = Path("wxbot/scanner.py").read_text(encoding="utf-8")
    # The PowerShell command should NOT use raw f-string with {text}
    # It should escape quotes or use a safe method
    assert "replace" in source or "escape" in source or '""' in source or "-EncodedCommand" in source, (
        "send_message_via_keyboard must escape special characters in text"
    )


def test_do_send_calls_scanner():
    """_do_send must call scanner.send_message_via_keyboard() instead of just logging."""
    source = Path("main.py").read_text(encoding="utf-8")
    # Find _do_send function
    match = re.search(
        r'def _do_send\(.*?\).*?(?=\n    def |\n\n|\Z)',
        source, re.DOTALL
    )
    assert match, "_do_send not found"
    body = match.group()
    # Must reference scanner or send_message
    assert "scanner" in body or "send_message" in body, (
        "_do_send must call scanner.send_message_via_keyboard(), not just log"
    )


def test_no_fstring_in_powershell_command():
    """PowerShell command must not use raw f-string interpolation."""
    source = Path("wxbot/scanner.py").read_text(encoding="utf-8")
    # Check that the PowerShell call doesn't use f-string with {text}
    assert "f\"Set-Clipboard" not in source, (
        "PowerShell command must not use f-string — use a safe method instead"
    )
