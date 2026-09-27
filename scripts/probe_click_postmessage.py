# -*- coding: utf-8 -*-
"""点击探针：能不能用 PostMessage 给**非前台**的企业微信发鼠标消息来切换会话。

背景：现在切会话靠 SetForegroundWindow + SetCursorPos + mouse_event —— 抢焦点、闪屏、
还可能把我们自己的 GUI 截进画面。如果 PostMessage(WM_LBUTTONDOWN/UP) 就能切会话，
整条"抢前台"逻辑可以删掉。

做法：PrintWindow 抓聊天区指纹 → PostMessage 点某一会话行 → 再抓 → 指纹变了就是成功。
不抢前台、不移动真实光标、不改窗口状态。只点左侧会话列表的一行，不碰任何按钮。

用法:
    python scripts/probe_click_postmessage.py                 # 默认点第 2 行
    python scripts/probe_click_postmessage.py --row-y 505
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import io
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe_capture_wework import find_hwnd, grab, u32  # noqa: E402

WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
MK_LBUTTON = 0x0001

u32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
u32.PostMessageW.restype = ctypes.c_bool
u32.ScreenToClient.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_long * 2)]
u32.ScreenToClient.restype = ctypes.c_bool
u32.GetClientRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_long * 4)]
u32.GetClientRect.restype = ctypes.c_bool


def chat_fp(arr, x0=140, x1=None, y0=200, y1=None):
    """聊天区指纹：整块像素的 md5（粗指纹，只要"变了没有"）。"""
    h, w = arr.shape[:2]
    x1 = x1 or w
    y1 = y1 or h
    return hashlib.md5(np.ascontiguousarray(arr[y0:y1, x0:x1]).tobytes()).hexdigest()[:12]


def click_client(hwnd, x, y, hold_ms=60):
    lp = (y << 16) | (x & 0xFFFF)
    ok1 = u32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lp)
    time.sleep(hold_ms / 1000)
    ok2 = u32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lp)
    return bool(ok1), bool(ok2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--row-y", type=int, default=223, help="要点的会话行 y（屏幕坐标）")
    ap.add_argument("--x", type=int, default=60, help="点的 x（屏幕坐标，会话列表内）")
    ap.add_argument("--wait", type=float, default=1.5)
    args = ap.parse_args()

    hwnd = find_hwnd()
    rect = (ctypes.c_long * 4)()
    u32.GetWindowRect(hwnd, rect)
    l, t = rect[0], rect[1]
    cr = (ctypes.c_long * 4)()
    u32.GetClientRect(hwnd, cr)
    print(f"hwnd={hwnd} 窗口左上=({l},{t}) 窗口={rect[2]-l}x{rect[3]-t} 客户区={cr[2]}x{cr[3]} "
          f"前台={u32.GetForegroundWindow() == hwnd}")

    _, before, _, _ = grab(hwnd, "print")
    fp0 = chat_fp(before)
    print(f"点击前聊天区指纹={fp0}")

    cx, cy = args.x - l, args.row_y - t
    print(f"PostMessage 点击 客户区坐标=({cx},{cy})  ← 屏幕({args.x},{args.row_y})")
    ok1, ok2 = click_client(hwnd, cx, cy)
    print(f"PostMessage 返回 down={ok1} up={ok2}  （注意：返回 True 只代表消息投进队列，不代表被处理）")

    time.sleep(args.wait)
    _, after, _, _ = grab(hwnd, "print")
    fp1 = chat_fp(after)
    print(f"点击后聊天区指纹={fp1}")

    changed = fp0 != fp1
    print("\n结论：" + ("✓ 聊天区变了 —— PostMessage 能切会话，可以不抢前台"
                       if changed else
                       "✗ 聊天区没变 —— 企业微信不吃 PostMessage 点击，仍需要真实鼠标/前台"))
    if changed:
        from PIL import Image
        out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "logs", "cap_probe", f"after_postclick_{time.strftime('%H%M%S')}.png")
        Image.fromarray(after).save(out)
        print(f"   点击后的画面: {out}")


if __name__ == "__main__":
    main()
