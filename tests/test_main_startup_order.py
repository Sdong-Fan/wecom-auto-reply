# tests/test_main_startup_order.py
"""`_main_impl` 里的启动顺序不能错 —— 这类 bug 会让人**一启动就崩**。

起因（2026-10-04，打包后实测）：在 `root = tk.Tk()` 之前写了一句
`root.after(400, _check_llm_ready)`，`root` 是 `_main_impl` 的局部变量，
于是每次启动都 `UnboundLocalError: cannot access local variable 'root'`
—— 编译得过、单测全绿（没人跑 _main_impl），但程序根本起不来。

这里用 AST 静态检查把这类"用了还没赋值的局部名"挡住，
再补一个"真的把程序拉起来看它活不活"的冒烟测试。
"""
import ast
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "main.py").read_text(encoding="utf-8")


def _main_impl_nodes():
    tree = ast.parse(SRC)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_main_impl":
            return node
    raise AssertionError("main.py 里找不到 _main_impl")


def _local_names(fn: ast.FunctionDef) -> set:
    """_main_impl 里被赋值的名字（= Python 眼里的局部变量）。"""
    names = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
    return names


def _uses_outside_nested_functions(fn: ast.FunctionDef):
    """收集"不在嵌套函数里"的名字读取 —— 嵌套函数是稍后才执行的，不算。"""
    out = []

    def walk(node, depth):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                walk(child, depth + 1)
                continue
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
                out.append((child.id, child.lineno, depth))
            walk(child, depth)

    walk(fn, 0)
    return out


def test_no_local_used_before_assignment_in_main_impl():
    """AST 检查：_main_impl 的局部变量不许"先用后赋值"。"""
    fn = _main_impl_nodes()
    locals_ = _local_names(fn)

    first_assign = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            first_assign.setdefault(node.id, node.lineno)

    bad = []
    for name, lineno, depth in _uses_outside_nested_functions(fn):
        if depth != 0 or name not in locals_:
            continue
        assigned = first_assign.get(name)
        if assigned is not None and lineno < assigned:
            bad.append(f"第 {lineno} 行用了 `{name}`，但它第 {assigned} 行才赋值")

    assert not bad, ("`_main_impl` 里有用到还没赋值的局部变量（启动即崩）：\n  "
                     + "\n  ".join(bad))


def test_root_is_only_used_after_tk_created():
    """点名 `root`：它是历史上真的崩过的那个。"""
    fn = _main_impl_nodes()
    assigns = [n.lineno for n in ast.walk(fn)
               if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
               and n.id == "root"]
    assert assigns, "没找到 root 的赋值？"
    first = min(assigns)
    for name, lineno, depth in _uses_outside_nested_functions(fn):
        if name == "root" and depth == 0:
            assert lineno > first, (
                f"第 {lineno} 行在 `root = ...`（第 {first} 行）之前用了 root "
                f"—— 打包版会 UnboundLocalError，一启动就退出")


@pytest.mark.slow
@pytest.mark.skipif(
    __import__("os").getenv("RUN_STARTUP_SMOKE") != "1",
    reason="会拉起 GUI：设 RUN_STARTUP_SMOKE=1 才跑（发布前建议跑一次）")
def test_app_actually_starts(tmp_path):
    """真的把程序拉起来，确认它不是"一启动就退出"。

    这是唯一能抓住 `UnboundLocalError` 那类启动崩溃的测试 ——
    静态检查负责定位，这个负责兜底。
    """
    exe = ROOT / ".venv" / "Scripts" / "python.exe"
    if not exe.exists():
        pytest.skip("没有 .venv，跳过启动冒烟")
    log = tmp_path / "startup.log"
    with open(log, "wb") as fh:
        proc = subprocess.Popen(
            [str(exe), str(ROOT / "main.py")],
            cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT,
            env={**__import__("os").environ, "WECOM_AUTOSTART": "0"})
        try:
            time.sleep(25)
            alive = proc.poll() is None
        finally:
            proc.kill()
            try:
                proc.wait(timeout=10)
            except Exception:
                pass
    text = log.read_text(encoding="utf-8", errors="replace")
    assert alive, ("程序启动后就退出了 —— 看日志：\n" + text[-1500:])
    assert "致命异常" not in text, ("启动过程报致命异常：\n" + text[-1500:])
