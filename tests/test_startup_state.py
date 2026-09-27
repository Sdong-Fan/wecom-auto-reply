"""启动状态与界面反馈：默认**未启动** + 常驻「设置」按钮 + 状态横幅。

为什么改：原来打开程序就自动开始扫描；目标软件没开时 `_run_scan` 裸 return，
界面还写着"运行中"、日志一个字都没有 —— 用户完全不知道为什么客户没收到回复。
"""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _bare_window():
    """绕开 Tk 拿一个 MainWindow 实例（只测逻辑，不弹窗）。"""
    from gui.main_window import MainWindow
    w = MainWindow.__new__(MainWindow)
    w._paused = True
    w._pause_btn = MagicMock()
    w._status_label = MagicMock()
    w._banner = MagicMock()
    w.on_pause = None
    w.on_resume = None
    return w


# ── 默认未启动 ────────────────────────────────────────────────────────

def test_default_is_stopped():
    """默认未启动；只有显式设了 WECOM_AUTOSTART=1 才开机即跑。"""
    src = (ROOT / "gui/main_window.py").read_text(encoding="utf-8")
    assert "self._paused = not self._autostart" in src, \
        "默认必须是未启动，不能一打开就扫描"
    assert 'WECOM_AUTOSTART' in src
    assert 'text="状态: 运行中" if self._autostart else "状态: 未启动"' in src, \
        "状态栏初值要跟着 autostart 走"
    assert 'self._paused = not self._autostart' in src


def test_pause_button_starts_as_start():
    src = (ROOT / "gui/main_window.py").read_text(encoding="utf-8")
    assert 'text="停止" if self._autostart else "开始"' in src


def test_toggle_from_stopped_to_running():
    w = _bare_window()
    w.on_resume = MagicMock()
    w._toggle_pause()
    assert w._paused is False
    w._pause_btn.config.assert_called_with(text="停止")
    w._status_label.config.assert_called_with(text="状态: 运行中")
    w.on_resume.assert_called_once()


def test_toggle_from_running_to_stopped_shows_banner():
    w = _bare_window()
    w._paused = False
    w.on_pause = MagicMock()
    w._toggle_pause()
    assert w._paused is True
    w._pause_btn.config.assert_called_with(text="开始")
    w.on_pause.assert_called_once()
    assert "未启动" in w._banner.config.call_args.kwargs["text"]


# ── 常驻设置按钮 ──────────────────────────────────────────────────────

def test_settings_button_always_present_and_wired():
    src = (ROOT / "gui/main_window.py").read_text(encoding="utf-8")
    assert 'text="设置"' in src, "「设置」必须常驻头部"
    assert "on_settings" in src
    m = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "MainWindow(on_settings=" in m, "main.py 必须把设置回调传进去"


def test_settings_callback_is_invoked():
    from gui.main_window import MainWindow
    w = MainWindow.__new__(MainWindow)
    w.on_settings = MagicMock()
    MainWindow._on_settings_clicked(w)
    w.on_settings.assert_called_once()


def test_settings_button_works_even_when_not_configured():
    """用户的要求：配置完之后也要能重新进设置，所以按钮不受状态影响。"""
    src = (ROOT / "gui/main_window.py").read_text(encoding="utf-8")
    btn = src[src.index('text="设置"'):]
    btn = btn[:btn.index("\n")]
    assert "state=" not in btn, "设置按钮不能被置灰"


# ── 状态横幅 ──────────────────────────────────────────────────────────

def test_banner_hidden_when_empty():
    w = _bare_window()
    w.set_banner("")
    w._banner.pack_forget.assert_called_once()


def test_banner_levels_have_distinct_colors():
    w = _bare_window()
    w.set_banner("出错了", level="error")
    err = w._banner.config.call_args.kwargs["foreground"]
    w.set_banner("提醒", level="warn")
    warn = w._banner.config.call_args.kwargs["foreground"]
    assert err != warn


# ── 静默失败修复 ──────────────────────────────────────────────────────

def test_target_missing_is_reported_not_silent():
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    idx = src.index("if not scanner.is_running():")
    body = src[idx:idx + 400]
    assert "_notify_target_missing()" in body, \
        "目标软件没找到时必须有提示，不能裸 return"
    assert "def _notify_target_missing" in src
    assert "set_banner" in src


def test_target_missing_log_is_throttled():
    """每 3 秒刷一条日志会刷爆，必须按分钟节流。"""
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    seg = src[src.index("def _notify_target_missing"):]
    seg = seg[:seg.index("def _run_scan")]
    assert "_last_missing_warn" in seg and "60" in seg


def test_startup_shows_banner():
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "未启动。当前：" in src, "启动时必须告诉用户当前通道和下一步"
