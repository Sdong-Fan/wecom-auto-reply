"""发送前必须确认"当前打开的就是目标会话"，否则宁可不发。

2026-09-25 发现：`send_message_via_keyboard` 只做「点输入框 → 粘贴 → 回车」，
作用于**当前打开的那个会话**。从待人工页给客户 A 点发送时，如果界面上开着的是
客户 B 的会话，A 的回复就会发到 B 那里。自动回复也有同样的竞态
（扫描线程在生成回复的几秒里可能已经点开了别人）。
"""
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SCANNER = (ROOT / "wxbot/scanner.py").read_text(encoding="utf-8")
MAIN = (ROOT / "main.py").read_text(encoding="utf-8")


def _scanner(cfg=None):
    from wxbot.scanner import Scanner
    return Scanner(cfg or {})


# ── 名字比对 ──────────────────────────────────────────────────────────

def test_name_core_strips_suffixes():
    f = _scanner()._name_core
    assert f("客户A@微信") == "客户A"
    assert f("客户D@微信") == "客户D"
    assert f("张三") == "张三"
    assert f("") == ""
    assert f(None) == ""


# ── open_conversation ─────────────────────────────────────────────────

@pytest.fixture
def scanner_with_list(monkeypatch):
    """造一个"列表里有两行"的 scanner。"""
    s = _scanner({"crop": {"name_row": {"top_offset": -16, "bottom_offset": 56},
                           "chat_area": {"top_margin_px": 100}},
                  "fallback": {"start_y_px": 0, "row_height_px": 100, "max_scan_rows": 2},
                  "scan": {"dynamic_rows": False}})
    img = Image.new("RGB", (400, 300), (250, 250, 250))
    monkeypatch.setattr(s, "capture_chat_list", lambda: img)
    monkeypatch.setattr(s, "chat_fingerprint", lambda: "fp")
    monkeypatch.setattr(s, "wait_chat_update", lambda *a, **k: 0.1)
    monkeypatch.setattr(s, "capture_region",
                        lambda *a: Image.new("RGB", (400, 100), (250, 250, 250)))
    monkeypatch.setattr(s, "get_col3_region", lambda: (100, 0, 400, 800))
    return s


def test_opens_matching_row_and_verifies(scanner_with_list, monkeypatch):
    """列表里第 2 行才是目标客户 → 必须点到那一行，并且校验通过后才算成功。"""
    s = scanner_with_list
    clicked = []
    calls = {"n": 0}

    def reader(img):
        calls["n"] += 1
        # 第 1 行读出来是别人，第 2 行是目标
        return None, ("客户B@微信" if calls["n"] == 1 else "客户A@微信")

    monkeypatch.setattr(s, "click_col2_row", lambda y: clicked.append(y) or True)
    monkeypatch.setattr(s, "verify_open", lambda name, r: True)
    assert s.open_conversation("客户A@微信", reader) is True
    assert clicked, "必须点了某一行"
    assert calls["n"] >= 2, "第 1 行不是目标，应当继续找"


def test_returns_false_when_conversation_not_in_list(scanner_with_list, monkeypatch):
    s = scanner_with_list
    monkeypatch.setattr(s, "click_col2_row", lambda y: True)
    monkeypatch.setattr(s, "verify_open", lambda name, r: True)
    assert s.open_conversation("不存在的客户", lambda img: (None, "客户B@微信")) is False


def test_returns_false_when_verification_fails(scanner_with_list, monkeypatch):
    """列表里有名字，但点开后校验不通过 → 必须返回 False（别发）。"""
    s = scanner_with_list
    monkeypatch.setattr(s, "click_col2_row", lambda y: True)
    monkeypatch.setattr(s, "verify_open", lambda name, r: False)
    assert s.open_conversation("客户A@微信", lambda img: (None, "客户A@微信")) is False


def test_empty_name_returns_false(scanner_with_list):
    assert scanner_with_list.open_conversation("", lambda img: (None, "x")) is False


# ── _do_send 接线 ─────────────────────────────────────────────────────

def test_do_send_opens_conversation_before_pasting():
    assert "scanner.open_conversation(customer_name, detector.read_row_name)" in MAIN
    i = MAIN.index("scanner.open_conversation(customer_name, detector.read_row_name)")
    j = MAIN.index("send_message_via_keyboard", i)
    assert i < j, "必须先确认会话，再粘贴"
    assert "拒绝发送" in MAIN


def test_do_send_aborts_and_warns():
    seg = MAIN[MAIN.index("def _do_send"):MAIN.index("def _process_send_queue_tick")]
    assert "return False" in seg
    assert "set_banner" in seg, "中止发送要让人看见，不能只在日志里"


def test_scanner_stores_cfg_for_row_geometry():
    assert "self._cfg = cfg" in SCANNER


def test_open_conversation_rejects_empty_and_missing():
    """两种失败路径都必须返回 False（调用方据此拒发）。"""
    assert "if not want:\n            return False" in SCANNER
    assert "拒绝发送，避免发错人" in SCANNER
