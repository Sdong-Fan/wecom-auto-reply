# wxbot/capture.py
"""截图工具 — 基于 mss（跨平台，沿用 wecom-cs-mano 方案）"""

from typing import Optional, Tuple
import mss
from PIL import Image


def capture_screen(region: Optional[Tuple[int, int, int, int]] = None) -> Image.Image:
    """截取屏幕或指定区域

    Args:
        region: (left, top, width, height)，None=全屏

    Returns:
        PIL Image (RGB)
    """
    with mss.mss() as sct:
        if region:
            monitor = {
                "left": region[0], "top": region[1],
                "width": region[2], "height": region[3],
            }
        else:
            monitor = sct.monitors[1]

        screenshot = sct.grab(monitor)
        return Image.frombytes("RGB", screenshot.size, screenshot.rgb)


def capture_region(left: int, top: int, width: int, height: int) -> Image.Image:
    """截取指定矩形区域"""
    return capture_screen((left, top, width, height))
