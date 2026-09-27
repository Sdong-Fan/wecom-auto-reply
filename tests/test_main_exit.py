"""TDD tests for main.py graceful exit handling.

Architecture: mainloop + after() + background thread.
_exit check runs every 100ms via root.after(100, _check_exit).
When _should_exit is True, _cleanup() and root.destroy() are called.
"""
import pytest


def test_has_check_exit_function():
    """main.py must define _check_exit for periodic exit detection."""
    with open("main.py", encoding="utf-8") as f:
        source = f.read()

    assert "def _check_exit" in source, \
        "main.py must define _check_exit function for exit detection"


def test_check_exit_schedules_itself():
    """_check_exit must re-schedule itself via root.after(100, _check_exit)."""
    with open("main.py", encoding="utf-8") as f:
        source = f.read()

    assert "root.after(100, _check_exit)" in source, \
        "_check_exit must re-schedule itself via root.after(100, _check_exit)"


def test_check_exit_calls_cleanup():
    """_check_exit must call _cleanup() when _should_exit is True."""
    with open("main.py", encoding="utf-8") as f:
        source = f.read()

    # Find the _check_exit function
    import re
    match = re.search(r'def _check_exit\(\).*?(?=\ndef |\n    #)', source, re.DOTALL)
    assert match, "_check_exit function not found"
    func = match.group()

    assert "_should_exit" in func, \
        "_check_exit must check _should_exit"
    assert "_cleanup()" in func, \
        "_check_exit must call _cleanup()"
    assert "root.destroy()" in func, \
        "_check_exit must call root.destroy()"


def test_main_uses_mainloop():
    """main.py must use root.mainloop() instead of asyncio.sleep loop."""
    with open("main.py", encoding="utf-8") as f:
        source = f.read()

    assert "root.mainloop()" in source, \
        "main.py must use root.mainloop() as the event loop"


def test_main_def_is_not_async():
    """main() should be a regular function (not async), since mainloop drives the event loop."""
    with open("main.py", encoding="utf-8") as f:
        source = f.read()

    # main() itself should be synchronous; async is only used in _scan_fallback
    assert "def main():" in source, \
        "main.py must define main() as a synchronous function"
    # The entry point should call main(), not asyncio.run(main())
    lines = source.split("\n")
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("main()") and not stripped.startswith("def "):
            return  # found the call
    assert False, "main.py __main__ block must call main() directly"


def test_guard_blocked_message_marked_seen_before_fallback():
    """After '>>> 客户消息', mark_message_seen must be called immediately
    (before RAG processing). This prevents the fallback path from
    re-processing guard-blocked messages due to customer name mismatch."""
    with open("main.py", encoding="utf-8") as f:
        source = f.read()

    # Find the section after ">>> 客户消息"
    marker = 'log.info(">>> 客户消息")'
    idx = source.find(marker)
    assert idx >= 0, "main.py must log '>>> 客户消息'"

    # The next mark_message_seen call should be right after (within 300 chars)
    after = source[idx:idx + 300]
    assert "detector.mark_message_seen(customer_name, text)" in after, \
        "mark_message_seen must be called immediately after '>>> 客户消息', before RAG processing"


def test_fallback_uses_main_customer_name():
    """Fallback path must use main path's customer_name for dedup,
    not a potentially different fb_name from a different Y position."""
    with open("main.py", encoding="utf-8") as f:
        source = f.read()

    # Find the fallback section — 取到下一个「══」段落为止，而不是写死 500 字符
    # （写死窗口会因为注释增删而误报，断言本身的意思不变）
    marker = "# ══ 11. 兜底"
    idx = source.find(marker)
    assert idx >= 0, "main.py must have fallback section"

    nxt = source.find("# ══", idx + len(marker))
    fallback_section = source[idx:nxt if nxt > 0 else len(source)]

    # The fallback must check is_message_seen with customer_name (main path's name)
    # NOT with fb_name which may differ due to OCR at different Y positions
    assert "is_message_seen(customer_name, fb_text)" in fallback_section or \
           "is_message_seen(customer_name," in fallback_section, \
        "Fallback must use customer_name (from main path) for dedup, not fb_name"
