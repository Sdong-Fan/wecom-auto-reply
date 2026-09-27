"""Tests for customer_id module."""
import numpy as np
from PIL import Image
from wxbot.detector.customer_id import (
    clean_name_text, is_customer_name, is_customer, has_stop_keyword,
)


def test_clean_name_text_strips_voice_marker():
    assert clean_name_text("[语音] 张姐 @微信") == "张姐 @微信"


def test_clean_name_text_empty():
    assert clean_name_text("") == ""
    assert clean_name_text("[语音][图片]") == ""


def test_is_customer_name_with_suffix():
    assert is_customer_name("张姐@微信") is True


def test_is_customer_name_without_suffix():
    assert is_customer_name("张姐") is False


def test_is_customer_name_empty():
    assert is_customer_name("") is False


def test_has_stop_keyword_detected():
    assert has_stop_keyword("请停止回复", ["停止回复", "别发了"]) is True


def test_has_stop_keyword_not_found():
    assert has_stop_keyword("你好啊", ["停止回复"]) is False


def test_is_customer_with_blue_bubble():
    """Blue bubble present = definitely customer (layer 1)."""
    assert is_customer(has_blue=True) is True


def test_is_customer_with_green_badge():
    """Green badge = customer (layer 2)."""
    arr = np.full((20, 20, 3), (128, 128, 128), dtype=np.uint8)
    arr[5:15, 2:12, 1] = 200  # G
    arr[5:15, 2:12, 0] = 50   # R
    arr[5:15, 2:12, 2] = 50   # B
    img = Image.fromarray(arr, "RGB")
    assert is_customer(name_img=img) is True


def test_is_customer_with_name_suffix():
    """@微信 suffix = customer (layer 3)."""
    assert is_customer(ocr_text="张姐@微信") is True
