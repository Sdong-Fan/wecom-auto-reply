# tests/test_bubble_top_anchored.py
"""内容不足一屏时，企业微信把消息**顶部对齐**渲染 —— 底部窗口扫不到。

真实故障（2026-10-05）：客户会话只有一条消息「我想修改收货地址」，
渲染在聊天区 y≈60，而气泡扫描只覆盖视口底部 85%（从 y=208 往上）→
永远判定"无气泡/已回复" → 客户发消息完全没反应。
（同一时间另一个会话内容较长、新消息在底部，所以照常回复 —— 这就是它难查的原因。）

修法：底部窗口一条都没扫到时，**全区域兜底重扫**一次。
"""
from PIL import Image

from wxbot.detector import MessageDetector

W, H = 400, 600
WHITE = (255, 255, 255)
GRAY = (235, 235, 235)


def _det(tmp_path):
    return MessageDetector({"state_dir": str(tmp_path)})


def _chat_at(top: int, height: int = 40):
    """在指定 top 处画一条靠左的客户灰气泡。"""
    img = Image.new("RGB", (W, H), WHITE)
    img.paste(GRAY, (10, top, int(W * 0.6), top + height))
    return img


def test_top_anchored_message_is_detected(tmp_path):
    """顶部对齐的消息必须能被扫到（修复前这里返回 None）。"""
    img = _chat_at(8)
    unreplied, has_blue, replied = _det(tmp_path).extract_bubbles_detail(img)
    assert unreplied is not None, "顶部对齐的消息被漏掉了"
    assert len(unreplied) == 1
    assert has_blue is False
    assert replied == []


def test_near_top_message_is_detected(tmp_path):
    """顶部略靠下（y=150，仍在默认窗口 90 之外）也要能扫到。"""
    img = _chat_at(150)
    unreplied, _, _ = _det(tmp_path).extract_bubbles_detail(img)
    assert unreplied is not None and len(unreplied) == 1


def test_bottom_message_still_detected(tmp_path):
    """底部有内容时走原有窗口，行为不变。"""
    img = _chat_at(H - 60)
    unreplied, _, _ = _det(tmp_path).extract_bubbles_detail(img)
    assert unreplied is not None and len(unreplied) == 1


def test_blank_chat_area_has_no_bubble(tmp_path):
    """空白聊天区不能扫出假气泡（兜底扫描放开了整个区域，这条是关键护栏）。"""
    img = Image.new("RGB", (W, H), WHITE)
    unreplied, has_blue, replied = _det(tmp_path).extract_bubbles_detail(img)
    assert unreplied is None
    assert has_blue is False
    assert replied == []


def test_too_small_image_returns_none(tmp_path):
    img = Image.new("RGB", (10, 10), WHITE)
    assert _det(tmp_path).extract_bubbles_detail(img) == (None, False, [])
