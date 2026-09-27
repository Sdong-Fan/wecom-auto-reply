"""Bubble detection for WeChat chat area.

Detects blue bubbles (agent's own replies), gray bubbles (customer
messages), and text rows (voice-to-text / non-bubble content).

Extracted from the original MessageDetector.extract_unreplied_bubbles.
"""
from typing import List, Optional, Tuple

from PIL import Image
import numpy as np

from wxbot.detector import DetectorResult


# ── default configs (overridable) ──────────────────────────────────────────

BLUE_DEFAULTS = {
    "b_excess_r": 15,
    "min_rgb": 200,
    "row_threshold_ratio": 0.15,
    "right_edge_ratio": 0.85,
    "max_height_ratio": 0.15,
    "scan_top_limit_ratio": 0.05,
    "scan_bottom_start_ratio": 0.15,
}

GRAY_DEFAULTS = {
    "max_channel_diff": 10,
    "r_range": [230, 254],
    "g_range": [230, 254],
    "b_range": [230, 254],
    "row_threshold_ratio": 0.15,
    "left_edge_ratio": 0.15,
    "max_height_ratio": 0.80,
    "min_height_px": 20,
    "nickname_top_offset": 15,
}

MERGE_GAP = 8
BLUE_MIN_HEIGHT = 15  # thinner = voice-message UI element, ignore
TEXT_MIN_PIXELS = 3
TEXT_LEFT_LIMIT_RATIO = 0.4
TEXT_RGB_THRESHOLD = 150
TEXT_MAX_HEIGHT_RATIO = 0.5


# ── pixel-level helpers ────────────────────────────────────────────────────

def is_blue_pixel(r: int, g: int, b: int, config: dict = None) -> bool:
    """Return True when the pixel belongs to **我方**的气泡。

    两种判据，按 config 选用：
    * 给了 ``my_rgb``（profile 标定出来的主题色）→ 与该色的曼哈顿距离 ≤ ``my_tolerance``。
      这是跨软件通用的那条：微信 PC 是绿气泡、企业微信是蓝气泡，靠颜色本身判，
      不写死"蓝"。容差默认 20（企微对方灰 (228,231,235) 与我方蓝 (201,231,255)
      的距离只有 47，容差给大了会把对方的话当成我方）。
    * 没给 ``my_rgb`` → 旧的企业微信蓝气泡判据（B > R + 15 且 B >= G 且 B >= 200），
      保持既有行为不变。
    """
    cfg = config or BLUE_DEFAULTS
    my_rgb = cfg.get("my_rgb")
    if my_rgb:
        tol = cfg.get("my_tolerance", 20)
        return (abs(r - int(my_rgb[0])) + abs(g - int(my_rgb[1]))
                + abs(b - int(my_rgb[2]))) <= tol
    excess_r = cfg.get("b_excess_r", BLUE_DEFAULTS["b_excess_r"])
    min_rgb = cfg.get("min_rgb", BLUE_DEFAULTS["min_rgb"])
    return b > r + excess_r and b >= g and b >= min_rgb


def is_gray_pixel(r: int, g: int, b: int, config: dict = None) -> bool:
    """Return True when the pixel belongs to a gray (customer) chat bubble.

    Default: each channel in [230, 254] and max channel diff < 10.
    """
    cfg = config or GRAY_DEFAULTS
    rr = cfg.get("r_range", GRAY_DEFAULTS["r_range"])
    gr = cfg.get("g_range", GRAY_DEFAULTS["g_range"])
    br = cfg.get("b_range", GRAY_DEFAULTS["b_range"])
    max_diff = cfg.get("max_channel_diff", GRAY_DEFAULTS["max_channel_diff"])
    return (
        rr[0] <= r <= rr[1]
        and gr[0] <= g <= gr[1]
        and br[0] <= b <= br[1]
        and max(abs(r - g), abs(r - b), abs(g - b)) < max_diff
    )


def mask_bubble(bubble: Image.Image, config: dict = None) -> Image.Image:
    """Mask non-gray pixels to white, leaving only gray bubble content."""
    masked = bubble.copy()
    px = masked.load()
    w, h = masked.size
    for y in range(h):
        for x in range(w):
            if not is_gray_pixel(*px[x, y][:3], config):
                px[x, y] = (255, 255, 255)
    return masked


# ── bubble extraction ──────────────────────────────────────────────────────

def extract_bubbles(
    chat_img: Image.Image,
    blue_cfg: dict = None,
    gray_cfg: dict = None,
) -> Tuple[Optional[List[Tuple[Image.Image, Image.Image]]], bool]:
    """Scan the chat-area image bottom-to-top, returning unreplied bubbles.

    Args:
        chat_img: Screenshot of column 3 (chat area) as PIL Image (RGB).

    Returns:
        (unreplied, has_blue) where *unreplied* is ``None`` or a list of
        ``(bubble_image, nick_image)`` tuples, and *has_blue* indicates
        whether any blue (agent) bubble was found.

    This is a direct extraction from ``MessageDetector.extract_unreplied_bubbles``.
    """
    bc = blue_cfg or BLUE_DEFAULTS
    gc = gray_cfg or GRAY_DEFAULTS

    pixels = chat_img.load()
    w, h = chat_img.size
    if w < 20 or h < 20:
        return None, False

    gray_thr = w * gc.get("row_threshold_ratio", GRAY_DEFAULTS["row_threshold_ratio"])
    blue_thr = w * bc.get("row_threshold_ratio", BLUE_DEFAULTS["row_threshold_ratio"])
    right_edge = int(w * bc.get("right_edge_ratio", BLUE_DEFAULTS["right_edge_ratio"]))
    left_edge = int(w * gc.get("left_edge_ratio", GRAY_DEFAULTS["left_edge_ratio"]))
    max_bubble_h = int(h * bc.get("max_height_ratio", BLUE_DEFAULTS["max_height_ratio"]))
    top_limit = int(h * bc.get("scan_top_limit_ratio", BLUE_DEFAULTS["scan_top_limit_ratio"]))
    bottom_start = int(h * bc.get("scan_bottom_start_ratio", BLUE_DEFAULTS["scan_bottom_start_ratio"]))

    bubbles: List[Tuple[int, int, bool]] = []  # (top, bottom, is_blue)

    # ── row classifiers (closures over threshold values) ───────────────

    def _is_gray_row(y: int) -> bool:
        gray_xs = [x for x in range(w) if is_gray_pixel(*pixels[x, y][:3], gc)]
        return len(gray_xs) >= gray_thr and min(gray_xs, default=w) < left_edge

    left_limit = int(w * TEXT_LEFT_LIMIT_RATIO)

    def _is_text_row(y: int) -> bool:
        count = 0
        for x in range(left_limit):
            r, g, b = pixels[x, y][:3]
            if r < TEXT_RGB_THRESHOLD and g < TEXT_RGB_THRESHOLD and b < TEXT_RGB_THRESHOLD:
                count += 1
                if count >= TEXT_MIN_PIXELS:
                    return True
        return False

    def _is_any_blue_row(y: int) -> bool:
        blue_xs = [x for x in range(w) if is_blue_pixel(*pixels[x, y][:3], bc)]
        return len(blue_xs) >= blue_thr

    # ── bottom-up scan ─────────────────────────────────────────────────

    y = h - 1
    while y > bottom_start:
        if _is_any_blue_row(y):
            bot = y
            while y > top_limit and _is_any_blue_row(y):
                y -= 1
            top = y + 1
            block_h = bot - top
            if block_h >= BLUE_MIN_HEIGHT:
                if block_h <= max_bubble_h:
                    bubbles.append((top, bot, True))
            y -= 1
            continue

        if _is_gray_row(y) and not _is_any_blue_row(y):
            bot = y
            while y > top_limit and _is_gray_row(y) and not _is_any_blue_row(y):
                y -= 1
            top = y + 1
            if bot - top <= h * gc.get("max_height_ratio", GRAY_DEFAULTS["max_height_ratio"]):
                bubbles.append((top, bot, False))
            continue

        if _is_text_row(y) and not _is_any_blue_row(y):
            bot = y
            while y > top_limit and _is_text_row(y) and not _is_any_blue_row(y):
                y -= 1
            top = y + 1
            if bot - top <= h * TEXT_MAX_HEIGHT_RATIO:
                bubbles.append((top, bot, False))
            continue

        y -= 1

    # ── merge adjacent non-blue fragments ──────────────────────────────

    i = len(bubbles) - 1
    while i > 0:
        _top_i, bot_i, blue_i = bubbles[i]
        top_j, _bot_j, blue_j = bubbles[i - 1]
        if not blue_i and not blue_j and (top_j - bot_i) <= MERGE_GAP:
            bubbles[i - 1] = (_top_i, _bot_j, False)
            bubbles.pop(i)
        i -= 1

    has_blue = any(is_blue for _, _, is_blue in bubbles)

    # ── find newest blue bubble ────────────────────────────────────────

    newest_blue_top = None
    for top, _bot, is_blue in bubbles:
        if is_blue and (newest_blue_top is None or top > newest_blue_top):
            newest_blue_top = top

    # ── collect unreplied (below newest blue) ─────────────────────────

    nick_offset = gc.get("nickname_top_offset", GRAY_DEFAULTS["nickname_top_offset"])
    unreplied = []
    for top, bot, is_blue in bubbles:
        if is_blue:
            if bot - top >= BLUE_MIN_HEIGHT:
                break
            continue
        if bot - top < 5:
            continue
        if newest_blue_top is not None and top < newest_blue_top:
            continue
        nick_top = max(0, top - nick_offset)
        bubble_img = chat_img.crop((0, top, w, bot + 1))
        nick_img = chat_img.crop((0, nick_top, w, bot + 1))
        unreplied.append((bubble_img, nick_img))

    return (unreplied if unreplied else None), has_blue
