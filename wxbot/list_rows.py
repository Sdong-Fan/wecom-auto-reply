# wxbot/list_rows.py
"""会话列表的**动态行定位** —— 不依赖固定行高。

为什么必须动态：微信的会话行高度**不固定**。某一行的消息预览是两行文字时
（比如我们自己刚发了一句比较长的话），那一行会变高，把后面所有行往下推。
用固定 `row_height_px` 数行会累积错位，实测踩到：
    名字读到的是"客户F"，消息预览却是发给"客户C"的内容
    → 判定成"客户F有新消息" → 点开了错误的会话。

做法：把列表区按"文字带 + 较大间隙"切成一个个会话行。
    文字带 = 该横排非背景像素数 > 0 的连续区间（名字/预览/时间都算）
    会话之间有大间隙（行间距），会话内部带与带之间间隙小
    用间隙的中位数自适应定阈值，不写死像素
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)


def _text_bands(diff_mask: np.ndarray, min_ink: int = 2) -> List[Tuple[int, int]]:
    """找出所有"有字"的横条（y0, y1）。"""
    counts = diff_mask.sum(axis=1)
    bands = []
    start = None
    for y in range(len(counts)):
        if counts[y] >= min_ink:
            if start is None:
                start = y
        else:
            if start is not None:
                bands.append((start, y - 1))
                start = None
    if start is not None:
        bands.append((start, len(counts) - 1))
    return bands


def detect_list_rows(list_img: Image.Image, max_rows: int = 12,
                     ink_tolerance: int = 18, min_row_h: int = 40,
                     min_ink: int = 3,
                     x_from_ratio: float = 0.28, x_to_ratio: float = 0.92,
                     skip_top_px: int = 0) -> List[Tuple[int, int]]:
    """把会话列表切成若干行，返回 **整幅图坐标系**下的 [(y_top, y_bottom), …]。

    ``x_from_ratio`` / ``x_to_ratio`` 把取样限制在**文字区**：跳过左侧头像列
    （头像是一大块色块，会把每一行的"墨"填满，切不出间隙）和右侧时间戳/角标。
    另外会丢掉纵向贯穿整列的结构（滚动条、行分隔线），否则每一行都有墨、
    永远切不出间隙（实测踩过：文字带只有 1 个，高 1738）。

    返回空列表表示认不出来（调用方应退回固定行高）。
    """
    arr = np.asarray(list_img.convert("RGB")).astype(np.int16)
    h, w, _ = arr.shape
    if h < 40 or w < 40:
        return []

    # 背景色 = 出现最多的颜色（会话列表底色）
    right = arr[::8, :].reshape(-1, 3)
    vals, cnt = np.unique(right, axis=0, return_counts=True)
    bg = vals[cnt.argmax()]
    diff_mask = np.abs(arr - bg).sum(axis=2) > ink_tolerance

    # 只看文字区
    x0 = max(0, int(w * x_from_ratio))
    x1 = min(w, int(w * x_to_ratio))
    band = diff_mask[:, x0:x1]
    if band.shape[1] < 5:
        return []
    # 丢掉**真正贯穿整列**的结构（滚动条 / 边框）：阈值必须很高。
    # 取 0.5 会把正常文字列一起砍掉 —— 会话名和预览在同一个 x 范围里，
    # 两者加起来覆盖率就有 ~0.49，卡在阈值边上，实测把名字条整条删掉了。
    col_ink = band.mean(axis=0)
    band = band[:, col_ink < 0.9]
    if band.shape[1] < 5:
        return []

    # 跳过顶部搜索框：它和第一个会话之间没有足够间隙，会被粘成一大块
    # （实测：不跳的话第一段是 0..217 的"搜索框+第一个会话"）。
    top = max(0, int(skip_top_px))
    if top:
        band = band[top:, :]
    if band.shape[0] < 20:
        return []

    bands = _text_bands(band, min_ink=min_ink)
    if len(bands) < 2:
        return []
    # 换算回列表区坐标
    bands = [(a + top, b + top) for a, b in bands]

    # 相邻文字带的间隙：会话内部（名字↔预览）小，会话之间大
    gaps = [bands[i + 1][0] - bands[i][1] - 1 for i in range(len(bands) - 1)]
    positive = sorted(g for g in gaps if g > 0)
    if not positive:
        return []
    # 阈值取"较小那半"的上沿：会话之间的间隙明显大于内部间隙
    mid = positive[len(positive) // 4] if len(positive) >= 4 else positive[0]
    split_gap = max(6, int(mid) + 6)

    rows: List[Tuple[int, int]] = []
    top, bottom = bands[0]
    for i, g in enumerate(gaps):
        if g >= split_gap:
            rows.append((top, bottom))
            top = bands[i + 1][0]
        bottom = bands[i + 1][1]
    rows.append((top, bottom))

    # 太矮的段多半是噪声（分隔线/图标），并掉
    rows = [(a, b) for a, b in rows if b - a >= 8]
    rows = [(a, b) for a, b in rows if b - a <= h]
    if not rows:
        return []

    # 相邻行间隔过小的合并（同一会话被误切）
    merged = [rows[0]]
    for a, b in rows[1:]:
        pa, pb = merged[-1]
        if a - pb < 10:
            merged[-1] = (pa, max(pb, b))
        else:
            merged.append((a, b))
    rows = merged

    # 行高异常（比中位数小一半以上）的丢掉；明显过高的（>2.5 倍）多半是
    # "搜索框/公告条 + 第一个会话"粘在一起，也丢掉，宁缺勿错。
    if len(rows) >= 3:
        heights = sorted(b - a for a, b in rows)
        med = heights[len(heights) // 2]
        rows = [(a, b) for a, b in rows
                if max(10, med * 0.4) <= (b - a) <= med * 2.5]

    return rows[:max_rows]
