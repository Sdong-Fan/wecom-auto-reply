# -*- coding: utf-8 -*-
"""通用截图模式可行性探针：不写死任何"这个 App 专属规则"，只用像素与颜色，
看能不能把聊天区/谁说的/输入框这三件事认出来。

要验证的假设（决定"兼容所有有聊天框的软件"是否成立）：
  H1 消息区能自动定位 —— 像素锚点（底色众数 + 分隔线），不需要每个 App 的几何
  H2 "谁说的"能靠颜色分开 —— 我方气泡是主题色（绿/蓝），对方是浅底；灰字是引用/时间戳
  H3 输入框能靠"下方整行单色分隔线"定位
  H4 新消息能靠"行像素变化"发现 —— 不需要每个 App 的红点规则（本会话已另证）

只读：PrintWindow 抓窗口，不点击、不输入。

用法:
    python scripts/probe_universal_side.py
    python scripts/probe_universal_side.py --dump          # 存一张裁剪图到 logs/
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from wxbot.native_win import ensure_dpi_aware, find_main_hwnd, grab_window  # noqa: E402

ensure_dpi_aware()


def gray(a):
    return a @ np.array([0.299, 0.587, 0.114])


def find_chat_panel(arr):
    """H1：像素锚点找聊天面板（底色众数 + 左右边界 + 上下边界）。"""
    h, w, _ = arr.shape
    right = arr[::8, w // 2::8].reshape(-1, 3)
    vals, cnt = np.unique(right, axis=0, return_counts=True)
    bg = vals[cnt.argmax()]
    isbg = np.abs(arr.astype(np.int16) - bg.astype(np.int16)).sum(-1) <= 6
    col = isbg[h // 4: h * 3 // 4].mean(0)
    x0 = int(np.argmax(col > 0.3))
    x1 = w - int(np.argmax(col[::-1] > 0.3))
    row = isbg[:, x0:x1].mean(1)
    y0 = int(np.argmax(row > 0.9))
    y1 = h - int(np.argmax(row[::-1] > 0.9))
    return bg, (x0, y0, x1, y1)


def find_input_top(arr, panel, header_h=90):
    """H3：输入框顶 = 面板 45% 高度以下第一根"整行单色且非底色"的分隔线。"""
    x0, y0, x1, y1 = panel
    bg = find_chat_panel(arr)[0]
    isbg = np.abs(arr.astype(np.int16) - bg.astype(np.int16)).sum(-1) <= 6
    row = isbg[:, x0:x1].mean(1)
    band = arr[y0:y1, x0:x1].astype(np.int16)
    seps = y0 + np.where((band.std(axis=(1, 2)) < 4) & (row[y0:y1] < 0.1))[0]
    seps = [int(s) for i, s in enumerate(seps) if i == 0 or s - seps[i - 1] > 3]
    below = [s for s in seps if s > y0 + 0.45 * (y1 - y0)]
    above = [s for s in seps if y0 + header_h < s < (below[0] if below else y1) - 50]
    return (below[0] if below else y1), (above[-1] if above else y0 + header_h)


def classify_box(chat, box, pane_bg):
    """jev 的 who_said：底色众数占比 + 对比度 → me / other / gray / image。"""
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]
    reg = chat[int(min(ys)):int(max(ys)), int(min(xs)):int(max(xs))].astype(np.int16)
    if reg.size == 0:
        return None, None, 0, 0.0
    vals, cnt = np.unique(reg.reshape(-1, 3), axis=0, return_counts=True)
    bg = vals[cnt.argmax()]
    uniformity = cnt.max() / (reg.shape[0] * reg.shape[1])
    if uniformity < 0.45:
        return None, bg, 0, uniformity          # 图片（头像/表情包/照片）里的字
    diff = np.abs(gray(reg) - gray(bg))
    ink = 0
    best = 0
    for r in (diff > 60).any(axis=1):
        best = best + 1 if r else 0
        ink = max(ink, best)
    # 绿底/蓝底 = 我方（主题色）。注意 arr 是 RGB，别把通道名写反了
    # （第一版就写反了 → b,g,r = bg[0],bg[1],bg[2]，结果自己的蓝气泡全被判成对方）
    r, g, b = int(bg[0]), int(bg[1]), int(bg[2])
    if g > r + 25 and g > b + 15:
        return "me", bg, ink, uniformity
    if b > r + 25 and b > g + 15:
        return "me", bg, ink, uniformity
    if diff.max() >= 150:
        return "other", bg, ink, uniformity
    return "gray", bg, ink, uniformity


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true")
    args = ap.parse_args()

    hwnd = find_main_hwnd()
    if not hwnd:
        sys.exit("没找到企业微信主窗口")
    img = grab_window(hwnd)
    if img is None:
        sys.exit("PrintWindow 抓窗口失败")
    arr = np.asarray(img.convert("RGB"))
    print(f"窗口 {img.size}")

    bg, panel = find_chat_panel(arr)
    print(f"H1 像素锚点: 面板底色={tuple(int(v) for v in bg)} 面板={panel}")
    in_top, msg_top = find_input_top(arr, panel)
    print(f"H3 输入框顶 y={in_top}（面板高 {panel[3]-panel[1]}，占 {(in_top-panel[1])/(panel[3]-panel[1]):.0%}）")
    print(f"H1 消息区顶 y={msg_top}（标题栏/公告条之下）")

    x0, _, x1, _ = panel
    chat = arr[msg_top:in_top, x0:x1]
    print(f"   聊天区裁块={chat.shape[1]}x{chat.shape[0]}")
    if args.dump:
        os.makedirs(os.path.join(HERE, "logs"), exist_ok=True)
        p = os.path.join(HERE, "logs", f"univ_chat_{time.strftime('%H%M%S')}.png")
        Image.fromarray(chat).save(p)
        print(f"   已存: {p}")

    from rapidocr_onnxruntime import RapidOCR
    eng = RapidOCR(intra_op_num_threads=4, det_limit_type="max", det_limit_side_len=4000)
    res, _ = eng(chat, use_cls=False)
    rows = sorted(res or [], key=lambda r: r[0][0][1])
    print(f"\nH2 逐框分类（共 {len(rows)} 框）：")
    counts = {"me": 0, "other": 0, "gray": 0, "image": 0}
    for box, text, score in rows:
        side, boxbg, ink, unif = classify_box(chat, box, bg)
        key = "image" if side is None else side
        counts[key] = counts.get(key, 0) + 1
        bgstr = "-" if boxbg is None else ",".join(str(int(v)) for v in boxbg)
        print(f"   {key:6} bg=({bgstr:11}) 众数占比={unif:.2f} 墨高={ink:2}  {text[:44]!r}")
    print(f"\n汇总: {counts}")
    print("\n判读：me/other 分得开 → H2 成立；gray 里应该是时间戳/引用/系统提示。")
    print("      若 me 与 other 大量混淆 → 通用颜色规则不成立，必须每个 App 单独定规则。")


if __name__ == "__main__":
    main()
