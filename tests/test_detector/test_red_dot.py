"""Tests for red_dot detector module."""
import numpy as np
from PIL import Image
from wxbot.detector.red_dot import detect_red_dots


def _make_gray(w: int = 50, h: int = 100) -> Image.Image:
    """Create a gray PIL Image for testing."""
    arr = np.full((h, w, 3), (128, 128, 128), dtype=np.uint8)
    return Image.fromarray(arr, "RGB")


def test_empty_on_gray_image():
    """All-gray image returns empty list."""
    img = _make_gray()
    result = detect_red_dots(img)
    assert result == []


def test_detects_single_red_cluster():
    """A red pixel cluster (R>90, G/B<130, R>G+20, R>B+20) is detected."""
    arr = np.full((100, 50, 3), (128, 128, 128), dtype=np.uint8)
    # Paint a red dot cluster: R=200, G=50, B=50, 4px tall
    arr[40:44, 0:14, 0] = 200  # R
    arr[40:44, 0:14, 1] = 50   # G
    arr[40:44, 0:14, 2] = 50   # B
    img = Image.fromarray(arr, "RGB")
    result = detect_red_dots(img)
    assert len(result) >= 1, f"Expected at least 1 detection, got {len(result)}"
    assert result[0].detected is True
    assert result[0].confidence > 0.0


def test_detects_multiple_separated_clusters():
    """Multiple red clusters with gaps > 5px are detected separately."""
    arr = np.full((200, 50, 3), (128, 128, 128), dtype=np.uint8)
    for y_start in [20, 80, 140]:
        arr[y_start:y_start + 4, 0:14, 0] = 200
        arr[y_start:y_start + 4, 0:14, 1] = 50
        arr[y_start:y_start + 4, 0:14, 2] = 50
    img = Image.fromarray(arr, "RGB")
    result = detect_red_dots(img)
    assert len(result) == 3, f"Expected 3, got {len(result)}"


def test_merges_nearby_rows_into_single_cluster():
    """Red dots within 5px vertically are merged into one cluster."""
    arr = np.full((50, 50, 3), (128, 128, 128), dtype=np.uint8)
    # Three rows with 2px gap between them
    for y in [20, 22, 24]:
        arr[y:y + 1, 0:14, 0] = 200
        arr[y:y + 1, 0:14, 1] = 50
        arr[y:y + 1, 0:14, 2] = 50
    img = Image.fromarray(arr, "RGB")
    result = detect_red_dots(img)
    assert len(result) == 1, f"Expected 1 merged cluster, got {len(result)}"


def test_returns_y_center_in_metadata():
    """Each result includes y_center in metadata."""
    arr = np.full((100, 50, 3), (128, 128, 128), dtype=np.uint8)
    arr[40:44, 0:14, 0] = 200
    arr[40:44, 0:14, 1] = 50
    arr[40:44, 0:14, 2] = 50
    img = Image.fromarray(arr, "RGB")
    result = detect_red_dots(img)
    assert len(result) >= 1
    assert "y_center" in result[0].metadata
    assert 40 <= result[0].metadata["y_center"] <= 44
