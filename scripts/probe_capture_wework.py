# -*- coding: utf-8 -*-
"""采集探针：企业微信主窗口能不能在**不抢前台**的情况下被抓到画面。

回答两个问题（决定任务 2 换不换采集方式）：
  1. PrintWindow + PW_RENDERFULLCONTENT 能不能从 WeWorkWindow 抓到完整画面？
     —— 能的话就可以彻底删掉 SetForegroundWindow（抢焦点/闪烁/抓到自己的 GUI 全没了）
  2. BitBlt 从窗口 DC 抓（需要窗口可见）抓到的是不是同一块？作为对照。

只读，不点击、不输入、不改窗口状态（不做 SetForegroundWindow / ShowWindow）。

用法:
    python scripts/probe_capture_wework.py            # 前台/后台各抓一次对比
    python scripts/probe_capture_wework.py --out logs/cap
"""
from __future__ import annotations

import argparse
import ctypes
import io
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

u32 = ctypes.WinDLL("user32", use_last_error=True)
g32 = ctypes.WinDLL("gdi32", use_last_error=True)
# 用独立 WinDLL 实例而不是 ctypes.windll.user32：argtypes 是挂在 CDLL 实例上的，
# 在共享单例上设会让 pywin32（win32gui.GetWindowRect）跟着炸。
# 详见 wxbot/native_win.py 顶部注释与 tests/test_native_capture.py。

# 不做 DPI 感知的话 GetWindowRect 返回的是「虚拟化」坐标（150% 缩放下只有真实值的 2/3），
# 抓出来既不是整窗也会和 OCR 的物理像素对不上。
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)          # PER_MONITOR_DPI_AWARE
except Exception:
    try:
        u32.SetProcessDPIAware()
    except Exception:
        pass

u32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_long * 4)]
u32.GetWindowRect.restype = ctypes.c_bool
u32.GetWindowDC.argtypes = [ctypes.c_void_p]
u32.GetWindowDC.restype = ctypes.c_void_p
u32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
u32.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
u32.PrintWindow.restype = ctypes.c_bool
u32.IsWindowVisible.argtypes = [ctypes.c_void_p]
u32.IsIconic.argtypes = [ctypes.c_void_p]
u32.GetForegroundWindow.restype = ctypes.c_void_p

g32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
g32.CreateCompatibleDC.restype = ctypes.c_void_p
g32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
g32.CreateCompatibleBitmap.restype = ctypes.c_void_p
g32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
g32.SelectObject.restype = ctypes.c_void_p
g32.DeleteObject.argtypes = [ctypes.c_void_p]
g32.DeleteDC.argtypes = [ctypes.c_void_p]
g32.BitBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                       ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_uint]

class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32), ("biHeight", ctypes.c_int32),
                ("biPlanes", ctypes.c_uint16), ("biBitCount", ctypes.c_uint16),
                ("biCompression", ctypes.c_uint32), ("biSizeImage", ctypes.c_uint32),
                ("biXPelsPerMeter", ctypes.c_int32), ("biYPelsPerMeter", ctypes.c_int32),
                ("biClrUsed", ctypes.c_uint32), ("biClrImportant", ctypes.c_uint32)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", ctypes.c_uint32 * 3)]


# GetDIBits 少一个 argtypes 就会被当成 32 位参数 → 64 位 HDC 直接 OverflowError
g32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
                          ctypes.c_void_p, ctypes.POINTER(BITMAPINFO), ctypes.c_uint]
g32.GetDIBits.restype = ctypes.c_int


def find_hwnd(exe="wxwork.exe", title="企业微信"):
    k32 = ctypes.windll.kernel32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _):
        if not u32.IsWindowVisible(hwnd):
            return True
        pid = ctypes.c_ulong()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = k32.OpenProcess(0x1000, False, pid.value)
        if not h:
            return True
        buf, size = ctypes.create_unicode_buffer(1024), ctypes.c_uint(1024)
        ok = k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        k32.CloseHandle(h)
        if ok and os.path.basename(buf.value).lower() == exe:
            t = ctypes.create_unicode_buffer(256)
            u32.GetWindowTextW(hwnd, t, 256)
            found.append((hwnd, t.value))
        return True

    u32.EnumWindows(cb, 0)
    if not found:
        raise RuntimeError("没找到企业微信窗口")
    return next((h for h, t in found if t == title), found[0][0])


def grab(hwnd, method="print"):
    rect = (ctypes.c_long * 4)()
    if not u32.GetWindowRect(hwnd, rect):
        raise RuntimeError("GetWindowRect 失败")
    l, t, r, b = rect
    w, h = r - l, b - t
    hdc = u32.GetWindowDC(hwnd)
    mdc = g32.CreateCompatibleDC(hdc)
    bmp = g32.CreateCompatibleBitmap(hdc, w, h)
    old = g32.SelectObject(mdc, bmp)
    try:
        if method == "print":
            # PW_RENDERFULLCONTENT(2)：让 DWM 把 GPU 合成的画面也画进来（自绘窗口必需）
            ok = u32.PrintWindow(hwnd, mdc, 2)
        else:
            ok = g32.BitBlt(mdc, 0, 0, w, h, hdc, 0, 0, 0x00CC0020)  # SRCCOPY
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = w
        info.bmiHeader.biHeight = -h  # 负数 = 自上而下
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0  # BI_RGB
        buf = ctypes.create_string_buffer(w * h * 4)
        n = g32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(info), 0)
        arr = np.frombuffer(buf, dtype=np.uint8, count=w * h * 4).reshape(h, w, 4)[:, :, :3][:, :, ::-1]
        return bool(ok), arr, (l, t, w, h), n
    finally:
        g32.SelectObject(mdc, old)
        g32.DeleteObject(bmp)
        g32.DeleteDC(mdc)
        u32.ReleaseDC(hwnd, hdc)


def stats(arr):
    return {"mean": round(float(arr.mean()), 2), "max": int(arr.max()),
            "nonblack_ratio": round(float((arr.max(axis=2) > 12).mean()), 4)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "logs", "cap_probe"))
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    hwnd = find_hwnd()
    fg = u32.GetForegroundWindow()
    print(f"hwnd={hwnd}  前台hwnd={fg}  是前台={hwnd == fg}  "
          f"最小化={bool(u32.IsIconic(hwnd))}  可见={bool(u32.IsWindowVisible(hwnd))}")

    stamp = time.strftime("%H%M%S")
    for method in ("print", "bitblt"):
        try:
            ok, arr, (l, t, w, h), n = grab(hwnd, method)
        except Exception as e:
            print(f"[{method}] 失败: {e}")
            continue
        s = stats(arr)
        path = os.path.join(args.out, f"{method}_{stamp}.png")
        Image.fromarray(arr).save(path)
        print(f"[{method}] 调用返回={ok} 扫描行={n} 区域=({l},{t},{w}x{h}) {s}")
        print(f"          → {path}")

    print("\n判读：PrintWindow 的 nonblack_ratio 明显 > 0.5 且图片里能看清会话列表/聊天 → "
          "可以不抢前台抓窗口；接近 0 → PrintWindow 对企业微信无效，只能用 WGC 或屏幕截图。")


if __name__ == "__main__":
    main()
