"""TDD: All sends must go through a single path with window switch.

Bug 1: responder.handle_customer_message calls send_reply internally
       (no window switch). main.py also has send_queue (no window switch).
Bug 2: Two different send mechanisms (pynput vs pyautogui).

Fix: handle_customer_message must NOT send. All sends go through
     main.py._do_send which wraps scanner._switch_to_wecom_and_back.
"""

from pathlib import Path


def test_handle_customer_message_does_not_send():
    """handle_customer_message must return result without sending.

    Sending is the caller's responsibility via send_queue → _do_send.
    """
    source = Path("rag/responder.py").read_text(encoding="utf-8")

    import re
    match = re.search(
        r'def handle_customer_message\(.*?\n(?:.*?\n)*?.*?(?=\n    def |\Z)',
        source, re.DOTALL
    )
    assert match, "Could not find handle_customer_message function"
    func = match.group()

    # Must NOT call send_reply
    assert "self.send_reply" not in func and "send_reply(" not in func, \
        "handle_customer_message must NOT call send_reply. " \
        "Sending is the caller's responsibility."


def test_do_send_uses_window_switch():
    """_do_send in main.py must call _switch_to_wecom_and_back."""
    source = Path("main.py").read_text(encoding="utf-8")

    import re
    match = re.search(
        r'def _do_send\(.*?\n(?:.*?\n)*?.*?(?=\n    def |\Z)',
        source, re.DOTALL
    )
    assert match, "Could not find _do_send function"
    func = match.group()

    assert "switch" in func or "wecom" in func or "focus" in func, \
        "_do_send must switch to WeChat window before sending"


def test_auto_send_uses_send_queue():
    """auto_send branch must put into send_queue for unified sending."""
    source = Path("main.py").read_text(encoding="utf-8")

    # Find the auto_send branch
    marker = 'dispatch_level == "auto_send"'
    idx = source.find(marker)
    assert idx >= 0, "Could not find auto_send branch"

    # Get the auto_send block
    block_start = source.rfind("\n", 0, idx)
    block_end = source.find("elif", idx)
    if block_end < 0:
        block_end = source.find("else:", idx)
    block = source[block_start:block_end]

    # Must use send_queue for unified sending
    assert "send_queue.put" in block, \
        "auto_send must use send_queue.put for unified sending path"
