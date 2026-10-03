"""发送路径必须跟着 profile 找窗口，不能写死「企业微信」。

踩过的坑：`_switch_to_wecom_and_back` 原来用 `getWindowsWithTitle('企业微信')`。
切到微信模式后它会激活**企业微信**，把回复粘到错误的地方 ——
用户看到的现象是"微信一条都没回，企业微信里莫名其妙多了话"。
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


# ── profile 映射 ──────────────────────────────────────────────────────

def test_profile_maps_title_hint():
    from wxbot.profile import apply_to_config, load_profile
    out = apply_to_config({}, load_profile("wechat_pc"))
    assert out["wecom"]["title_hint"] == "微信"


def test_wecom_profile_keeps_its_title():
    from wxbot.profile import apply_to_config, load_profile
    out = apply_to_config({}, load_profile("wecom"))
    assert out["wecom"]["title_hint"] == "企业微信"


def test_prefer_title_used_when_no_title_hint():
    from wxbot.profile import apply_to_config
    out = apply_to_config({}, {"window": {"prefer_title": "Telegram"}})
    assert out["wecom"]["title_hint"] == "Telegram"


# ── Scanner ───────────────────────────────────────────────────────────

def _scanner(cfg):
    from wxbot.scanner import Scanner
    return Scanner(cfg)


def test_scanner_reads_title_hint_from_config():
    s = _scanner({"wecom": {"title_hint": "微信"}})
    assert s._title_hint == "微信"


def test_scanner_defaults_to_wecom_title():
    assert _scanner({})._title_hint == "企业微信"


def test_find_by_title_uses_configured_title(monkeypatch):
    """按标题兜底找窗口时必须用配置里的标题（微信模式要找「微信」），并排掉自己的窗口。"""
    s = _scanner({"wecom": {"title_hint": "微信"}})
    # hwnd → (标题, pid)；1 是机器人自己的界面（标题也含"微信"），2 才是微信本体
    windows = {1: ("微信智能客服", 74568), 2: ("微信", 12345)}

    monkeypatch.setattr("wxbot.scanner.win32gui.EnumWindows",
                        lambda cb, p: [cb(h, None) for h in windows])
    monkeypatch.setattr("wxbot.scanner.win32gui.IsWindowVisible", lambda h: True)
    monkeypatch.setattr("wxbot.scanner.win32gui.GetWindowText",
                        lambda h: windows[h][0])
    monkeypatch.setattr("wxbot.scanner.win32gui.GetClassName",
                        lambda h: "WeChatMainWndForPC")
    monkeypatch.setattr("wxbot.scanner.win32process.GetWindowThreadProcessId",
                        lambda h, p=None: (0, windows[h][1]))
    monkeypatch.setattr("wxbot.scanner.os.getpid", lambda: 74568)

    assert s._find_by_title() == 2, "应当按配置标题找，且不能返回机器人自己的窗口"


def test_switch_raises_when_window_missing(monkeypatch):
    s = _scanner({"wecom": {"title_hint": "微信"}})

    class FakeGui:
        @staticmethod
        def getActiveWindow():
            return None

        @staticmethod
        def position():
            return (0, 0)

    monkeypatch.setattr("wxbot.scanner.pyautogui", FakeGui)
    monkeypatch.setattr(s, "find_window", lambda: 0)
    monkeypatch.setattr(s, "_find_by_title", lambda: 0)
    with pytest.raises(RuntimeError):
        s._switch_to_wecom_and_back(lambda: None)


def test_switch_does_not_use_title_substring_search():
    """发送路径不许再按标题子串取 windows[0] —— 那会抓到机器人自己的界面。

    （注释里提到老写法没关系，这里查的是**代码**：不能有 `windows[0]` 取值，
    也不能调 pygetwindow 的 activate；必须走 find_window。）
    """
    src = (ROOT / "wxbot/scanner.py").read_text(encoding="utf-8")
    assert "windows[0]" not in src
    assert "target_window.activate()" not in src
    assert "hwnd = self._activate_target()" in src


def test_no_hardcoded_wecom_title_in_scanner_source():
    src = (ROOT / "wxbot/scanner.py").read_text(encoding="utf-8")
    assert "getWindowsWithTitle('企业微信')" not in src
    assert 'getWindowsWithTitle("企业微信")' not in src
