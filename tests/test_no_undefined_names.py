# tests/test_no_undefined_names.py
"""静态检查：模块级函数里**不许用没定义的内部变量**。

为什么要这个测试（踩过两次，第二次潜伏很久）：
`_scan_fallback` 是**模块级函数**，看不到 `_main_impl` 的局部变量。
写代码时顺手用了 `whitelist` / `send_queue` 这类名字 → 每次运行到那行就
`NameError`，表现成"点了开始没反应"。第一次补了
`whitelist/new_tracker/only_new_messages`，**漏了 `send_queue`/`pending_queue`**；
因为那两个名字只在"气泡在但一个字都读不出来"（图片/语音/文件）的分支里用到，
很少有人走到，所以一直没暴露 —— 直到 2026-10-05 修好扫描窗口、
那条分支被激活，扫描线程才开始崩。

这类 bug 靠跑测试很难发现（要刚好走到那个分支），但**静态分析一眼就能看出来**。
所以这里直接用 AST 扫一遍：模块级函数里凡是"既不是参数、也不是局部赋值、
也不是模块级名字、也不是内置名"的变量引用，全部报出来。
"""
import ast
import builtins
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# 允许出现的"动态"名字：pytest 注入、以及确实由 globals/局部注入的名字
ALLOW = {"__file__", "__name__", "__doc__", "self", "cls"}

CHECK_FILES = ["main.py", "rag/responder.py", "wxbot/scanner.py"]


def _module_level_names(tree) -> set:
    """模块顶层能解析到的名字：import、赋值、def/class。"""
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for a in node.names:
                names.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                names.add(a.asname or a.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                names |= {n.id for n in ast.walk(t) if isinstance(n, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.If, ast.Try)):      # 条件定义（如 TYPE_CHECKING）
            for sub in node.body + getattr(node, "orelse", []):
                if isinstance(sub, (ast.Import, ast.ImportFrom, ast.Assign,
                                    ast.FunctionDef, ast.AsyncFunctionDef,
                                    ast.ClassDef)):
                    names |= _module_level_names(ast.Module(body=[sub], type_ignores=[]))
    return names


def _undefined_in(fn, module_names: set) -> list:
    """返回 [(行号, 名字)]：函数里引用了却解析不到的名字。"""
    params = {a.arg for a in fn.args.args}
    params |= {a.arg for a in fn.args.kwonlyargs}
    if fn.args.vararg:
        params.add(fn.args.vararg.arg)
    if fn.args.kwarg:
        params.add(fn.args.kwarg.arg)

    defined = set(params)
    # 局部赋值 / 嵌套 def / for 目标 / with as / except as / 推导式变量
    # ★ 也要算上**函数体内的延迟导入**（`from x import y` 写在函数里很常见，
    #    main.py 大量这么用）—— 漏了它会产生一堆误报。
    for n in ast.walk(fn):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            defined.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(n.name)
        elif isinstance(n, ast.arg):
            defined.add(n.arg)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            defined.add(n.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            defined |= set(n.names)
        elif isinstance(n, ast.Import):
            for a in n.names:
                defined.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, ast.ImportFrom):
            for a in n.names:
                defined.add(a.asname or a.name)

    out = []
    for n in ast.walk(fn):
        if not (isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)):
            continue
        if n.id in defined or n.id in module_names or n.id in ALLOW:
            continue
        if hasattr(builtins, n.id):
            continue
        out.append((n.lineno, n.id))
    return out


@pytest.mark.parametrize("relpath", CHECK_FILES)
def test_module_functions_have_no_undefined_names(relpath):
    """模块级函数不许引用解析不到的名字 —— 就是上面那个 NameError 的成因。"""
    path = ROOT / relpath
    tree = ast.parse(path.read_text(encoding="utf-8"))
    module_names = _module_level_names(tree)

    problems = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for lineno, name in _undefined_in(node, module_names):
                problems.append("%s:%d 函数 %s() 用到未定义的名字 %r"
                                % (relpath, lineno, node.name, name))
    assert not problems, "发现未定义的名字（会 NameError）:\n  " + "\n  ".join(problems)


def test_scan_fallback_receives_every_thing_it_uses():
    """`_scan_fallback` 必须**显式收到**它用到的所有内部变量。

    这条钉住 2026-10-05 的崩溃：它用了 send_queue/pending_queue，
    但这两个名字既不是参数、也没有外层作用域可捕获 → 扫到"图片/语音"就 NameError。
    """
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in tree.body
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "_scan_fallback")
    params = {a.arg for a in fn.args.args} | {a.arg for a in fn.args.kwonlyargs}
    for needed in ("send_queue", "pending_queue", "whitelist", "new_tracker",
                   "only_new_messages"):
        assert needed in params, "`_scan_fallback` 少了参数 %r" % needed
    # 调用点也要真的传进去（有参数但没传 = 默认 None = 那条路静默失效）
    assert "send_queue=send_queue" in src
    assert "pending_queue=pending_queue" in src
