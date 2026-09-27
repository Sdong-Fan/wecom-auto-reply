# -*- coding: utf-8 -*-
"""动态行定位的单元测试（用合成图，不依赖真实窗口）。"""
import numpy as np
import pytest
from PIL import Image

from wxbot.list_rows import detect_list_rows


def _list_img(rows, w=360, bg=(238, 238, 240), row_gap=24,
              name_h=26, preview_h=22, top=110):
    """造一张假的会话列表：每行 = 名字条 + 预览条，行与行之间留 row_gap。

    rows: [(名字文字长度, 预览是否两行)] —— 两行预览会把这一行撑高。
    """
    h = top + sum(name_h + preview_h + (preview_h if two else 0) + row_gap
                  for _n, two in rows) + 40
    arr = np.full((h, w, 3), bg, dtype=np.uint8)
    y = top
    for name_len, two in rows:
        # 名字条（文字区 x 从 0.3w 到 0.9w）
        arr[y:y + name_h, int(w * 0.32):int(w * 0.32) + name_len * 8] = (40, 40, 40)
        y += name_h + 6
        arr[y:y + preview_h, int(w * 0.32):int(w * 0.32) + 120] = (120, 120, 120)
        y += preview_h
        if two:                       # 预览第二行 → 这一行变高
            arr[y:y + preview_h, int(w * 0.32):int(w * 0.32) + 90] = (120, 120, 120)
            y += preview_h
        y += row_gap
    # 顶部搜索框（要跳过）
    arr[40:90, int(w * 0.32):int(w * 0.32) + 100] = (150, 150, 150)
    return Image.fromarray(arr), top


def test_detects_rows_with_uniform_height():
    img, _ = _list_img([(6, False)] * 5)
    rows = detect_list_rows(img, skip_top_px=110, max_rows=5)
    assert len(rows) == 5, f"应当切出 5 行，实际 {len(rows)}"
    tops = [a for a, _ in rows]
    assert tops == sorted(tops)
    # 间距应当接近（造出来的行高一致）
    steps = [tops[i + 1] - tops[i] for i in range(len(tops) - 1)]
    assert max(steps) - min(steps) <= 6, f"等高的行，间距应当一致: {steps}"


def test_row_that_grows_does_not_shift_later_rows():
    """关键：某行预览变两行时，后面的行必须仍然各自对得上（固定行高就会串）。"""
    img, _ = _list_img([(6, False), (6, True), (6, False), (6, False)])
    rows = detect_list_rows(img, skip_top_px=110, max_rows=4)
    assert len(rows) == 4
    a0, a1, a2, a3 = [a for a, _ in rows]
    # 第二行预览两行 → 它比第一行高；后面的行被整体推下去，但各自仍是一整行
    assert a2 - a1 > a1 - a0, f"第二行变高，第三行应当被推得更远: {a1-a0} vs {a2-a1}"
    assert a3 - a2 <= a2 - a1, "第四行恢复普通高度"


def test_skips_search_box_at_top():
    img, _ = _list_img([(6, False)] * 3)
    rows = detect_list_rows(img, skip_top_px=110, max_rows=3)
    assert rows and rows[0][0] >= 105, f"不该把顶部搜索框算成会话: {rows[0]}"


def test_returns_empty_for_blank_image():
    assert detect_list_rows(Image.new("RGB", (360, 800), (238, 238, 240))) == []


def test_returns_empty_for_tiny_image():
    assert detect_list_rows(Image.new("RGB", (10, 10))) == []


def test_respects_max_rows():
    img, _ = _list_img([(6, False)] * 8)
    assert len(detect_list_rows(img, skip_top_px=110, max_rows=3)) <= 3
