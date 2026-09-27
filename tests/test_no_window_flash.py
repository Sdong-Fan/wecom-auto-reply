"""TDD: subprocess calls must use CREATE_NO_WINDOW to prevent console flash.

Bug: When main.py runs in DETACHED_PROCESS mode, every subprocess.run()
without CREATE_NO_WINDOW creates a visible black console window that flashes.

Fix: Add creationflags=CREATE_NO_WINDOW to all subprocess.run() calls
in sender.py and toast.py.
"""


def test_sender_clipboard_uses_create_no_window():
    """sender.py clipboard subprocess must use CREATE_NO_WINDOW."""
    with open("wxbot/sender.py", encoding="utf-8") as f:
        source = f.read()

    assert "CREATE_NO_WINDOW" in source, \
        "sender.py must use CREATE_NO_WINDOW in subprocess.run() calls"


def test_toast_uses_create_no_window():
    """toast.py PowerShell subprocess must use CREATE_NO_WINDOW."""
    with open("notify/toast.py", encoding="utf-8") as f:
        source = f.read()

    assert "CREATE_NO_WINDOW" in source, \
        "toast.py must use CREATE_NO_WINDOW in subprocess.run() calls"
