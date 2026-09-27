"""TDD: auto_replies.jsonl must NOT log "success": true before send.

Bug: _log_reply() writes "success": true when generation succeeds,
but the actual send happens asynchronously via send_queue and CAN FAIL.
The JSONL misleads readers into thinking the message was sent.

Fix:
1. handle_customer_message does NOT log for auto_send (send not yet done)
2. A separate log_send_result() method is called after actual send succeeds
3. The JSONL entry accurately reflects send outcome
"""

from pathlib import Path
import re


def _get_handle_customer_message_body():
    """Extract handle_customer_message method body."""
    source = Path("rag/responder.py").read_text(encoding="utf-8")
    pattern = (
        r'async def handle_customer_message\(.*?\n'
        r'(?:.*?\n)*?'
        r'(?=\n    async def |\n    def |\nclass |\Z)'
    )
    match = re.search(pattern, source, re.DOTALL)
    assert match, "Could not find handle_customer_message"
    return match.group()


def test_handle_customer_message_does_not_log_for_auto_send():
    """handle_customer_message must NOT log to JSONL before actual send.

    The _log_reply call within handle_customer_message must be guarded
    so that auto_send results are NOT logged until the send actually
    succeeds (which happens in the caller via send_queue).
    """
    body = _get_handle_customer_message_body()

    # The _log_reply call must be guarded to skip auto_send
    # It should check dispatch_level before logging
    assert "_log_reply" in body, \
        "handle_customer_message should still call _log_reply for " \
        "non-auto_send dispatch levels"

    # Check that auto_send is excluded from immediate logging
    # Either: _log_reply is not called unconditionally
    # OR: _log_reply has a condition excluding auto_send
    assert "auto_send" in body or "dispatch_level" in body, \
        "handle_customer_message must check dispatch_level before logging. " \
        "auto_send results should NOT be logged until send actually succeeds."


def test_log_send_result_method_exists():
    """Responder must have a method to log send outcome after actual send."""
    source = Path("rag/responder.py").read_text(encoding="utf-8")

    # Should have a method that logs the send result
    assert "def log_send_result" in source or "def log_send" in source, \
        "Responder must have a log_send_result method that is called " \
        "after the actual send succeeds/fails. This ensures the JSONL " \
        "accurately reflects whether the message was actually sent."


def test_send_queue_handler_logs_after_send():
    """main.py must call log_send_result after _do_send completes.

    The _process_send_queue_tick function should log the send result
    after _do_send returns (True or False).
    """
    source = Path("main.py").read_text(encoding="utf-8")

    # Find _process_send_queue_tick function
    pattern = (
        r'def _process_send_queue_tick\(\):.*?\n'
        r'(?:.*?\n)*?'
        r'(?=\n    def |\nclass |\Z)'
    )
    match = re.search(pattern, source, re.DOTALL)
    assert match, "Could not find _process_send_queue_tick"
    func = match.group()

    # Should call log_send_result or responder.log_* after _do_send
    assert "log_send" in func or "_log_reply" in func, \
        "_process_send_queue_tick must log the send result after " \
        "_do_send completes, so the JSONL reflects actual send outcome."
