"""TDD: All sends go through unified send_queue path.

Design: handle_customer_message generates reply but does NOT send.
All sends (auto_send + manual) go through send_queue → _do_send →
scanner._switch_to_wecom_and_back.
"""

from pathlib import Path


def test_responder_does_not_send():
    """handle_customer_message must NOT call send_reply."""
    source = Path("rag/responder.py").read_text(encoding="utf-8")

    import re
    match = re.search(
        r'def handle_customer_message\(.*?\n(?:.*?\n)*?.*?(?=\n    def |\Z)',
        source, re.DOTALL
    )
    assert match, "Could not find handle_customer_message"
    func = match.group()

    assert "self.send_reply" not in func and "send_reply(" not in func, \
        "handle_customer_message must NOT call send_reply"
