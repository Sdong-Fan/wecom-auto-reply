"""Customer identification for WeChat chat-list rows.

Three-layer check:
1. Blue bubble presence in the chat area (agent already replied)
2. Green badge pixels in the contact-name region
3. OCR name text containing ``@微信`` suffix or regex pattern

Also provides name-cleaning to strip system markers.
"""
from typing import List, Optional
import re

from PIL import Image

from wxbot.detector.name_region import has_green_badge

DEFAULT_SUFFIXES = ["@微信", ")微信"]
DEFAULT_PATTERN = r'@[一-鿿]{2,}'
DEFAULT_NON_CUSTOMER_KEYWORDS = [
    "行业资讯", "企业微信日报", "系统通知", "工作通知",
    "日程提醒", "待办提醒", "会议邀请", "打卡提醒",
    "审批通知", "汇报通知", "公告通知",
    "此群为", "群公告", "群成员", "全员群", "群聊",
    "加入了群", "退出了群", "邀请你加入",
]

_NAME_SYS_MARKERS = [
    "[语音]", "[图片]", "[视频]", "[文件]", "[链接]", "[位置]",
    "[红包]", "[名片]", "[小程序]", "[聊天记录]", "[动画表情]",
    "[通话]", "[位置共享]",
]


def clean_name_text(name_text: str) -> str:
    """Strip WeChat system markers from OCR name text, truncate to customer suffix."""
    if not name_text:
        return ""
    for marker in _NAME_SYS_MARKERS:
        name_text = name_text.replace(marker, "")
    name_text = name_text.strip()
    if not name_text:
        return ""
    # Truncate at customer suffix boundary
    for suffix in DEFAULT_SUFFIXES:
        idx = name_text.find(suffix)
        if idx >= 0:
            name_text = name_text[:idx + len(suffix)]
            break
    return name_text.strip()


def is_customer_name(
    ocr_text: str,
    suffixes: List[str] = None,
    pattern: str = None,
) -> bool:
    """Check whether OCR text indicates an external customer (not internal/system).

    Args:
        ocr_text: OCR-extracted text from the name region.
        suffixes: List of suffix strings that mark a customer (default: @微信, )微信).
        pattern: Regex pattern alternative (default: r'@[一-鿿]{2,}').

    Returns:
        True if the text matches any suffix or pattern.
    """
    if not ocr_text:
        return False
    suffixes = suffixes or DEFAULT_SUFFIXES
    pattern = pattern or DEFAULT_PATTERN
    for suffix in suffixes:
        if suffix in ocr_text:
            return True
    if re.search(pattern, ocr_text):
        return True
    return False


def is_customer(
    name_img: Image.Image = None,
    ocr_text: str = "",
    has_blue: bool = False,
    suffixes: List[str] = None,
    pattern: str = None,
    green_config: dict = None,
    non_customer_keywords: List[str] = None,
) -> bool:
    """Three-layer customer verification.

    1. Blue bubble present → definitely a customer (agent replied to someone)
    2. Green badge found in name region → external contact
    3. OCR name matches customer suffix/pattern → external contact
    Fallback: if no system keywords found, treat as customer.

    Args:
        name_img: Cropped name-region image (for green badge check).
        ocr_text: OCR-extracted text of the name.
        has_blue: Whether blue bubbles were found in the chat.
        suffixes, pattern: Customer name detection config.
        green_config: Green badge detection config.
        non_customer_keywords: Keywords that indicate a NON-customer.

    Returns:
        True if the row appears to be a customer.
    """
    non_cust = non_customer_keywords or DEFAULT_NON_CUSTOMER_KEYWORDS

    # Layer 1: blue bubble presence = someone already being replied to
    if has_blue:
        return True

    # Layer 2: green badge = external contact
    if has_green_badge(name_img, config=green_config):
        return True

    # Layer 3: OCR name match
    if is_customer_name(ocr_text, suffixes=suffixes, pattern=pattern):
        return True

    # Fallback: if no system/non-customer keyword found, assume customer
    if ocr_text:
        for kw in non_cust:
            if kw in ocr_text:
                return False
        return True

    return False


def has_stop_keyword(text: str, stop_keywords: List[str] = None) -> bool:
    """Check if text contains a customer request to stop auto-replying."""
    if not text or not stop_keywords:
        return False
    return any(kw in text for kw in stop_keywords)


def contains_own_reply(ocr_text: str, recently_sent: List[str]) -> bool:
    """Check if OCR text matches one of our recently sent replies."""
    if not ocr_text or not recently_sent:
        return False
    return any(sent.strip() in ocr_text for sent in recently_sent if len(sent.strip()) >= 3)
