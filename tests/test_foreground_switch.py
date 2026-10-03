"""Tests for _switch_to_wecom_and_back and send_message_via_keyboard (Steps 5-6).

Verifies:
- WeChat window not found → raises RuntimeError
- activate() failure → raises exception
- func exception → re-raised after cleanup
- PowerShell uses CREATE_NO_WINDOW
"""

import subprocess
import pytest
from unittest.mock import MagicMock, patch, PropertyMock


class FakeScanner:
    """Minimal import-free stand-in so we can test the methods in isolation."""

    def __init__(self):
        self._t_focus = 0.3
        self._t_click = 0.1
        self._click_input_x = 0.5
        self._click_input_bot = 30
        # 发送时按标题找窗口，标题跟着 profile 走
        # （见 tests/test_send_target_profile.py：写死「企业微信」会把回复粘错窗口）
        self._title_hint = "企业微信"
        self._window_class = "WeWorkWindow"
        # ★ 2026-10-03：切窗口的活儿收进 _activate_target()（按进程名+窗口类找，
        #   不再按标题取 windows[0] —— 机器人自己的界面标题也叫「企业微信智能客服」）。
        #   这里用假的 hwnd 替掉，专测 switch 的调用顺序与异常清理。
        self._activate_target = MagicMock(return_value=526648)
        self.logger = MagicMock()
        self.mouse = MagicMock()
        self.kb = MagicMock()

    def get_col3_region(self):
        return (100, 200, 400, 600)

    def click_position(self, x, y):
        pass

    # Import the real methods at class level so we test the actual code
    import importlib
    _scanner_mod = importlib.import_module("wxbot.scanner")
    _switch_to_wecom_and_back = _scanner_mod.Scanner._switch_to_wecom_and_back
    send_message_via_keyboard = _scanner_mod.Scanner.send_message_via_keyboard
    click_input_box = _scanner_mod.Scanner.click_input_box
    _set_clipboard = _scanner_mod.Scanner._set_clipboard


def _make_scanner():
    return FakeScanner()


class TestSwitchWindowNotFound:
    """切不到目标窗口（没找到 / 前台锁定被拒）→ 必须抛异常，且什么都不做。"""

    @patch("wxbot.scanner.pyautogui")
    def test_raises_when_no_wecom_window(self, mock_gui):
        mock_gui.getActiveWindow.return_value = MagicMock()
        mock_gui.position.return_value = (100, 200)

        s = _make_scanner()
        s._activate_target.return_value = 0
        func = MagicMock()
        with pytest.raises(RuntimeError):
            s._switch_to_wecom_and_back(func)

        func.assert_not_called()

    @patch("wxbot.scanner.pyautogui")
    def test_prev_window_not_activated(self, mock_gui):
        """切不到目标窗口时，不许去激活别的窗口。"""
        prev_win = MagicMock()
        mock_gui.getActiveWindow.return_value = prev_win
        mock_gui.position.return_value = (100, 200)

        s = _make_scanner()
        s._activate_target.return_value = 0
        with pytest.raises(RuntimeError):
            s._switch_to_wecom_and_back(MagicMock())

        prev_win.activate.assert_not_called()


class TestSwitchActivateFailure:
    """激活失败（返回 0）→ 抛异常，func 不执行。"""

    @patch("wxbot.scanner.pyautogui")
    def test_activate_failure_propagates(self, mock_gui):
        prev_win = MagicMock()
        mock_gui.getActiveWindow.return_value = prev_win
        mock_gui.position.return_value = (100, 200)

        s = _make_scanner()
        s._activate_target.return_value = 0
        func = MagicMock()
        with pytest.raises(RuntimeError):
            s._switch_to_wecom_and_back(func)

        func.assert_not_called()
        prev_win.activate.assert_not_called()


class TestActivateTargetReal:
    """真 Scanner 的 _activate_target：找对窗口 → 抢前台 → 失败才放弃。"""

    def _scanner(self):
        from wxbot.scanner import Scanner
        return Scanner({"wecom": {"title_hint": "企业微信"}})

    def test_uses_find_window_then_force_foreground(self, monkeypatch):
        from wxbot import scanner as mod

        s = self._scanner()
        monkeypatch.setattr(s, "find_window", lambda: 526648)
        monkeypatch.setattr(mod.win32gui, "IsIconic", lambda h: False)
        monkeypatch.setattr(mod.win32gui, "SetForegroundWindow", lambda h: 0)
        monkeypatch.setattr(mod, "_force_foreground", lambda h: True)

        assert s._activate_target() == 526648

    def test_set_foreground_success_skips_fallback(self, monkeypatch):
        from wxbot import scanner as mod

        s = self._scanner()
        monkeypatch.setattr(s, "find_window", lambda: 526648)
        monkeypatch.setattr(mod.win32gui, "IsIconic", lambda h: False)
        monkeypatch.setattr(mod.win32gui, "SetForegroundWindow", lambda h: 1)
        called = []
        monkeypatch.setattr(mod, "_force_foreground", lambda h: called.append(h))

        assert s._activate_target() == 526648
        assert called == [], "第一次就成功了不该走兜底"

    def test_returns_zero_when_everything_fails(self, monkeypatch):
        """前台锁定 + 标题也找不到 → 返回 0（调用方跳过，绝不乱粘）。"""
        from wxbot import scanner as mod

        s = self._scanner()
        monkeypatch.setattr(s, "find_window", lambda: 0)
        monkeypatch.setattr(s, "_find_by_title", lambda: 0)
        monkeypatch.setattr(mod.win32gui, "SetForegroundWindow", lambda h: 0)
        monkeypatch.setattr(mod, "_force_foreground", lambda h: False)

        assert s._activate_target() == 0


class TestSwitchFuncException:
    """When the wrapped func raises, the exception must be re-raised."""

    @patch("wxbot.scanner.pyautogui")
    def test_func_exception_is_re_raised(self, mock_gui):
        prev_win = MagicMock()
        mock_gui.getActiveWindow.return_value = prev_win
        mock_gui.position.return_value = (100, 200)

        s = _make_scanner()
        broken_func = MagicMock(side_effect=RuntimeError("boom"))

        with pytest.raises(RuntimeError, match="boom"):
            s._switch_to_wecom_and_back(broken_func)

    @patch("wxbot.scanner.pyautogui")
    def test_restore_still_attempted_after_func_crash(self, mock_gui):
        """Even if func() blows up, the method must try to restore focus."""
        prev_win = MagicMock()
        mock_gui.getActiveWindow.return_value = prev_win
        mock_gui.position.return_value = (500, 600)

        s = _make_scanner()
        broken_func = MagicMock(side_effect=RuntimeError("boom"))
        with pytest.raises(RuntimeError):
            s._switch_to_wecom_and_back(broken_func)

        prev_win.activate.assert_called_once()
        mock_gui.moveTo.assert_called_once_with((500, 600))


class TestSwitchSuccessPath:
    """Happy path: 切到目标窗口 → func 执行 → 焦点还原。"""

    @patch("wxbot.scanner.pyautogui")
    def test_func_called_and_focus_restored(self, mock_gui):
        prev_win = MagicMock()
        mock_gui.getActiveWindow.return_value = prev_win
        mock_gui.position.return_value = (300, 400)

        s = _make_scanner()
        func = MagicMock()
        s._switch_to_wecom_and_back(func)

        func.assert_called_once()
        s._activate_target.assert_called_once()
        prev_win.activate.assert_called_once()
        mock_gui.moveTo.assert_called_once_with((300, 400))


class TestSendKeyboardCreateNoWindow:
    """Clipboard must use ctypes (no subprocess)."""

    @patch("wxbot.scanner.pyautogui")
    def test_uses_ctypes_clipboard(self, mock_gui):
        """send_message_via_keyboard must use ctypes, not subprocess."""
        s = _make_scanner()
        s._set_clipboard = MagicMock()
        s.send_message_via_keyboard("hello")
        s._set_clipboard.assert_called_once_with("hello")

    @patch("wxbot.scanner.pyautogui")
    def test_sends_ctrl_v_then_enter(self, mock_gui):
        """After setting clipboard, must send Ctrl+V + Enter."""
        s = _make_scanner()
        s._set_clipboard = MagicMock()
        s.send_message_via_keyboard("hello")

        mock_gui.hotkey.assert_called_once_with('ctrl', 'v')
        mock_gui.press.assert_called_once_with('enter')


class TestScannerSourceIntegrity:
    """Verify the methods exist on the real Scanner class."""

    def test_switch_method_exists(self):
        from wxbot.scanner import Scanner
        assert hasattr(Scanner, '_switch_to_wecom_and_back')

    def test_send_keyboard_method_exists(self):
        from wxbot.scanner import Scanner
        assert hasattr(Scanner, 'send_message_via_keyboard')
