"""TDD: main.py must clean up on normal exit.

After removing watchdog, main.py only needs to close Qdrant on exit.
"""

import re


def test_main_has_cleanup_function():
    """main.py must define _cleanup function."""
    with open("main.py", encoding="utf-8") as f:
        source = f.read()

    assert "def _cleanup" in source, \
        "main.py must define a _cleanup function"


def test_main_cleanup_closes_qdrant():
    """_cleanup must close the Qdrant client."""
    with open("main.py", encoding="utf-8") as f:
        source = f.read()

    match = re.search(r'def _cleanup.*?(?=\ndef |\Z)', source, re.DOTALL)
    assert match, "Could not find _cleanup function"
    func = match.group()

    assert "close" in func.lower(), \
        "_cleanup must call close() on Qdrant client"


def test_main_calls_cleanup_on_window_close():
    """main.py must call _cleanup when window is closed."""
    with open("main.py", encoding="utf-8") as f:
        source = f.read()

    assert "_cleanup" in source, \
        "main.py must reference _cleanup"
    assert re.search(r'_cleanup\(\)', source), \
        "main.py must call _cleanup() somewhere"
