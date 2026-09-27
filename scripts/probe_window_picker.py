# -*- coding: utf-8 -*-
"""窗口选择器探针：验证"让用户点一下目标窗口就能自动认出这个软件"这条路可不可行。

做法：GetCursorPos + WindowFromPoint + GetAncestor(GA_ROOT) → 进程名 + 窗口类 + 标题。
用户在向导里把鼠标移到聊天软件窗口上，我们就能自动填出 profile 的三要素，
不需要用户手输 "WXWork.exe / WeWorkWindow"。

本探针不依赖用户操作：拿企业微信窗口中心点当"鼠标位置"验证同一条代码路径。

只读，不点击不输入。

用法:
    python scripts/probe_window_picker.py                 # 用企业微信窗口中心点自测
    python scripts/probe_window_picker.py --cursor        # 用当前真实光标位置（你自己把鼠标移过去）
"""
from __future__ import annotations

import argparse
import ctypes
import io
import os
import sys
from ctypes import wintypes

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

u32 = ctypes.WinDLL("user32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)

u32.WindowFromPoint.argtypes = [wintypes.POINT]
u32.WindowFromPoint.restype = wintypes.HWND
u32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
u32.GetAncestor.restype = wintypes.HWND
u32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
u32.GetCursorPos.restype = wintypes.BOOL
u32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
u32.IsWindowVisible.argtypes = [wintypes.HWND]

GA_ROOT = 2
k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
k32.OpenProcess.restype = wintypes.HANDLE
k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                           wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
k32.CloseHandle.argtypes = [wintypes.HANDLE]


def exe_of_pid(pid):
    h = k32.OpenProcess(0x1000, False, pid)
    if not h:
        return ""
    try:
        b = ctypes.create_unicode_buffer(1024)
        s = wintypes.DWORD(1024)
        if k32.QueryFullProcessImageNameW(h, 0, b, ctypes.byref(s)):
            return os.path.basename(b.value)
        return ""
    finally:
        k32.CloseHandle(h)


def describe(hwnd):
    """一个屏幕点/句柄 → profile 三要素。"""
    root = u32.GetAncestor(hwnd, GA_ROOT) or hwnd
    cls = ctypes.create_unicode_buffer(256)
    u32.GetClassNameW(root, cls, 256)
    title = ctypes.create_unicode_buffer(256)
    u32.GetWindowTextW(root, title, 256)
    pid = wintypes.DWORD()
    u32.GetWindowThreadProcessId(root, ctypes.byref(pid))
    r = wintypes.RECT()
    u32.GetWindowRect(root, ctypes.byref(r))
    return {
        "hwnd": int(root),
        "process": exe_of_pid(pid.value),
        "window_class": cls.value,
        "title": title.value,
        "title_length": len(title.value),
        "rect": [r.left, r.top, r.right - r.left, r.bottom - r.top],
        "visible": bool(u32.IsWindowVisible(root)),
    }


def pick_at(x, y):
    hwnd = u32.WindowFromPoint(wintypes.POINT(x, y))
    if not hwnd:
        return None
    return describe(hwnd)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cursor", action="store_true", help="用真实光标位置")
    ap.add_argument("--hwnd", action="store_true",
                    help="自测：直接描述企业微信句柄（不依赖它在最上层）")
    args = ap.parse_args()

    if args.hwnd:
        from wxbot.native_win import ensure_dpi_aware, find_main_hwnd
        ensure_dpi_aware()
        hwnd = find_main_hwnd()
        if not hwnd:
            sys.exit("没找到企业微信窗口")
        info = describe(hwnd)
        print(f"直接描述企业微信句柄 {hwnd}：")
        for k, v in info.items():
            print(f"    {k:14} = {v!r}")
        return

    if args.cursor:
        p = wintypes.POINT()
        u32.GetCursorPos(ctypes.byref(p))
        x, y = p.x, p.y
        print(f"光标位置: ({x},{y})")
    else:
        from wxbot.native_win import ensure_dpi_aware, find_main_hwnd, window_rect
        ensure_dpi_aware()
        hwnd = find_main_hwnd()
        if not hwnd:
            sys.exit("没找到企业微信窗口")
        l, t, w, h = window_rect(hwnd)
        x, y = l + w // 2, t + h // 2
        print(f"自测点=企业微信窗口中心 ({x},{y})")

    info = pick_at(x, y)
    if not info:
        sys.exit("WindowFromPoint 返回空")
    print("拾取结果（可直接写进 profile）：")
    for k, v in info.items():
        print(f"    {k:14} = {v!r}")
    print("\n判读：process + window_class + title_length 三要素齐了，"
          "就能唯一确定这个软件的主窗口 —— 用户向导只要让他把鼠标移过去点一下。")


if __name__ == "__main__":
    main()
