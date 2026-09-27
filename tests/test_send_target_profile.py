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


def test_switch_uses_configured_title(monkeypatch):
    """切前台必须用配置里的标题，不是写死的「企业微信」。"""
    s = _scanner({"wecom": {"title_hint": "微信"}})
    asked = []

    class FakeGui:
        @staticmethod
        def getActiveWindow():
            return None

        @staticmethod
        def position():
            return (0, 0)

        @staticmethod
        def moveTo(*a):
            pass

        @staticmethod
        def getWindowsWithTitle(t):
            asked.append(t)
            w = type("W", (), {"activate": lambda self: None})()
            return [w]

    monkeypatch.setattr("wxbot.scanner.pyautogui", FakeGui)
    monkeypatch.setattr("wxbot.scanner.time.sleep", lambda s: None)
    s._switch_to_wecom_and_back(lambda: None)
    assert asked == ["微信"], f"应当按配置标题找窗口，实际问了 {asked}"


def test_switch_raises_when_window_missing(monkeypatch):
    s = _scanner({"wecom": {"title_hint": "微信"}})

    class FakeGui:
        @staticmethod
        def getActiveWindow():
            return None

        @staticmethod
        def position():
            return (0, 0)

        @staticmethod
        def getWindowsWithTitle(t):
            return []

    monkeypatch.setattr("wxbot.scanner.pyautogui", FakeGui)
    with pytest.raises(RuntimeError):
        s._switch_to_wecom_and_back(lambda: None)


def test_no_hardcoded_wecom_title_in_scanner_source():
    src = (ROOT / "wxbot/scanner.py").read_text(encoding="utf-8")
    assert "getWindowsWithTitle('企业微信')" not in src
    assert 'getWindowsWithTitle("企业微信")' not in src
