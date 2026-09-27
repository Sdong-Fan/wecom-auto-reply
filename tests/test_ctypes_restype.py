"""TDD: ctypes _set_clipboard must set restype for Win32 functions.

Bug: On 64-bit Windows, ctypes defaults to 32-bit c_int return type.
GlobalAlloc returns a 64-bit HGLOBAL → truncated to 32 bits → invalid
handle passed to GlobalLock → returns NULL (0) → ctypes.memmove(0, ...)
→ access violation writing 0x0000000000000000.

Fix:
1. Set argtypes + restype for all Win32 functions used
2. Check GlobalLock return value (NULL guard) before memmove
"""

from pathlib import Path
import re
import ast


def _get_set_clipboard_source():
    """Extract the _set_clipboard function body from scanner.py."""
    source = Path("wxbot/scanner.py").read_text(encoding="utf-8")

    # Find _set_clipboard function
    pattern = r'def _set_clipboard\(.*?\n(?:.*?\n)*?.*?(?=\n    def |\n    @|\nclass |\Z)'
    match = re.search(pattern, source, re.DOTALL)
    assert match, "Could not find _set_clipboard function"
    return match.group()


def test_ctypes_functions_have_restype_set():
    """Every Win32 function used in _set_clipboard must have .restype set."""
    func_source = _get_set_clipboard_source()

    # These Win32 functions are used
    win32_funcs = {
        "OpenClipboard",
        "EmptyClipboard",
        "CloseClipboard",
        "GlobalAlloc",
        "GlobalLock",
        "GlobalUnlock",
        "SetClipboardData",
    }

    for func in win32_funcs:
        # Check that restype is set for this function
        # Pattern: <obj>.<func>.restype = ...
        restype_pattern = re.compile(
            rf'{func}\.restype\s*=', re.IGNORECASE
        )
        assert restype_pattern.search(func_source), \
            f"restype must be set for {func} to prevent 64-bit pointer truncation. " \
            f"Without restype, ctypes defaults to 32-bit c_int, which truncates " \
            f"64-bit HGLOBAL/LPVOID/HANDLE values."


def test_ctypes_functions_have_argtypes_set():
    """Win32 functions that take parameters must have .argtypes set.

    EmptyClipboard() and CloseClipboard() take no arguments, so argtypes
    is optional for them. Functions that take handles/pointers MUST have
    argtypes set to prevent 64-bit truncation.
    """
    func_source = _get_set_clipboard_source()

    # Only functions that take parameters (skip no-arg functions)
    win32_funcs_with_args = {
        "OpenClipboard",
        "SetClipboardData",
        "GlobalAlloc",
        "GlobalLock",
        "GlobalUnlock",
    }

    for func in win32_funcs_with_args:
        argtypes_pattern = re.compile(
            rf'{func}\.argtypes\s*=', re.IGNORECASE
        )
        assert argtypes_pattern.search(func_source), \
            f"argtypes must be set for {func} to ensure correct parameter types. " \
            f"Without argtypes, parameter marshalling may be incorrect on 64-bit Windows."


def test_global_lock_null_check():
    """GlobalLock return value must be checked for NULL before memmove."""
    func_source = _get_set_clipboard_source()

    lines = func_source.split("\n")
    glock_line = None
    memmove_line = None
    for i, line in enumerate(lines):
        # "GlobalLock(" specifically matches the call, not the error string
        if "GlobalLock(" in line or "GlobalLock (" in line:
            if glock_line is None:
                glock_line = i
        if "memmove" in line:
            memmove_line = i

    assert glock_line is not None, "GlobalLock must be called"
    assert memmove_line is not None, "memmove must be called"
    assert glock_line < memmove_line, \
        "GlobalLock must be called before memmove"

    # Check lines between GlobalLock and memmove for a null guard
    between = "\n".join(lines[glock_line:memmove_line])
    assert "if not p" in func_source, \
        "Must check GlobalLock return value for NULL before calling memmove. " \
        "If GlobalLock returns NULL (0), memmove writes to address 0 " \
        "causing access violation."


def test_no_powershell_for_clipboard():
    """send_message_via_keyboard must NOT use PowerShell for clipboard."""
    source = Path("wxbot/scanner.py").read_text(encoding="utf-8")

    match = re.search(
        r'def send_message_via_keyboard\(self.*?\n(?:.*?\n)*?.*?(?=\n    def |\Z)',
        source, re.DOTALL
    )
    assert match, "Could not find send_message_via_keyboard function"
    func = match.group()

    assert "powershell" not in func.lower(), \
        "send_message_via_keyboard must not use PowerShell for clipboard."
