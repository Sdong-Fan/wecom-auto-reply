"""TDD: send_message_via_keyboard must click input box before pasting.

Bug: send_message_via_keyboard only does Ctrl+V + Enter.
After _switch_to_wecom_and_back activates WeChat, the cursor may not
be in the message input box. Paste goes to wrong widget.

Fix: Call click_input_box() before Ctrl+V.
"""

from pathlib import Path


def test_send_clicks_input_box():
    """send_message_via_keyboard must click input box before pasting."""
    source = Path("wxbot/scanner.py").read_text(encoding="utf-8")

    import re
    match = re.search(
        r'def send_message_via_keyboard\(self.*?\n(?:.*?\n)*?.*?(?=\n    def |\Z)',
        source, re.DOTALL
    )
    assert match, "Could not find send_message_via_keyboard function"
    func = match.group()

    # Must click input box before pasting
    assert "click_input_box" in func, \
        "send_message_via_keyboard must click input box before Ctrl+V"
