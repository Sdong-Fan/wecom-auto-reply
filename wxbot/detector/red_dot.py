"""Red dot detection in WeChat chat list column.

Scans the left portion of column 2 (chat list) for red pixel clusters
indicating unread messages. Extracted from the original MessageDetector class.

Original signature: detect_red_dots(self, img: Image.Image) -> List[int]
New signature: detect_red_dots(img: Image.Image, config: dict = None) -> List[DetectorResult]
"""
from typing import List

from PIL import Image
import numpy as np

from wxbot.detector import DetectorResult


# Default thresholds (overridable via config dict)
DEFAULTS = {
    "scan_width_ratio": 0.05,
    "scan_width_min_px": 20,
    "r_min": 90,
    "r_max": 220,
    "gb_max": 130,
    "min_pixels": 2,
    "red_excess_g": 20,
    "red_excess_b": 20,
    "cluster_gap": 5,
    "cluster_min_points": 2,
}


def detect_red_dots(img: Image.Image, config: dict = None) -> List[DetectorResult]:
    """Detect red dot clusters in the chat list screenshot.

    Scans the left ``scan_width_ratio`` portion of the image for red pixels
    (R between r_min/r_max, G/B below gb_max, R exceeding G/B by excess
    thresholds).  Detected pixels are clustered by Y proximity and filtered
    by minimum cluster size.

    Args:
        img: Column-2 screenshot as PIL Image (RGB).
        config: Optional dict overriding any DEFAULTS key.

    Returns:
        List of DetectorResult, one per red-dot cluster.  The ``metadata``
        dict carries ``y_center`` (int) and ``cluster_size`` (int).
    """
    cfg = dict(DEFAULTS)
    if config:
        cfg.update(config)

    scan_w_ratio = cfg["scan_width_ratio"]
    scan_w_min = cfg["scan_width_min_px"]
    r_min = cfg["r_min"]
    r_max = cfg["r_max"]
    gb_max = cfg["gb_max"]
    min_px = cfg["min_pixels"]
    red_excess_g = cfg["red_excess_g"]
    red_excess_b = cfg["red_excess_b"]
    cluster_gap = cfg["cluster_gap"]
    cluster_min = cfg["cluster_min_points"]

    pixels = img.load()
    w, h = img.size
    scan_w = max(int(w * scan_w_ratio), scan_w_min)

    dots = []
    for y in range(h):
        row_dots = []
        for x in range(scan_w):
            r, g, b = pixels[x, y][:3]
            if (r_min < r < r_max
                    and g < gb_max and b < gb_max
                    and r > g + red_excess_g
                    and r > b + red_excess_b):
                row_dots.append((x, y))
        if len(row_dots) >= min_px:
            dots.extend(row_dots)

    if not dots:
        return []

    dots.sort(key=lambda d: d[1])
    clusters, cur = [], [dots[0]]
    for i in range(1, len(dots)):
        if abs(dots[i][1] - cur[-1][1]) < cluster_gap:
            cur.append(dots[i])
        else:
            if len(cur) >= cluster_min:
                y_center = sum(d[1] for d in cur) // len(cur)
                y_min = min(d[1] for d in cur)
                y_max = max(d[1] for d in cur)
                clusters.append(DetectorResult(
                    detected=True,
                    confidence=_compute_confidence(cur, cluster_min),
                    bbox=(0, y_min, scan_w, y_max),
                    text="",
                    metadata={"y_center": y_center, "cluster_size": len(cur)},
                ))
            cur = [dots[i]]
    if len(cur) >= cluster_min:
        y_center = sum(d[1] for d in cur) // len(cur)
        y_min = min(d[1] for d in cur)
        y_max = max(d[1] for d in cur)
        clusters.append(DetectorResult(
            detected=True,
            confidence=_compute_confidence(cur, cluster_min),
            bbox=(0, y_min, scan_w, y_max),
            text="",
            metadata={"y_center": y_center, "cluster_size": len(cur)},
        ))

    return clusters


def _compute_confidence(cluster: list, min_points: int) -> float:
    """Heuristic confidence: larger clusters relative to minimum = higher confidence."""
    ratio = len(cluster) / max(min_points, 1)
    if ratio >= 10:
        return 1.0
    elif ratio >= 5:
        return 0.9
    elif ratio >= 3:
        return 0.8
    elif ratio >= 2:
        return 0.6
    return 0.4


# Legacy-compatible wrapper returning plain y-centers
def detect_red_dots_legacy(img: Image.Image, config: dict = None) -> List[int]:
    """Return list of Y-center positions (original MessageDetector format)."""
    results = detect_red_dots(img, config)
    return [r.metadata["y_center"] for r in results]
