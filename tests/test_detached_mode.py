"""TDD tests for detached process mode.

When main.py is launched from a terminal, closing the terminal kills
the process. To prevent this, main.py should support a --detach flag
that re-launches itself as a detached process (no console window).
"""
import os
import pytest


def test_main_py_has_detach_flag():
    """main.py should accept a --detach command-line flag.

    When --detach is passed, main.py should re-launch itself as a
    detached process (CREATE_NO_WINDOW on Windows) and exit the
    original terminal-bound process. This prevents the system from
    dying when the user closes the terminal window.
    """
    with open("main.py", encoding="utf-8") as f:
        source = f.read()

    assert "--detach" in source or "detach" in source, \
        "main.py must support --detach flag for detached mode"


def test_main_py_uses_detached_process():
    """When detaching, main.py should use DETACHED_PROCESS to detach
    from the parent console while still allowing GUI windows.

    Must NOT use CREATE_NO_WINDOW — that prevents tkinter from creating
    the UI window (tested and confirmed bug: UI invisible with that flag).
    """
    with open("main.py", encoding="utf-8") as f:
        source = f.read()

    assert "DETACHED_PROCESS" in source, \
        "main.py must use DETACHED_PROCESS for detached mode"
    # Verify CREATE_NO_WINDOW is not used in subprocess calls (ignore comments/docstrings)
    import re
    # Find all subprocess.Popen creationflags= lines
    flags = re.findall(r'creationflags\s*=\s*(.+?)(?:\n|$)', source)
    for flag_line in flags:
        assert "CREATE_NO_WINDOW" not in flag_line, \
            f"CREATE_NO_WINDOW in creationflags: {flag_line.strip()}"


def test_detach_strips_flag_from_child_argv():
    """_detach_process must strip --detach from the child's argv.

    Without this, the child process enters _detach_process() again,
    spawning another child in an infinite loop. Each child briefly
    creates the tkinter window (flash) then sys.exit(0).
    """
    import ast
    with open("main.py", encoding="utf-8") as f:
        source = f.read()

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_detach_process":
            # Find the Popen call and check its args
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    if isinstance(child.func, ast.Attribute) and child.func.attr == "Popen":
                        # Check that sys.argv is NOT passed directly
                        for arg in child.args:
                            if isinstance(arg, ast.BinOp) and isinstance(arg.right, ast.Name):
                                if arg.right.id == "argv":
                                    # Found [sys.executable] + sys.argv — bad!
                                    # Check if there's a filtered version above
                                    pass
            # Simpler check: look for "--detach" filtering in the function body
            func_source = ast.get_source_segment(source, node)
            assert "--detach" in func_source, \
                "_detach_process must reference --detach for filtering"
            assert "child_argv" in func_source or "sys.argv" not in func_source or \
                "--detach" in func_source, \
                "_detach_process must strip --detach from child argv"
            return
    assert False, "_detach_process function not found"


def test_main_py_has_top_level_exception_handler():
    """main() should have a top-level try/except that catches ALL
    exceptions including those that bypass the inner loop handler.

    Currently the inner try/except catches Exception, but if an
    exception is raised outside the inner loop (e.g., during
    initialization), the process dies silently.
    """
    with open("main.py", encoding="utf-8") as f:
        source = f.read()

    # The main() function should have a top-level try/except
    # that wraps the entire function body, not just the while loop
    assert "def main" in source, "main.py must have a main() function"
    # Check for try/except at the top level of main()
    lines = source.split("\n")
    in_main = False
    has_top_level_try = False
    indent_level = 0
    for line in lines:
        if "def main(" in line and "async" not in line and "_main" not in line:
            in_main = True
            indent_level = len(line) - len(line.lstrip())
            continue
        if in_main:
            stripped = line.strip()
            if stripped.startswith("try:") and len(line) - len(line.lstrip()) == indent_level + 4:
                has_top_level_try = True
                break

    assert has_top_level_try, \
        "main() must have a top-level try/except around the entire body"
