# tests/test_window_topmost.py
"""窗口置顶开关。

店主反馈："软件好像强制置顶在所有页面前面了，可以改吗"。

原来是**硬编码** `self.root.attributes('-topmost', True)` —— 没开关、没注释，
想不置顶只能改代码。置顶对"盯着待人工"有用，但一直压着别的窗口很烦，
所以改成：**默认不置顶** + 底部一个「窗口置顶」勾选框，随时切、写回配置。

这里钉四件事：
  · 默认值是不置顶（不许悄悄改回 True）；
  · `attributes('-topmost', ...)` 必须读配置，不能再硬编码；
  · 勾/取消真的写进 config.json，而且**只动这一个键**；
  · 界面上那个勾选框的初始状态要跟配置一致。
"""
import json
import tkinter as tk
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_default_is_not_on_top():
    """默认不置顶。"""
    import sys
    sys.path.insert(0, str(ROOT))
    from gui.main_window import always_on_top_enabled
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    assert cfg.get("ui", {}).get("always_on_top") is False
    assert always_on_top_enabled() is False


def test_topmost_is_read_from_config_not_hardcoded():
    """`attributes('-topmost', ...)` 必须走配置 —— 不许再写死 True。

    查 **AST** 而不是查源码字符串：注释里会引用那句旧写法做说明，字符串会误判。
    """
    import ast

    src = (ROOT / "gui/main_window.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = []
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "attributes" and n.args):
            first = n.args[0]
            if isinstance(first, ast.Constant) and "topmost" in str(first.value):
                calls.append(n)
    assert calls, "找不到设置 '-topmost' 的地方（是不是改了写法？这条测试该跟着改）"
    for c in calls:
        arg = c.args[1] if len(c.args) > 1 else None
        assert not (isinstance(arg, ast.Constant) and arg.value is True), \
            "不许把置顶写死成 True —— 要读配置"
    # 启动时那次必须来自配置读取函数
    assert "attributes('-topmost', always_on_top_enabled())" in src


def test_set_always_on_top_roundtrip(tmp_path, monkeypatch):
    """勾/取消要真的写进 config.json（重启也记得）。"""
    from config import settings_store as ss
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"ui": {"always_on_top": False}}), encoding="utf-8")
    monkeypatch.setattr(ss, "load_config",
                        lambda path=None: json.loads(p.read_text(encoding="utf-8")))
    monkeypatch.setattr(ss, "save_config",
                        lambda cfg, path=None: p.write_text(json.dumps(cfg),
                                                            encoding="utf-8"))

    from gui.main_window import set_always_on_top
    set_always_on_top(True)
    assert json.loads(p.read_text(encoding="utf-8"))["ui"]["always_on_top"] is True
    set_always_on_top(False)
    assert json.loads(p.read_text(encoding="utf-8"))["ui"]["always_on_top"] is False


def test_set_always_on_top_keeps_other_config(tmp_path, monkeypatch):
    """只动 `ui.always_on_top`，别把别的键冲掉（save_config 是整份覆盖写）。"""
    from config import settings_store as ss
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"ui": {"always_on_top": False, "theme": "dark"},
                             "kb": {"learn": {"enabled": True}},
                             "rag": {"high_confidence_threshold": 0.5}}),
                 encoding="utf-8")
    monkeypatch.setattr(ss, "load_config",
                        lambda path=None: json.loads(p.read_text(encoding="utf-8")))
    monkeypatch.setattr(ss, "save_config",
                        lambda cfg, path=None: p.write_text(json.dumps(cfg),
                                                            encoding="utf-8"))

    from gui.main_window import set_always_on_top
    set_always_on_top(True)
    got = json.loads(p.read_text(encoding="utf-8"))
    assert got["ui"]["theme"] == "dark", "别把 ui 段里别的键冲掉"
    assert got["kb"]["learn"]["enabled"] is True
    assert got["rag"]["high_confidence_threshold"] == 0.5


def test_footer_has_the_checkbox():
    """底部要有「窗口置顶」勾选框，且初始状态跟配置一致。"""
    pytest.importorskip("tkinter")
    try:
        _probe = tk.Tk()
    except Exception as e:
        pytest.skip("没有可用的显示环境: %s" % e)
    _probe.withdraw()
    try:
        from gui.main_window import MainWindow, always_on_top_enabled
        w = MainWindow()
        w.root.withdraw()                       # 测试期间别弹窗
        found = []

        def walk(widget):
            for c in widget.winfo_children():
                try:
                    if "窗口置顶" in str(c.cget("text")):
                        found.append(c)
                except Exception:
                    pass
                walk(c)

        walk(w.root)
        assert found, "底部没找到「窗口置顶」勾选框"
        assert bool(w._topmost_on.get()) is bool(always_on_top_enabled())
        w.root.destroy()
    finally:
        _probe.destroy()
