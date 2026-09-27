"""Tests for name_region detector module."""
import numpy as np
from PIL import Image
from wxbot.detector.name_region import detect_name_region, has_green_badge


def test_detect_name_region_returns_none_on_blank():
    """All-white image has no text → returns None."""
    img = Image.fromarray(np.full((40, 100, 3), 255, dtype=np.uint8), "RGB")
    assert detect_name_region(img) is None


def test_detect_name_region_finds_text_block():
    """Image with dark text region returns a cropped sub-image."""
    arr = np.full((30, 120, 3), 255, dtype=np.uint8)
    # Simulate name text: dark pixels in the left portion
    for y in range(5, 20):
        for x in range(10, 70):
            arr[y, x] = (50, 50, 50)
    img = Image.fromarray(arr, "RGB")
    result = detect_name_region(img)
    assert result is not None, "Should detect text region"
    assert result.size[0] > 0 and result.size[1] > 0


def test_has_green_badge_true():
    """Image with green pixels returns True."""
    arr = np.full((20, 20, 3), (128, 128, 128), dtype=np.uint8)
    # Paint green badge: G=200, R=50, B=50
    arr[5:15, 2:12, 1] = 200  # G channel
    arr[5:15, 2:12, 0] = 50   # R channel
    arr[5:15, 2:12, 2] = 50   # B channel
    img = Image.fromarray(arr, "RGB")
    assert has_green_badge(img) is True


def test_has_green_badge_false_no_green():
    """Image without green pixels returns False."""
    arr = np.full((20, 20, 3), (128, 128, 128), dtype=np.uint8)
    img = Image.fromarray(arr, "RGB")
    assert has_green_badge(img) is False


def test_has_green_badge_none_input():
    """None input returns False."""
    assert has_green_badge(None) is False


def test_has_green_badge_small_image():
    """Very small image (< 10px) returns False."""
    arr = np.full((5, 5, 3), (50, 200, 50), dtype=np.uint8)
    img = Image.fromarray(arr, "RGB")
    assert has_green_badge(img) is False
