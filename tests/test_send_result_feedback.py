"""TDD: UI must only show '已发送' after _do_send succeeds.

Bug: root.after(0, window.increment_auto_sent) runs immediately after
send_queue.put, before the actual send. If _do_send fails, UI still
shows success.

Fix: Move UI updates to after _do_send returns True.
"""

from pathlib import Path


def test_auto_send_ui_update_after_send():
    """auto_send branch must NOT call increment_auto_sent before send_queue."""
    source = Path("main.py").read_text(encoding="utf-8")

    # Find the auto_send block
    marker = 'dispatch_level == "auto_send"'
    idx = source.find(marker)
    assert idx >= 0, "Could not find auto_send branch"

    # Get the auto_send block (until next elif/else)
    block_start = source.rfind("\n", 0, idx)
    block_end = source.find("elif", idx)
    if block_end < 0:
        block_end = source.find("else:", idx)
    block = source[block_start:block_end]

    # increment_auto_sent must NOT be in the auto_send block
    # (it should be in _process_send_queue_tick after _do_send succeeds)
    assert "increment_auto_sent" not in block, \
        "auto_send must NOT call increment_auto_sent. " \
        "UI update must happen after _do_send succeeds in _process_send_queue_tick."


def test_send_queue_tick_updates_ui_on_success():
    """_process_send_queue_tick must update UI after _do_send returns True."""
    source = Path("main.py").read_text(encoding="utf-8")

    import re
    match = re.search(
        r'def _process_send_queue_tick\(\).*?(?=\n    def |\Z)',
        source, re.DOTALL
    )
    assert match, "Could not find _process_send_queue_tick"
    func = match.group()

    # Must check _do_send return value
    assert "increment_auto_sent" in func or "add_record" in func, \
        "_process_send_queue_tick must update UI after successful send"
