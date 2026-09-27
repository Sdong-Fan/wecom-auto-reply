"""原生采集层测试：PrintWindow 抓窗口 / PostMessage 点击 / DPI / 像素锚点。

对应 docs/探针-采集方式.md：不抢前台抓窗口、不抢前台点击。
这里只测纯逻辑与坐标换算（真机抓图靠 scripts/probe_capture_wework.py）。
"""
import ctypes
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PIL import Image

from wxbot import native_win


# ── DPI ────────────────────────────────────────────────────────────────

def test_native_win_uses_private_dll_instances():
    """native_win 绝不能污染 ctypes.windll 的共享 DLL 实例。

    踩过：在 ctypes.windll.user32 上设 GetWindowRect.argtypes 之后，
    pywin32 的 win32gui.GetWindowRect 抛
    `TypeError: expected LP_RECT instance instead of pointer to RECT`，
    整个发送路径挂掉。所以必须用独立的 WinDLL 实例。
    """
    assert native_win._u32 is not ctypes.windll.user32
    assert native_win._k32 is not ctypes.windll.kernel32
    assert native_win._g32 is not ctypes.windll.gdi32


def test_pywin32_getwindowrect_still_works_after_import():
    """导入 native_win 之后，pywin32 的 GetWindowRect 必须照常可用。"""
    import win32gui
    hwnd = win32gui.GetDesktopWindow()
    rect = win32gui.GetWindowRect(hwnd)   # 不能抛 TypeError
    assert len(rect) == 4


def test_ensure_dpi_aware_is_idempotent():
    assert native_win.ensure_dpi_aware() in (True, False)
    first = native_win.dpi_aware()
    assert native_win.ensure_dpi_aware() in (True, False)
    assert native_win.dpi_aware() == first


# ── 坐标换算 ────────────────────────────────────────────────────────────

def test_post_click_rejects_coords_outside_window(monkeypatch):
    monkeypatch.setattr(native_win, "window_rect", lambda hwnd: (100, 200, 400, 300))
    assert native_win.post_click(1234, 0, 0) is False      # 窗口左上角之外
    assert native_win.post_click(1234, 999, 300) is False  # 右边界之外
    assert native_win.post_click(1234, 200, 999) is False  # 下边界之外


def test_post_click_converts_screen_to_client(monkeypatch):
    """窗口在 (100,200)：屏幕点 (150,250) → 客户区 (50,50) → lParam 0x00320032。"""
    monkeypatch.setattr(native_win, "window_rect", lambda hwnd: (100, 200, 400, 300))
    seen = []

    class FakeU32:
        @staticmethod
        def PostMessageW(hwnd, msg, wp, lp):
            seen.append((hwnd, msg, wp, lp))
            return True

    monkeypatch.setattr(native_win, "_u32", FakeU32)
    monkeypatch.setattr(native_win.time, "sleep", lambda s: None)

    assert native_win.post_click(777, 150, 250) is True
    assert len(seen) == 2
    assert seen[0][1] == native_win.WM_LBUTTONDOWN
    assert seen[0][3] == (50 << 16) | 50
    assert seen[1][1] == native_win.WM_LBUTTONUP
    assert seen[1][2] == 0  # 抬起时 MK_LBUTTON 必须清掉


def test_post_click_propagates_post_message_failure(monkeypatch):
    monkeypatch.setattr(native_win, "window_rect", lambda hwnd: (0, 0, 400, 300))

    class FakeU32:
        @staticmethod
        def PostMessageW(hwnd, msg, wp, lp):
            return False

    monkeypatch.setattr(native_win, "_u32", FakeU32)
    assert native_win.post_click(1, 10, 10) is False


def test_grab_region_rejects_region_outside_window(monkeypatch):
    monkeypatch.setattr(native_win, "window_rect", lambda hwnd: (0, 0, 200, 200))
    assert native_win.grab_region(1, (150, 150, 100, 100)) is None
    assert native_win.grab_region(1, (-10, 0, 50, 50)) is None
    assert native_win.grab_region(1, (0, 0, 0, 50)) is None


def test_grab_region_crops_from_window_bitmap(monkeypatch):
    monkeypatch.setattr(native_win, "window_rect", lambda hwnd: (100, 200, 300, 400))
    full = Image.new("RGB", (300, 400), (255, 255, 255))
    full.paste(Image.new("RGB", (10, 10), (255, 0, 0)), (20, 30))
    monkeypatch.setattr(native_win, "grab_window", lambda hwnd: full)

    got = native_win.grab_region(1, (120, 230, 10, 10))
    assert got is not None and got.size == (10, 10)
    assert got.getpixel((0, 0)) == (255, 0, 0)


# ── 最小化 / 存活 ───────────────────────────────────────────────────────

def test_ensure_renderable_noop_when_not_iconic(monkeypatch):
    class FakeU32:
        @staticmethod
        def IsIconic(hwnd):
            return False

    monkeypatch.setattr(native_win, "_u32", FakeU32)
    assert native_win.ensure_renderable(1) is False


def test_ensure_renderable_unminimizes_without_activation(monkeypatch):
    calls = []

    class FakeU32:
        @staticmethod
        def IsIconic(hwnd):
            return True

        @staticmethod
        def ShowWindow(hwnd, cmd):
            calls.append(("show", cmd))
            return True

        @staticmethod
        def SetWindowPos(hwnd, after, x, y, cx, cy, flags):
            calls.append(("pos", after, flags))
            return True

    monkeypatch.setattr(native_win, "_u32", FakeU32)
    monkeypatch.setattr(native_win.time, "sleep", lambda s: None)

    assert native_win.ensure_renderable(9) is True
    assert calls[0] == ("show", native_win.SW_SHOWNOACTIVATE)
    _, after, flags = calls[1]
    assert after == native_win.HWND_BOTTOM
    assert flags & native_win.SWP_NOACTIVATE  # 绝不激活


def test_is_alive_handles_dead_handle(monkeypatch):
    class FakeU32:
        @staticmethod
        def IsWindow(hwnd):
            return False

        @staticmethod
        def IsWindowVisible(hwnd):
            return False

    monkeypatch.setattr(native_win, "_u32", FakeU32)
    assert native_win.is_alive(0) is False
    assert native_win.is_alive(123) is False


# ── 黑帧识别（微信 PC 风险）────────────────────────────────────────────

def test_looks_blank_detects_all_black():
    """jev 实测：微信是 GPU 合成窗口，PrintWindow 容易出整张黑。
    黑帧不报错、只是 OCR 读不到字，会让扫描"正常地什么都不干"，所以要主动识别。"""
    assert native_win.looks_blank(Image.new("RGB", (100, 100), (0, 0, 0))) is True
    assert native_win.looks_blank(Image.new("RGB", (100, 100), (5, 5, 5))) is True


def test_looks_blank_false_for_normal_screenshot():
    img = Image.new("RGB", (100, 100), (245, 247, 250))
    assert native_win.looks_blank(img) is False
    img.paste(Image.new("RGB", (10, 10), (0, 0, 0)), (0, 0))
    assert native_win.looks_blank(img) is False


def test_grab_window_accepts_check_blank_flag():
    import inspect
    sig = inspect.signature(native_win.grab_window)
    assert "check_blank" in sig.parameters
    assert sig.parameters["check_blank"].default is True, "默认要识别黑帧"


def test_blank_grab_falls_back_to_screen(monkeypatch):
    """黑帧要退回到区域抓屏，而不是让 OCR 去啃一张黑图。"""
    s = _scanner()
    monkeypatch.setattr(s, "_grab_window_cached", lambda: None)
    monkeypatch.setattr("wxbot.scanner.mss.mss",
                        MagicMock(side_effect=RuntimeError("fallback used")))
    with pytest.raises(RuntimeError, match="fallback used"):
        s.capture_screen((0, 0, 10, 10))


# ── 窗口挑选：标题优先（不按面积）──────────────────────────────────────

def test_find_main_hwnd_supports_prefer_title():
    import inspect
    sig = inspect.signature(native_win.find_main_hwnd)
    assert "prefer_title" in sig.parameters, \
        "微信同进程还有工具窗/看图窗，必须能按标题挑主窗口"


def test_calibrator_selects_window_by_title():
    from pathlib import Path
    src = Path("scripts/calibrate_chat_app.py").read_text(encoding="utf-8")
    assert '"--title"' in src
    assert "args.title" in src


# ── 像素锚点 ────────────────────────────────────────────────────────────

def _panel_image():
    """模拟企微：左 100px 深色导航栏 + 364px 列表 + 右侧浅色聊天面板。"""
    img = Image.new("RGB", (1000, 600), (40, 40, 40))
    img.paste(Image.new("RGB", (364, 600), (245, 245, 245)), (100, 0))
    img.paste(Image.new("RGB", (536, 600), (255, 255, 255)), (464, 0))
    return img


def test_detect_panels_finds_chat_panel_left_edge():
    panels = native_win.detect_panels(_panel_image())
    assert panels is not None
    # 右半边出现最多的颜色是白色聊天面板 → x0 应落在 464 附近
    assert abs(panels["x0"] - 464) <= 8
    assert panels["x1"] >= 990


def test_detect_panels_returns_none_on_tiny_image():
    assert native_win.detect_panels(Image.new("RGB", (50, 50))) is None


def test_check_layout_flags_config_mismatch():
    img = _panel_image()
    assert native_win.check_layout(img, 464, tolerance=20) is None
    warn = native_win.check_layout(img, 100, tolerance=20)
    assert warn and "边界" in warn


# ── Scanner 接线 ────────────────────────────────────────────────────────

def _scanner(cfg=None):
    from wxbot.scanner import Scanner
    base = {"columns": {"col1_width_px": 100, "col2_width_px": 364},
            "capture": {"native_window": True}}
    if cfg:
        base.update(cfg)
    return Scanner(base)


def test_scanner_native_default_true_and_can_be_disabled():
    assert _scanner()._native is True
    assert _scanner({"capture": {"native_window": False}})._native is False


def test_click_col2_row_uses_post_click_in_native_mode(monkeypatch):
    s = _scanner()
    s._hwnd = 555
    monkeypatch.setattr(s, "find_window", lambda: 555)
    monkeypatch.setattr(s, "get_col2_region", lambda: (100, 0, 364, 1728))
    monkeypatch.setattr(s, "click_position",
                        lambda x, y: pytest.fail("native 模式不该动真实鼠标"))

    calls = []
    monkeypatch.setattr(native_win, "post_click",
                        lambda hwnd, x, y, hold: calls.append((hwnd, x, y)) or True)

    assert s.click_col2_row(129) is True
    assert calls == [(555, 100 + int(364 * s._click_col2_x), 129)]


def test_click_col2_row_falls_back_to_real_mouse(monkeypatch):
    s = _scanner()
    s._hwnd = 555
    monkeypatch.setattr(s, "find_window", lambda: 555)
    monkeypatch.setattr(s, "get_col2_region", lambda: (100, 0, 364, 1728))
    monkeypatch.setattr(native_win, "post_click", lambda *a, **k: False)
    moved = []
    monkeypatch.setattr(s, "click_position", lambda x, y: moved.append((x, y)))

    assert s.click_col2_row(223) is True
    assert len(moved) == 1


def test_focus_native_does_not_steal_foreground(monkeypatch):
    s = _scanner()
    s._hwnd = 555
    monkeypatch.setattr(s, "find_window", lambda: 555)
    shown = []
    monkeypatch.setattr(native_win, "ensure_renderable",
                        lambda hwnd: shown.append(hwnd) or False)
    with patch("wxbot.scanner.win32gui.SetForegroundWindow",
               side_effect=AssertionError("native 模式不该抢前台")):
        assert s.focus() is True
    assert shown == [555]


def test_capture_screen_falls_back_to_mss_when_grab_fails(monkeypatch):
    s = _scanner()
    monkeypatch.setattr(s, "_grab_window_cached", lambda: None)
    monkeypatch.setattr("wxbot.scanner.mss.mss", MagicMock(side_effect=RuntimeError("mss stub")))
    with pytest.raises(RuntimeError, match="mss stub"):
        s.capture_screen((0, 0, 10, 10))


def test_capture_screen_crops_window_bitmap(monkeypatch):
    s = _scanner()
    full = Image.new("RGB", (2019, 1728), (200, 200, 200))
    full.paste(Image.new("RGB", (50, 50), (0, 128, 255)), (100, 200))
    monkeypatch.setattr(s, "_grab_window_cached", lambda: full)
    monkeypatch.setattr(s, "find_window", lambda: 111)
    monkeypatch.setattr("wxbot.scanner.win32gui.GetWindowRect",
                        lambda hwnd: (0, 0, 2019, 1728))

    got = s.capture_screen((100, 200, 50, 50))
    assert got.size == (50, 50)
    assert got.getpixel((0, 0)) == (0, 128, 255)
