"""TDD: Concurrent scan threads must not overlap.

Bug: scan_tick() spawns a new thread every CHECK seconds. If a scan
takes longer than CHECK (OCR + LLM + sleep), multiple _run_scan
threads run concurrently, corrupting shared state (detector, pending_queue).

Fix: Add threading.Lock, non-blocking acquire at _run_scan entry.
"""

from pathlib import Path


def test_scan_uses_nonblocking_lock():
    """_run_scan must acquire a lock non-blocking and skip if held."""
    source = Path("main.py").read_text(encoding="utf-8")

    # Must define a scan lock
    assert "_scan_lock" in source or "scan_lock" in source, \
        "main.py must define a scan lock for thread synchronization"

    # Must use non-blocking acquire
    assert "acquire(blocking=False)" in source or "acquire(False)" in source, \
        "_run_scan must use non-blocking lock acquire"

    # Must release in finally block
    import re
    match = re.search(
        r'def _run_scan\(\).*?(?=\n    def |\n    # ─|$)',
        source, re.DOTALL
    )
    assert match, "Could not find _run_scan function"
    func = match.group()

    assert "finally" in func, \
        "_run_scan must release lock in finally block"
    assert "release" in func, \
        "_run_scan must call lock.release()"
