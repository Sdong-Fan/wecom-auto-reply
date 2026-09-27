# -*- coding: utf-8 -*-
"""OCR 引擎对比探针：RapidOCR(ONNX) vs PaddleOCR —— 同一张真实企微截图，比准确率与耗时。

为什么要比：PaddleOCR 在本机偶发 `RuntimeError: could not execute a primitive`（每半小时数次），
一次崩溃就丢一轮扫描；而且它 import 时会和 torch 抢 DLL。RapidOCR 走 ONNX Runtime，
没有 paddle 运行时。换之前先用真实截图确认它认得一样准。

只读：PrintWindow 抓窗口（不抢前台），不点击不输入。

用法:
    python scripts/probe_ocr_backend.py
    python scripts/probe_ocr_backend.py --repeat 3
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from wxbot.native_win import ensure_dpi_aware, find_main_hwnd, grab_window  # noqa: E402
from wxbot.native_win import check_layout  # noqa: E402

ensure_dpi_aware()


def rapid_lines(img):
    """RapidOCR → [(text, conf)]，顺便返回耗时 ms。"""
    from rapidocr_onnxruntime import RapidOCR
    global _RAPID
    try:
        _RAPID
    except NameError:
        _RAPID = RapidOCR(intra_op_num_threads=4, det_limit_type="max", det_limit_side_len=4000)
    arr = np.asarray(img.convert("RGB"))
    t0 = time.perf_counter()
    res, _ = _RAPID(arr, use_cls=False)
    ms = (time.perf_counter() - t0) * 1000
    out = [(r[1], float(r[2])) for r in (res or [])]
    return out, ms


def paddle_lines(img):
    # torch 必须先于 paddle 导入，否则 paddle 自己的 DLL 会和 torch 的抢，
    # 报 "Error loading torch/lib/shm.dll"（项目 CLAUDE.md 记过这条）。
    try:
        import torch  # noqa: F401
    except Exception as e:
        print(f"  (import torch 失败: {e})")
    from paddleocr import PaddleOCR
    global _PADDLE
    try:
        _PADDLE
    except NameError:
        _PADDLE = PaddleOCR(lang="ch")
    arr = np.asarray(img.convert("RGB"))
    t0 = time.perf_counter()
    res = _PADDLE.ocr(arr)
    ms = (time.perf_counter() - t0) * 1000
    out = []
    for r in (res[0] or []):
        out.append((r[1][0], float(r[1][1])))
    return out, ms


def show(name, lines, ms, min_conf=0.5):
    kept = [(t, c) for t, c in lines if c >= min_conf]
    text = " ".join(t.strip() for t, _ in kept)
    print(f"  [{name}] {ms:6.0f} ms  行数={len(lines)} (≥0.5: {len(kept)})")
    print(f"      {text[:220]!r}")
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=2)
    args = ap.parse_args()

    hwnd = find_main_hwnd()
    if not hwnd:
        sys.exit("没找到企业微信主窗口")
    full = grab_window(hwnd)
    if full is None:
        sys.exit("抓窗口失败")
    print(f"hwnd={hwnd} 抓图={full.size}")

    warn = check_layout(full, 464)
    print(f"面板边界自检（聊天面板左边界）: {warn or '与 config 一致'}")

    # 左侧会话列表（前缀区）与右侧聊天区各裁一块
    W, H = full.size
    col2 = full.crop((100, 0, 464, H))
    chat = full.crop((464, 100, W - int(W * 0.30), H - 240))
    print(f"会话列表裁块={col2.size}  聊天区裁块={chat.size}")

    for label, img in (("会话列表", col2), ("聊天区", chat)):
        print(f"\n===== {label} =====")
        texts = {}
        for name, fn in (("RapidOCR", rapid_lines), ("PaddleOCR", paddle_lines)):
            for i in range(max(1, args.repeat)):
                try:
                    lines, ms = fn(img)
                except Exception as e:
                    print(f"  [{name}] 第{i+1}次 失败: {type(e).__name__}: {e}")
                    continue
                t = show(f"{name} #{i+1}", lines, ms)
                texts.setdefault(name, []).append(t)
        if len(texts) == 2:
            a = texts["RapidOCR"][0]
            b = texts["PaddleOCR"][0]
            same = a == b
            print(f"  两边文本完全一致: {same}")
            if not same:
                print(f"      Rapid : {a[:150]!r}")
                print(f"      Paddle: {b[:150]!r}")


if __name__ == "__main__":
    main()
