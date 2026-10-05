# tests/test_no_undefined_names.py
"""静态检查：函数里**不许用解析不到的名字**（含嵌套函数、跨兄弟作用域）。

为什么需要（同一个坑踩了三次）：
`main.py` 里大量逻辑写成 `_main_impl` 内部的**嵌套函数**，它们共享 `_main_impl`
的作用域，**兄弟之间看不到对方的局部变量**。踩过的：

1. `_scan_fallback`（模块级）用了 `whitelist` / `send_queue` / `pending_queue`
   却既不是参数、也没有外层作用域 → 每轮扫描 NameError；
2. 同上，`send_queue`/`pending_queue` 潜伏更久 —— 只在"气泡在但读不出字"
   （图片/语音/文件）那条少见分支上崩，修好扫描窗口后才被激活；
3. `_confirm_screenshot_sent` 用了 `before_unreplied`，而那是它的**兄弟函数**
   `_do_send` 的局部变量 → 每次发送都记一条"发送失败"，客户其实收到了，
   日志在说谎。

这类 bug 靠跑测试很难撞上（要刚好走到那条分支），但**静态分析一眼能看出来**。
所以这里用 AST 带**作用域链**扫一遍：模块级名字 + 逐层外层函数的局部名 +
自己这一层的局部名（参数 / 赋值 / 嵌套 def / 导入 / for·with·except 目标 /
global·nonlocal）+ 内置名 —— 都解析不到就报出来。

★ 第 3 次能发生，正是因为最初这版检查**只扫模块级函数**、没走嵌套作用域。
"""

import ast
import builtins
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

CHECK_FILES = ["main.py", "rag/responder.py", "wxbot/scanner.py"]
ALLOW = {"__file__", "__name__", "__doc__", "self", "cls"}


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
        elif isinstance(node, (ast.If, ast.Try)):
            for sub in list(node.body) + list(getattr(node, "orelse", [])):
                if isinstance(sub, (ast.Import, ast.ImportFrom, ast.Assign,
                                    ast.FunctionDef, ast.AsyncFunctionDef,
                                    ast.ClassDef)):
                    names |= _module_level_names(ast.Module(body=[sub],
                                                            type_ignores=[]))
    return names


def _scope_nodes(fn):
    """遍历函数体内**属于这一层**的节点 —— **不进入**嵌套函数/类的内部。"""
    stack = list(fn.body)
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.Lambda, ast.ClassDef)):
            continue                      # 嵌套作用域单独递归处理
        stack.extend(ast.iter_child_nodes(node))


def _bindings(fn) -> set:
    """这个函数**自己**绑定的名字（不含嵌套函数内部的绑定）。"""
    names = {a.arg for a in fn.args.args} | {a.arg for a in fn.args.kwonlyargs}
    if fn.args.vararg:
        names.add(fn.args.vararg.arg)
    if fn.args.kwarg:
        names.add(fn.args.kwarg.arg)
    for n in _scope_nodes(fn):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            names.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)             # 嵌套函数名属于本层
        elif isinstance(n, ast.ExceptHandler) and n.name:
            names.add(n.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            names |= set(n.names)
        elif isinstance(n, ast.Import):
            for a in n.names:
                names.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, ast.ImportFrom):
            for a in n.names:
                names.add(a.asname or a.name)
    return names


def _resolves(name: str, scopes: list, module_names: set) -> bool:
    if name in ALLOW or name in module_names or hasattr(builtins, name):
        return True
    return any(name in scope for scope in scopes)


def _check_function(fn, enclosing: list, module_names: set,
                    relpath: str, problems: list):
    local = _bindings(fn)
    scopes = enclosing + [local]
    for n in _scope_nodes(fn):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            if not _resolves(n.id, scopes, module_names):
                problems.append("%s:%d 函数 %s() 用到解析不到的名字 %r"
                                % (relpath, n.lineno, fn.name, n.id))
    for n in _scope_nodes(fn):            # 递归进嵌套函数，当前作用域当它的外层
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _check_function(n, scopes, module_names, relpath, problems)


@pytest.mark.parametrize("relpath", CHECK_FILES)
def test_functions_have_no_undefined_names(relpath):
    """连嵌套函数一起查 —— 跨兄弟作用域取名是踩过三次的那类 bug。"""
    tree = ast.parse((ROOT / relpath).read_text(encoding="utf-8"))
    module_names = _module_level_names(tree)
    problems = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _check_function(node, [], module_names, relpath, problems)
    assert not problems, "发现解析不到的名字（会 NameError）:\n  " + "\n  ".join(problems)


def test_checker_actually_walks_nested_functions():
    """自检：这道检查必须能看见嵌套函数。

    否则它会重演第一次的失误 —— 只扫模块级函数，
    于是 `_confirm_screenshot_sent` 里那个 `before_unreplied` 就漏过去了。
    """
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    seen = []

    def walk(node, path=""):
        for n in ast.iter_child_nodes(node):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                seen.append(path + "/" + n.name)
                walk(n, path + "/" + n.name)

    walk(tree)
    depth = [p for p in seen if p.count("/") >= 2]
    assert depth, "main.py 里应当有嵌套函数（否则这道检查没意义）"
    assert any("_confirm_screenshot_sent" in p for p in depth), seen


def test_scan_fallback_receives_every_thing_it_uses():
    """`_scan_fallback` 必须**显式收到**它用到的所有内部变量。

    钉住 2026-10-05 的崩溃：它用了 send_queue/pending_queue，
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
    assert "send_queue=send_queue" in src
    assert "pending_queue=pending_queue" in src


def test_confirm_screenshot_sent_takes_before_as_argument():
    """`before` 必须当**参数**传 —— 它和 `_do_send` 是兄弟，抓不到对方的局部变量。

    钉住 2026-10-06 的崩溃：`before=before_unreplied` →
    `NameError: name 'before_unreplied' is not defined` ——
    每次发送都记一条"发送失败"，但客户其实收到了（日志在说谎）。
    """
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    def find(node):
        for n in ast.iter_child_nodes(node):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if n.name == "_confirm_screenshot_sent":
                    return n
                got = find(n)
                if got is not None:
                    return got
        return None

    fn = find(tree)
    assert fn is not None, "找不到 _confirm_screenshot_sent"
    params = {a.arg for a in fn.args.args} | {a.arg for a in fn.args.kwonlyargs}
    assert "before" in params, "`before` 必须当参数传，不能靠闭包抓"
    # 查 AST 而不是查源码字符串：docstring 里会引用这个名字做说明，字符串会误判
    loaded = {n.id for n in ast.walk(fn)
              if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    assert "before_unreplied" not in loaded, "函数体里不该直接引用 before_unreplied"
    passed = [k for n in ast.walk(fn) if isinstance(n, ast.Call) for k in n.keywords]
    assert any(k.arg == "before" and isinstance(k.value, ast.Name)
               and k.value.id == "before" for k in passed), \
        "必须把 before 透传给 _verify_screenshot_sent(before=before)"
