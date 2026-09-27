"""TDD: PendingQueue must be thread-safe.

Bug: PendingQueue uses plain dict. Scan thread calls push(),
GUI thread calls remove()/get()/get_all() concurrently.
Can cause RuntimeError: dictionary changed size during iteration.

Fix: Add threading.Lock to all methods that access _items.
"""

from pathlib import Path


def test_pending_queue_has_lock():
    """PendingQueue must define a threading.Lock."""
    source = Path("rag/human_fallback.py").read_text(encoding="utf-8")

    assert "threading" in source, \
        "human_fallback.py must import threading"
    assert "Lock" in source, \
        "PendingQueue must use threading.Lock"


def test_pending_queue_push_uses_lock():
    """push() must acquire lock before accessing _items."""
    source = Path("rag/human_fallback.py").read_text(encoding="utf-8")

    import re
    match = re.search(
        r'def push\(.*?\n(?:.*?\n)*?.*?(?=\n    def |\Z)',
        source, re.DOTALL
    )
    assert match, "Could not find push method"
    func = match.group()

    assert "self._lock" in func or "lock" in func.lower(), \
        "push() must acquire lock before accessing _items"


def test_pending_queue_remove_uses_lock():
    """remove()/remove_key() 必须加锁再碰 _items。

    按人名的 ``remove`` 现在只是"取最新一条 + 按 key 删"，两步都走加锁的辅助方法 ——
    所以这里认两种写法：自己加锁，或者委托给加锁的 get/remove_key。
    """
    source = Path("rag/human_fallback.py").read_text(encoding="utf-8")

    import re
    match = re.search(
        r'def remove\(.*?\n(?:.*?\n)*?.*?(?=\n    def |\Z)',
        source, re.DOTALL
    )
    assert match, "Could not find remove method"
    func = match.group()

    ok = ("self._lock" in func or "lock" in func.lower()
          or ("self.get(" in func and "remove_key(" in func))
    assert ok, "remove() must acquire lock (自己加锁，或委托给加锁的方法)"


def test_remove_key_uses_lock():
    source = Path("rag/human_fallback.py").read_text(encoding="utf-8")
    import re
    match = re.search(
        r'def remove_key\(.*?\n(?:.*?\n)*?.*?(?=\n    def |\Z)',
        source, re.DOTALL
    )
    assert match, "Could not find remove_key method"
    assert "self._lock" in match.group()
