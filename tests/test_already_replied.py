# tests/test_already_replied.py
"""「人工回过也算回过」。

店主要是在企微里**手动**回了一句，机器人就不该再插一句"稍等"。
判定靠气泡颜色：蓝泡（我们或人工发的）**之上**的客户气泡 = 已经有人回过了。

把这些气泡的文字记为已处理之后，以后界面再读到旧内容
（列表重排、窗口没刷新、程序重启、OCR 多读进几条）也不会再回一遍。
"""

import pytest
from PIL import Image

from wxbot.detector import MessageDetector

W, H = 400, 600
WHITE = (255, 255, 255)
GRAY = (235, 235, 235)          # 客户气泡（左）
BLUE = (150, 150, 210)          # 我们/人工 的气泡（右）


@pytest.fixture
def det(tmp_path):
    return MessageDetector({"state_dir": str(tmp_path)})


def _chat(*bubbles, gap=40):
    """画一张假聊天区：bubbles 从下往上给，元素是 (颜色, 高度)。"""
    img = Image.new("RGB", (W, H), WHITE)
    y = H - 60
    for color, height in bubbles:
        top = y - height
        if color == GRAY:
            img.paste(color, (10, top, int(W * 0.6), y))     # 客户：靠左
        else:
            img.paste(color, (int(W * 0.45), top, W - 10, y))  # 我们：靠右
        y = top - gap
    return img


def test_no_blue_means_nothing_replied(det):
    """只有客户气泡：没有"已回复"的，全部算未回复。"""
    img = _chat((GRAY, 40), (GRAY, 40))
    unreplied, has_blue, replied = det.extract_bubbles_detail(img)
    assert has_blue is False
    assert replied == []
    assert unreplied and len(unreplied) == 2


def test_blue_on_top_means_customer_bubbles_already_replied(det):
    """客户说两句 → 有人回了一句（蓝泡在最下面）→ 这两句都算已回复。"""
    img = _chat((BLUE, 40), (GRAY, 40), (GRAY, 40))
    unreplied, has_blue, replied = det.extract_bubbles_detail(img)
    assert has_blue is True
    assert unreplied is None, "已经有人回过了，没有待回复的"
    assert len(replied) == 2, "蓝泡之上的两条客户气泡都要被认出来"


def test_customer_message_after_reply_is_unreplied(det):
    """人工回过之后客户又追问 → 新的那条才算未回复，老的不算。"""
    img = _chat((GRAY, 40), (BLUE, 40), (GRAY, 40))
    unreplied, has_blue, replied = det.extract_bubbles_detail(img)
    assert has_blue is True
    assert unreplied and len(unreplied) == 1
    assert len(replied) == 1


def test_two_replies_still_one_boundary(det):
    """回过两次（比如先占位语、再人工补一句）：上方的客户消息都算已回复。"""
    img = _chat((BLUE, 40), (BLUE, 40), (GRAY, 40), (GRAY, 40))
    unreplied, has_blue, replied = det.extract_bubbles_detail(img)
    assert unreplied is None
    assert len(replied) == 2


def test_old_api_shape_unchanged(det):
    """老的 extract_unreplied_bubbles 还是两元组（别的调用方在用）。"""
    img = _chat((BLUE, 40), (GRAY, 40))
    got = det.extract_unreplied_bubbles(img)
    assert isinstance(got, tuple) and len(got) == 2


def test_tiny_image_safe(det):
    assert det.extract_bubbles_detail(Image.new("RGB", (5, 5))) == (None, False, [])


# ── 接线：认出来的气泡要真的被记为已见 ──────────────────────────────

def test_mark_already_replied_marks_texts():
    import main as main_mod

    class _D:
        def __init__(self):
            self.calls = 0
            self.marked = []

        def extract_text(self, img, min_conf=None):
            self.calls += 1
            return f"客户说的第{self.calls}句"

        def is_message_seen(self, customer, text):
            return False

        def mark_messages_seen(self, customer, texts, already_replied=False):
            self.marked.extend(texts)
            self.already_replied = already_replied

    d = _D()
    imgs = [Image.new("RGB", (10, 10), GRAY) for _ in range(3)]
    n = main_mod._mark_already_replied(d, "客户B@微信", imgs)
    assert n == 3
    assert len(d.marked) == 3, "已回复过的气泡要逐条记为已见"
    assert d.already_replied is True, \
        "「人工回过也算回过」是 24h 长记忆，不能只当短窗口去重"


def test_mark_already_replied_is_bounded():
    """只 OCR 最靠近蓝泡的几条 —— 再往上的老消息不值得花 OCR。"""
    import main as main_mod

    class _D:
        def __init__(self):
            self.calls = 0
            self.marked = []

        def extract_text(self, img, min_conf=None):
            self.calls += 1
            return f"第{self.calls}句客户的话"

        def is_message_seen(self, customer, text):
            return False

        def mark_messages_seen(self, customer, texts, already_replied=False):
            self.marked.extend(texts)
            self.already_replied = already_replied

    d = _D()
    imgs = [Image.new("RGB", (10, 10), GRAY) for _ in range(20)]
    main_mod._mark_already_replied(d, "客户B@微信", imgs)
    assert d.calls == 5, "上限 5 条"


def test_mark_already_replied_skips_already_seen():
    import main as main_mod

    class _D:
        def __init__(self):
            self.marked = []

        def extract_text(self, img, min_conf=None):
            return "早就记过的句子"

        def is_message_seen(self, customer, text):
            return True

        def mark_messages_seen(self, customer, texts, already_replied=False):
            self.marked.extend(texts)
            self.already_replied = already_replied

    d = _D()
    main_mod._mark_already_replied(d, "客户B@微信",
                                   [Image.new("RGB", (10, 10), GRAY)])
    assert d.marked == [], "已经记过的不重复写"


def test_mark_already_replied_handles_empty():
    import main as main_mod
    assert main_mod._mark_already_replied(None, "谁", []) == 0
    assert main_mod._mark_already_replied(None, "谁", None) == 0


def test_mark_already_replied_survives_ocr_error():
    """OCR 炸了不能让整轮扫描挂掉。"""
    import main as main_mod

    class _D:
        def extract_text(self, img, min_conf=None):
            raise RuntimeError("OCR 崩了")

        def is_message_seen(self, c, t):
            return False

        def mark_messages_seen(self, c, texts, already_replied=False):
            pass

    assert main_mod._mark_already_replied(
        _D(), "客户B", [Image.new("RGB", (10, 10), GRAY)]) == 0
