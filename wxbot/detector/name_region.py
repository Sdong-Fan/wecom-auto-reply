"""Name-region detection in WeChat chat-list rows.

Dynamically locates the contact-name area within a row screenshot
by finding dark-pixel text regions and applying width-change heuristics
to separate the name from message-preview text.

Also provides green-badge detection for identifying external contacts.
"""
from typing import Optional

import numpy as np
from PIL import Image


NAME_REGION_DEFAULTS = {
    "text_rgb_threshold": 100,
    "min_dark_pixels": 3,
    "width_change_ratio": 0.5,
    "min_width_ratio": 0.15,
    "min_height_px": 5,
    "crop_padding_left": 20,
    "crop_padding_right": 20,
    "crop_padding_top": 1,
    "crop_padding_bottom": 2,
}

GREEN_BADGE_DEFAULTS = {
    "min_green": 100,
    "green_excess_ratio": 1.3,
    "min_green_pixels": 10,
}


def detect_name_region(
    row_img: Image.Image, config: dict = None
) -> Optional[Image.Image]:
    """Dynamically locate and crop the contact-name region from a chat-list row.

    Args:
        row_img: Screenshot of a single chat-list row as PIL Image (RGB).
        config: Optional dict overriding ``NAME_REGION_DEFAULTS`` keys.

    Returns:
        Cropped name-region Image, or ``None`` if no text region found.
    """
    cfg = dict(NAME_REGION_DEFAULTS)
    if config:
        cfg.update(config)

    arr = np.array(row_img.convert("RGB"))
    h, w = arr.shape[:2]

    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    threshold = cfg["text_rgb_threshold"]
    is_text = (r < threshold) | (g < threshold) | (b < threshold)

    row_dark = np.sum(is_text, axis=1)
    row_left = []
    row_right = []
    for y in range(h):
        text_indices = np.where(is_text[y, :])[0]
        if len(text_indices) > 0:
            row_left.append(int(text_indices[0]))
            row_right.append(int(text_indices[-1]))
        else:
            row_left.append(-1)
            row_right.append(-1)

    text_regions = []
    in_text = False
    text_start = 0
    prev_width = 0
    for y in range(h):
        if row_dark[y] > cfg["min_dark_pixels"]:
            if not in_text:
                text_start = y
                in_text = True
            text_indices = np.where(is_text[y, :])[0]
            if len(text_indices) > 0:
                curr_width = int(text_indices[-1] - text_indices[0])
            else:
                curr_width = 0
            if prev_width > 0 and (
                curr_width < prev_width * cfg["width_change_ratio"]
                or curr_width > prev_width * (1 + cfg["width_change_ratio"])
            ):
                text_regions.append((text_start, y - 1))
                text_start = y
            prev_width = curr_width
        else:
            if in_text:
                text_regions.append((text_start, y - 1))
                in_text = False
                prev_width = 0
    if in_text:
        text_regions.append((text_start, h - 1))

    min_width = w * cfg["min_width_ratio"]
    for start, end in text_regions:
        has_wide_row = False
        for y in range(start, end + 1):
            if row_right[y] >= 0:
                curr_width = row_right[y] - row_left[y]
                if curr_width >= min_width:
                    has_wide_row = True
                    break
        if has_wide_row and (end - start + 1) >= cfg["min_height_px"]:
            text_region = is_text[start : end + 1, :]
            col_text = np.sum(text_region, axis=0)
            text_cols = np.where(col_text > 0)[0]
            if len(text_cols) == 0:
                continue
            left = max(0, int(text_cols[0]) - cfg["crop_padding_left"])
            right = min(w, int(text_cols[-1]) + cfg["crop_padding_right"])
            top = max(0, start - cfg["crop_padding_top"])
            bottom = min(h, end + cfg["crop_padding_bottom"])
            return row_img.crop((left, top, right, bottom))

    return None


def has_green_badge(img: Image.Image, config: dict = None) -> bool:
    """Check whether a contact-name image contains a green external-contact badge.

    Green pixels: G > min_green AND G > R * excess AND G > B * excess.

    Args:
        img: Cropped name-region image, or None.
        config: Optional dict overriding ``GREEN_BADGE_DEFAULTS`` keys.

    Returns:
        True if enough green pixels found.
    """
    if img is None:
        return False
    cfg = dict(GREEN_BADGE_DEFAULTS)
    if config:
        cfg.update(config)

    pixels = img.load()
    w, h = img.size
    if w < 10 or h < 10:
        return False
    min_green = cfg["min_green"]
    excess = cfg["green_excess_ratio"]
    min_px = cfg["min_green_pixels"]
    green_count = 0
    for y in range(h):
        for x in range(w):
            r, g, b = pixels[x, y][:3]
            if g > min_green and g > r * excess and g > b * excess:
                green_count += 1
                if green_count > min_px:
                    return True
    return False
