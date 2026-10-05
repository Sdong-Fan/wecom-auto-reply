"""Detector package - unified interface for all WeChat UI detectors.

Each detector module provides standalone functions that return DetectorResult
or lists of DetectorResult. The original MessageDetector class in detector.py
delegates to these functions for backward compatibility.
"""
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class DetectorResult:
    """Unified return type for all detector modules.

    Attributes:
        detected: Whether the target was found.
        confidence: Detection confidence 0.0-1.0.
        bbox: Bounding box (x1, y1, x2, y2) in pixel coordinates, or None.
        text: OCR-extracted text, if applicable, empty string otherwise.
        metadata: Detector-specific additional data (e.g. cluster_id, pixel_color).
    """
    detected: bool
    confidence: float = 0.0
    bbox: Optional[tuple[int, int, int, int]] = None
    text: str = ""
    metadata: dict = field(default_factory=dict)


# Re-export original data structures for backward compatibility
@dataclass
class Bubble:
    """Bubble information - migrated from detector.py."""
    top: int
    bottom: int
    is_blue: bool
    image: Optional["Image.Image"] = None
    nick_image: Optional["Image.Image"] = None
    text: str = ""


@dataclass
class ChatState:
    """Chat state - migrated from detector.py."""
    has_red_dot: bool = False
    red_dot_positions: List[int] = field(default_factory=list)
    customer_name: str = ""
    is_customer: bool = False
    unreplied_messages: List[str] = field(default_factory=list)
    last_reply_is_ours: bool = False
    bubbles: List[Bubble] = field(default_factory=list)
    has_blue: bool = False


from wxbot.detector.red_dot import detect_red_dots, detect_red_dots_legacy
from wxbot.detector.bubble import (  # noqa: E402
    is_blue_pixel, is_gray_pixel, mask_bubble, extract_bubbles,
)
from wxbot.detector.name_region import (  # noqa: E402
    detect_name_region, has_green_badge,
)
from wxbot.detector.customer_id import (  # noqa: E402
    is_customer_name, clean_name_text, is_customer,
    has_stop_keyword, contains_own_reply,
)
from wxbot.detector.ocr_engine import (  # noqa: E402
    extract_text, strip_ocr_artifacts, clear_ocr_cache,
)
from wxbot.detector.dedup import (  # noqa: E402
    ImageHashCache, ClickCooldown, MessageDedup, ReplyCooldown,
)

# Re-import MessageDetector from the original .py file (now wrapped)
# so ``from wxbot.detector import MessageDetector`` continues to work.
from wxbot.detector._message_detector import (  # noqa: E402
    MessageDetector,
)

__all__ = [
    "DetectorResult", "Bubble", "ChatState",
    "detect_red_dots", "detect_red_dots_legacy",
    "is_blue_pixel", "is_gray_pixel", "mask_bubble", "extract_bubbles",
    "detect_name_region", "has_green_badge",
    "is_customer_name", "clean_name_text", "is_customer",
    "has_stop_keyword", "contains_own_reply",
    "extract_text", "strip_ocr_artifacts", "clear_ocr_cache",
    "ImageHashCache", "ClickCooldown", "MessageDedup", "ReplyCooldown",
    "MessageDetector",
]
