"""冒烟测试：真的把 `_scan_fallback` 调起来，抓「用了作用域里不存在的名字」这类错。

2026-09-25 踩到：`_scan_fallback` 是**模块级**函数，看不到 `_main_impl` 的局部变量。
白名单 / 新消息跟踪器都加在 `_main_impl` 里，函数体里直接用 →
每轮扫描 `NameError: name 'only_new_messages' is not defined`，
表现成"点了开始什么都没发生"。源码字符串测试完全抓不到这种错。
"""
import asyncio
from unittest.mock import MagicMock

import pytest
from PIL import Image

import main as main_mod


def _fake_scanner(c2_img):
    s = MagicMock()
    s.get_col2_region.return_value = (100, 0, 362, 1738)
    s.capture_chat_list.return_value = c2_img
    s.chat_fingerprint.return_value = "fp"
    s.click_col2_row.return_value = True
    s.wait_chat_update.return_value = 0.0
    s.capture_chat_area.return_value = Image.new("RGB", (600, 400), (250, 250, 250))
    return s


def _fake_detector():
    d = MagicMock()
    # 每行都当成非客户 —— 这样最容易走到"continue"分支，把作用域问题暴露出来
    d.read_row_name.return_value = (Image.new("RGB", (100, 30)), "某某")
    d.is_customer_name.return_value = False
    d.is_clickable.return_value = True
    d.clean_name_text.side_effect = lambda t: t
    d.is_in_cooldown.return_value = False
    d.extract_unreplied_bubbles.return_value = ([], False)
    d.extract_text.return_value = ""
    d.is_message_seen.return_value = False
    return d


CFG = {
    "fallback": {"start_y_px": 139, "row_height_px": 98, "max_scan_rows": 6,
                 "fallback_click_cooldown_seconds": 120},
    "crop": {"name_row": {"top_offset": -16, "bottom_offset": 56,
                          "x_from_ratio": 0.2, "x_to_ratio": 1.0}},
    "ocr_confidence": {"name_detection": 0.05, "bubble_text": 0.15},
    "timing": {"fallback_after_click_delay": 0.01, "fallback_after_scroll_delay": 0.01},
    "dedup": {"fallback_click_cooldown_seconds": 120},
    "scan": {"only_new_messages": True},
}


def test_scan_fallback_runs_without_nameerror():
    """最小冒烟：不传跟踪器也必须能跑完（内部会自己造一个）。"""
    c2 = Image.new("RGB", (362, 1738), (250, 250, 250))
    out = asyncio.run(main_mod._scan_fallback(
        _fake_scanner(c2), _fake_detector(), dict(CFG),
        whitelist=None, new_tracker=None, only_new_messages=False))
    assert out == (False, "", None, [])   # 第 4 项是这条消息的气泡文本


def test_scan_fallback_accepts_injected_dependencies():
    """白名单 / 跟踪器 / 开关都由调用方传入 —— 不能靠闭包。"""
    import inspect
    sig = inspect.signature(main_mod._scan_fallback)
    for p in ("whitelist", "new_tracker", "only_new_messages"):
        assert p in sig.parameters, f"_scan_fallback 必须接收 {p} 参数"


def test_scan_fallback_primes_baseline_then_returns_nothing():
    """开了 only_new_messages：第一次调用建基线、什么都不处理。"""
    from wxbot.new_message_tracker import NewMessageTracker
    c2 = Image.new("RGB", (362, 1738), (250, 250, 250))
    t = NewMessageTracker(enabled=True)
    out = asyncio.run(main_mod._scan_fallback(
        _fake_scanner(c2), _fake_detector(), dict(CFG),
        whitelist=None, new_tracker=t, only_new_messages=True))
    assert out == (False, "", None)
    assert t.primed is True, "第一次调用必须建好基线"
    assert t.size > 0


def test_scan_fallback_second_call_has_no_new_messages():
    """同一张图再跑一次：没有任何会话变化 → 一个新消息都不该有。"""
    from wxbot.new_message_tracker import NewMessageTracker
    c2 = Image.new("RGB", (362, 1738), (250, 250, 250))
    t = NewMessageTracker(enabled=True)
    sc, de = _fake_scanner(c2), _fake_detector()
    asyncio.run(main_mod._scan_fallback(sc, de, dict(CFG), None, t, True))
    out = asyncio.run(main_mod._scan_fallback(sc, de, dict(CFG), None, t, True))
    assert out == (False, "", None, [])
    assert not de.click_col2_row.called, "没有变化就绝不能点开会话"


def test_main_passes_dependencies_at_call_site():
    """调用点必须把 `_scan_fallback` 用到的**每个**内部变量都传进去。

    ★ 2026-10-05 扩：原来只查 whitelist/new_tracker/only_new_messages，
    漏了 send_queue/pending_queue —— 而那两个名字正好在"气泡在但读不出字"
    （图片/语音/文件）那条**很少走到的**分支里用，于是潜伏成 NameError：
    扫描一碰到图片/语音/文件就崩，客户发什么机器人都没反应。
    """
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")
    assert "whitelist=whitelist," in src
    assert "new_tracker=new_tracker," in src
    assert "only_new_messages=only_new_messages," in src
    assert "send_queue=send_queue," in src, "漏传 send_queue → 扫到图片/语音就 NameError"
    # 末位参数后面没有逗号（实际是 `pending_queue=pending_queue))`），别把逗号写进断言
    assert "pending_queue=pending_queue" in src, "漏传 pending_queue 同上"
    assert "found, fb_text, fb_name, fb_bubbles" in src, \
        "兜底返回的 4 元组要接住（第 4 项是气泡文本，用于逐条记已见）"
