"""TDD: Send queue processor must not block GUI thread.

Bug: _process_send_queue_tick drains ALL items in a tight loop.
send_message_via_keyboard has PowerShell subprocess + sleep.
Multiple items block tkinter event loop for seconds.

Fix: Process only 1 item per tick.
"""

from pathlib import Path


def test_send_queue_processes_one_item_per_tick():
    """_process_send_queue_tick must process at most 1 item per call."""
    source = Path("main.py").read_text(encoding="utf-8")

    import re
    match = re.search(
        r'def _process_send_queue_tick\(\).*?(?=\n    def |\Z)',
        source, re.DOTALL
    )
    assert match, "Could not find _process_send_queue_tick function"
    func = match.group()

    # Must NOT have a while loop that drains all items
    # (should process only 1 item, then re-schedule)
    assert "while True" not in func, \
        "_process_send_queue_tick must NOT use 'while True' loop. " \
        "Process only 1 item per tick to avoid blocking GUI."
