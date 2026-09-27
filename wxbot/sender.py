# wxbot/sender.py
"""键盘控制 — pynput 剪贴板粘贴 + 回车发送"""

import subprocess
import time
import platform
import logging
from pynput.keyboard import Key, Controller

logger = logging.getLogger(__name__)


class MessageSender:
    """通过剪贴板粘贴 + 回车在企微中发送消息"""

    def __init__(self):
        self.keyboard = Controller()

    def send(self, text: str):
        """粘贴文本并回车发送"""
        self._write_clipboard(text)
        time.sleep(0.1)
        self._paste()
        time.sleep(0.2)
        self._press_enter()
        logger.info(f"Sent: {text[:60]}...")

    def _write_clipboard(self, text: str):
        system = platform.system()
        if system == "Windows":
            # base64 编码避免引号等特殊字符破坏 PowerShell 命令
            import base64
            encoded = base64.b64encode(text.encode('utf-8')).decode('ascii')
            cmd = (
                f'$bytes = [Convert]::FromBase64String("{encoded}");'
                f'[System.Windows.Forms.Clipboard]::SetText('
                f'[System.Text.Encoding]::UTF8.GetString($bytes))'
            )
            subprocess.run(
                ["powershell", "-command",
                 f'Add-Type -AssemblyName System.Windows.Forms; {cmd}'],
                capture_output=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        elif system == "Darwin":
            subprocess.run(
                ["pbcopy"], input=text.encode("utf-8"),
                capture_output=True, timeout=3,
            )
        else:
            subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=text.encode("utf-8"),
                capture_output=True, timeout=3,
            )

    def _paste(self):
        self.keyboard.press(Key.ctrl)
        self.keyboard.press("v")
        self.keyboard.release("v")
        self.keyboard.release(Key.ctrl)

    def _press_enter(self):
        self.keyboard.press(Key.enter)
        self.keyboard.release(Key.enter)

    def press_enter(self):
        """只按回车（用于切换会话等）"""
        self._press_enter()
