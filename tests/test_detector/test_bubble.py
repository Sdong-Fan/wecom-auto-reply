"""Tests for bubble detector module."""
import numpy as np
from PIL import Image
from wxbot.detector.bubble import is_blue_pixel, is_gray_pixel, mask_bubble


def _make_rgb_image(w, h, r, g, b) -> Image.Image:
    arr = np.full((h, w, 3), (r, g, b), dtype=np.uint8)
    return Image.fromarray(arr, "RGB")


def test_is_blue_pixel_true():
    """Pixel with B > R+15 and B >= G and B >= 200 is blue."""
    assert is_blue_pixel(100, 200, 220) is True  # B=220 > R=100+15, B >= G


def test_is_blue_pixel_false_gray():
    """Neutral gray pixel is not blue."""
    assert is_blue_pixel(200, 200, 200) is False


def test_is_blue_pixel_false_red():
    """Red pixel is not blue."""
    assert is_blue_pixel(255, 50, 50) is False


def test_is_gray_pixel_true():
    """Pixel with RGB all 230-254 and channel diff < 10 is gray."""
    assert is_gray_pixel(240, 238, 242) is True


def test_is_gray_pixel_false_varied():
    """Pixel with large channel differences is not gray."""
    assert is_gray_pixel(100, 200, 50) is False


def test_is_gray_pixel_false_dark():
    """Dark pixel is not gray."""
    assert is_gray_pixel(50, 50, 50) is False


def test_mask_bubble_preserves_gray():
    """mask_bubble keeps gray pixels, replaces non-gray with white."""
    arr = np.full((20, 30, 3), (240, 240, 240), dtype=np.uint8)  # gray
    arr[5:15, 5:25, :] = (100, 50, 200)  # non-gray (blue)
    img = Image.fromarray(arr, "RGB")
    masked = mask_bubble(img)
    masked_arr = np.array(masked)
    # Gray region should remain gray
    assert tuple(masked_arr[0, 0]) == (240, 240, 240)
    # Non-gray region should be white
    assert tuple(masked_arr[10, 15]) == (255, 255, 255)


def test_blue_pixel_detection_with_config():
    """Custom config changes detection thresholds."""
    # Default: B > R + 15, B >= G, B >= 200
    assert is_blue_pixel(180, 180, 195) is False  # B not > R+15, B < 200
    custom_cfg = {"b_excess_r": 10, "min_rgb": 190}
    assert is_blue_pixel(180, 180, 195, config=custom_cfg) is True
