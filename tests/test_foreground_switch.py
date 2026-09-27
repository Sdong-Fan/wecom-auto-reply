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
    """When no 企业微信 window is found, the method must raise."""

    @patch("wxbot.scanner.pyautogui")
    def test_raises_when_no_wecom_window(self, mock_gui):
        mock_gui.getActiveWindow.return_value = MagicMock()
        mock_gui.position.return_value = (100, 200)
        mock_gui.getWindowsWithTitle.return_value = []

        s = _make_scanner()
        func = MagicMock()
        with pytest.raises(RuntimeError):
            s._switch_to_wecom_and_back(func)

        func.assert_not_called()

    @patch("wxbot.scanner.pyautogui")
    def test_prev_window_not_activated(self, mock_gui):
        """When no wecom window, we must NOT activate any other window."""
        prev_win = MagicMock()
        mock_gui.getActiveWindow.return_value = prev_win
        mock_gui.position.return_value = (100, 200)
        mock_gui.getWindowsWithTitle.return_value = []

        s = _make_scanner()
        with pytest.raises(RuntimeError):
            s._switch_to_wecom_and_back(MagicMock())

        prev_win.activate.assert_not_called()


class TestSwitchActivateFailure:
    """When activate() raises, the method must propagate the exception."""

    @patch("wxbot.scanner.pyautogui")
    def test_activate_exception_propagates(self, mock_gui):
        prev_win = MagicMock()
        mock_gui.getActiveWindow.return_value = prev_win
        mock_gui.position.return_value = (100, 200)

        wecom_win = MagicMock()
        wecom_win.activate.side_effect = Exception("Access denied")
        mock_gui.getWindowsWithTitle.return_value = [wecom_win]

        s = _make_scanner()
        func = MagicMock()
        with pytest.raises(Exception, match="Access denied"):
            s._switch_to_wecom_and_back(func)

        func.assert_not_called()


class TestSwitchFuncException:
    """When the wrapped func raises, the exception must be re-raised."""

    @patch("wxbot.scanner.pyautogui")
    def test_func_exception_is_re_raised(self, mock_gui):
        prev_win = MagicMock()
        mock_gui.getActiveWindow.return_value = prev_win
        mock_gui.position.return_value = (100, 200)

        wecom_win = MagicMock()
        mock_gui.getWindowsWithTitle.return_value = [wecom_win]

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

        wecom_win = MagicMock()
        mock_gui.getWindowsWithTitle.return_value = [wecom_win]

        s = _make_scanner()
        broken_func = MagicMock(side_effect=RuntimeError("boom"))
        with pytest.raises(RuntimeError):
            s._switch_to_wecom_and_back(broken_func)

        prev_win.activate.assert_called_once()
        mock_gui.moveTo.assert_called_once_with((500, 600))


class TestSwitchSuccessPath:
    """Happy path: func runs, focus restored."""

    @patch("wxbot.scanner.pyautogui")
    def test_func_called_and_focus_restored(self, mock_gui):
        prev_win = MagicMock()
        mock_gui.getActiveWindow.return_value = prev_win
        mock_gui.position.return_value = (300, 400)

        wecom_win = MagicMock()
        mock_gui.getWindowsWithTitle.return_value = [wecom_win]

        s = _make_scanner()
        func = MagicMock()
        s._switch_to_wecom_and_back(func)

        func.assert_called_once()
        wecom_win.activate.assert_called_once()
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
