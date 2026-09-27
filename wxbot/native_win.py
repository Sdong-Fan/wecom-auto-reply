# wxbot/native_win.py
"""原生窗口采集：PrintWindow 抓窗口 + PostMessage 点击 —— **全程不抢前台**。

背景（探针实测，见 docs/探针-采集方式.md）：
  企业微信的会话列表与聊天区在 UIA 里没有节点、在 Win32 里没有子窗口（自绘到
  ``WeWorkWindow`` 一块画布上）。所以只能截图。但"截图"不等于"抢前台 + 移光标"：

  * ``PrintWindow(hwnd, hdc, PW_RENDERFULLCONTENT)`` 在窗口**处于后台**时也能拿到
    完整画面（实测 2019x1728、nonblack_ratio 0.9961、气泡文字清晰）；
  * ``PostMessageW`` 发 WM_LBUTTONDOWN/UP 就能点会话列表切会话（实测聊天区指纹
    变化、目标行变高亮），**窗口不需要是前台**。

于是扫描这一路可以完全不再抢焦点：不闪屏、不打断用户、不移动真实光标，
而且抓的是目标窗口本身 —— 天生不会把我们自己的 GUI 截进画面。

发送那一路仍然要抢前台（Ctrl+V / Enter 需要键盘焦点），不在本模块范围。

DPI：150% 缩放下不做 DPI 感知，``GetWindowRect`` 返回的是虚拟化坐标
（1346x1152 而不是 2019x1728），抓出来的不是整窗、点击位置也全错。
所以 ``ensure_dpi_aware()`` 必须在建任何窗口之前调用。

所有 Win32 调用都显式声明 argtypes/restype —— 默认 c_int 会在 Win64 上
把 64 位句柄截断（项目里 test_ctypes_restype.py 守着这条）。
"""

import ctypes
import logging
import os
import time
from ctypes import wintypes
from typing import Optional, Tuple

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# ⚠️ 必须用**独立的 DLL 实例**，不能用 ctypes.windll.user32。
# ctypes 的 argtypes/restype 挂在 CDLL 实例上，而 ctypes.windll.user32 是
# 进程内共享单例 —— pywin32（win32gui.GetWindowRect 等）用的正是它。
# 一旦在共享实例上把 GetWindowRect 的 argtypes 设成 POINTER(wintypes.RECT)，
# pywin32 传 pywintypes.RECT 就会炸：
#     TypeError: expected LP_RECT instance instead of pointer to RECT
# 实测后果：所有发送在 _switch_to_wecom_and_back 里失败。
# WinDLL(...) 每次构造都是新实例，argtypes 只作用于我们自己。
_u32 = ctypes.WinDLL("user32", use_last_error=True)
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_g32 = ctypes.WinDLL("gdi32", use_last_error=True)

# ═══ Win32 声明 ═══════════════════════════════════════════════════════

_u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_u32.GetWindowRect.restype = wintypes.BOOL
_u32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_u32.GetClientRect.restype = wintypes.BOOL
_u32.IsWindow.argtypes = [wintypes.HWND]
_u32.IsWindow.restype = wintypes.BOOL
_u32.IsWindowVisible.argtypes = [wintypes.HWND]
_u32.IsWindowVisible.restype = wintypes.BOOL
_u32.IsIconic.argtypes = [wintypes.HWND]
_u32.IsIconic.restype = wintypes.BOOL
_u32.GetWindowDC.argtypes = [wintypes.HWND]
_u32.GetWindowDC.restype = wintypes.HDC
_u32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
_u32.ReleaseDC.restype = ctypes.c_int
_u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_u32.GetWindowTextW.restype = ctypes.c_int
_u32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_u32.GetClassNameW.restype = ctypes.c_int
_u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_u32.GetWindowThreadProcessId.restype = wintypes.DWORD
_u32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
_u32.EnumWindows.restype = wintypes.BOOL
_u32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
_u32.PrintWindow.restype = wintypes.BOOL
_u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_u32.PostMessageW.restype = wintypes.BOOL
_u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
_u32.ShowWindow.restype = wintypes.BOOL
_u32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, wintypes.UINT]
_u32.SetWindowPos.restype = wintypes.BOOL

_g32.CreateCompatibleDC.argtypes = [wintypes.HDC]
_g32.CreateCompatibleDC.restype = wintypes.HDC
_g32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
_g32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
_g32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
_g32.SelectObject.restype = wintypes.HGDIOBJ
_g32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
_g32.DeleteObject.restype = wintypes.BOOL
_g32.DeleteDC.argtypes = [wintypes.HDC]
_g32.DeleteDC.restype = wintypes.BOOL
_g32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                           ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
_g32.GetDIBits.restype = ctypes.c_int

_k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                            wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
_k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.CloseHandle.restype = wintypes.BOOL

PW_RENDERFULLCONTENT = 0x00000002
WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
MK_LBUTTON = 0x0001
SW_SHOWNOACTIVATE = 4
HWND_BOTTOM = 1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", ctypes.c_uint32 * 3)]


# ═══ DPI ══════════════════════════════════════════════════════════════

_dpi_done = False


def ensure_dpi_aware() -> bool:
    """进程级 DPI 感知，必须在任何窗口/抓图之前调用一次。幂等。

    不做的话在 150% 缩放的屏幕上，GetWindowRect 拿到的是 2/3 的虚拟化坐标，
    PrintWindow 抓出来的尺寸也不对，裁剪和点击全错。
    """
    global _dpi_done
    if _dpi_done:
        return True
    ok = False
    try:
        # PER_MONITOR_AWARE_V2 = -4；Win10 1703+
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        ok = True
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_DPI_AWARE
            ok = True
        except Exception:
            try:
                _u32.SetProcessDPIAware()
                ok = True
            except Exception as e:
                logger.warning(f"设置 DPI 感知失败: {e}")
    _dpi_done = ok
    logger.info(f"DPI 感知: {'已开启' if ok else '未开启（坐标可能虚拟化）'}")
    return ok


def dpi_aware() -> bool:
    return _dpi_done


# ═══ 窗口查找 ═════════════════════════════════════════════════════════

def _exe_of_pid(pid: int) -> str:
    h = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if _k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        _k32.CloseHandle(h)


def find_main_hwnd(process_names=None, window_class: str = "WeWorkWindow",
                   min_title_len: int = 2, prefer_title: str = "") -> Optional[int]:
    """按「进程名 + 窗口类 + 有标题 + 可见」找聊天软件主窗口。

    **不按面积挑**：同一个进程里还有工具窗、看图窗（微信就有 'Weixin' 工具窗和
    '图片和视频' 看图窗），面积可能比主窗口还大 —— 这条是 jev-chat-windows 实测记下来的。

    ``prefer_title``：主窗口标题精确匹配它时优先返回（微信 PC 主窗口标题是「微信」，
    同进程的其它窗口标题是 'Weixin' / '图片和视频'）。找不到才退回第一个匹配项。
    """
    names = [n.lower() for n in (process_names or ["wxwork.exe"])]
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _):
        if not _u32.IsWindowVisible(hwnd):
            return True
        try:
            cls = ctypes.create_unicode_buffer(256)
            _u32.GetClassNameW(hwnd, cls, 256)
            if cls.value != window_class:
                return True
            pid = wintypes.DWORD()
            _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if _exe_of_pid(pid.value) not in names:
                return True
            title = ctypes.create_unicode_buffer(256)
            _u32.GetWindowTextW(hwnd, title, 256)
            if len(title.value) > min_title_len:
                found.append((int(hwnd), title.value))
        except Exception:
            pass
        return True

    _u32.EnumWindows(_cb, 0)
    if not found:
        return None
    if prefer_title:
        for h, t in found:
            if t == prefer_title:
                return h
        logger.debug(f"没有标题为 {prefer_title!r} 的窗口，退回第一个匹配"
                     f"（候选标题：{[t for _, t in found]}）")
    return found[0][0]


def window_rect(hwnd: int) -> Optional[Tuple[int, int, int, int]]:
    try:
        r = wintypes.RECT()
        if not _u32.GetWindowRect(hwnd, ctypes.byref(r)):
            return None
        return (r.left, r.top, r.right - r.left, r.bottom - r.top)
    except Exception:
        return None


# ═══ 抓窗口 ═══════════════════════════════════════════════════════════

_blank_warned = False


def looks_blank(img: Image.Image) -> bool:
    """抓出来是不是一片黑（PrintWindow 在 GPU 合成窗口上的典型失败）。

    jev-chat-windows 的 KICKOFF 记着：**微信是 GPU 合成窗口，PrintWindow 容易黑屏**，
    所以他们优先用 WGC。我们在企业微信上验证过 PrintWindow 可用，但换到微信 PC
    就可能整张黑 —— 那种情况下不报错、只是 OCR 读不到任何字，扫描会"正常地什么都不干"。
    这里主动识别黑帧，让调用方退回到区域抓屏。
    """
    arr = np.asarray(img.convert("RGB"))
    if arr.size == 0:
        return True
    return int(arr.max()) < 12


def grab_window(hwnd: int, check_blank: bool = True) -> Optional[Image.Image]:
    """PrintWindow 抓整个窗口 → RGB PIL 图。失败（或抓到黑帧）返回 None。"""
    global _blank_warned
    rect = window_rect(hwnd)
    if not rect:
        return None
    left, top, w, h = rect
    if w <= 0 or h <= 0:
        return None
    hdc = _u32.GetWindowDC(hwnd)
    if not hdc:
        return None
    mdc = _g32.CreateCompatibleDC(hdc)
    bmp = _g32.CreateCompatibleBitmap(hdc, w, h)
    old = None
    try:
        old = _g32.SelectObject(mdc, bmp)
        if not _u32.PrintWindow(hwnd, mdc, PW_RENDERFULLCONTENT):
            return None
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = w
        info.bmiHeader.biHeight = -h  # 负数 = 自上而下
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0  # BI_RGB
        buf = ctypes.create_string_buffer(w * h * 4)
        got = _g32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(info), 0)
        if got == 0:
            return None
        arr = np.frombuffer(buf, dtype=np.uint8, count=w * h * 4)
        arr = arr.reshape(h, w, 4)[:, :, :3][:, :, ::-1]  # BGRA → RGB
        img = Image.fromarray(np.ascontiguousarray(arr))
        if check_blank and looks_blank(img):
            if not _blank_warned:
                _blank_warned = True
                logger.warning(
                    "PrintWindow 抓到黑帧（该窗口可能是 GPU 合成自绘窗口，比如微信 PC 4.x）。"
                    "已退回区域抓屏；若持续如此，需要改用 Windows Graphics Capture。")
            return None
        return img
    except Exception as e:
        logger.debug(f"grab_window 失败: {e}")
        return None
    finally:
        try:
            if old:
                _g32.SelectObject(mdc, old)
            _g32.DeleteObject(bmp)
            _g32.DeleteDC(mdc)
            _u32.ReleaseDC(hwnd, hdc)
        except Exception:
            pass


def grab_region(hwnd: int, region: Tuple[int, int, int, int]) -> Optional[Image.Image]:
    """抓窗口内的某个**屏幕坐标**矩形。区域超出窗口范围返回 None（调用方回退）。"""
    rect = window_rect(hwnd)
    if not rect:
        return None
    wl, wt, ww, wh = rect
    x, y, rw, rh = region
    cx, cy = x - wl, y - wt
    if cx < 0 or cy < 0 or cx + rw > ww or cy + rh > wh or rw <= 0 or rh <= 0:
        return None
    full = grab_window(hwnd)
    if full is None:
        return None
    return full.crop((cx, cy, cx + rw, cy + rh))


# ═══ 后台点击 ═════════════════════════════════════════════════════════

def post_click(hwnd: int, x: int, y: int, hold_ms: int = 60) -> bool:
    """向窗口投递一次左键点击（**屏幕坐标**）。窗口不必是前台，真实光标不动。

    只用于"切会话"这类不需要键盘焦点的操作；往输入框里打字仍然要真实前台。
    """
    rect = window_rect(hwnd)
    if not rect:
        return False
    cx, cy = x - rect[0], y - rect[1]
    if cx < 0 or cy < 0 or cx >= rect[2] or cy >= rect[3]:
        logger.debug(f"post_click 坐标越界: 窗口{rect} 点({x},{y})")
        return False
    lp = (cy << 16) | (cx & 0xFFFF)
    ok_down = bool(_u32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lp))
    if not ok_down:
        return False
    time.sleep(max(0, hold_ms) / 1000.0)
    ok_up = bool(_u32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lp))
    return ok_up


# ═══ 最小化处理 ═══════════════════════════════════════════════════════

def ensure_renderable(hwnd: int) -> bool:
    """窗口最小化时 Windows 根本不渲染 —— 任何截图法都拿不到画面。

    做法（抄 jev）：无激活还原 + 压到所有窗口最底下。看着像收起来了，
    但 DWM 继续画。不抢焦点、不改大小位置。返回是否动了手。
    """
    try:
        if not _u32.IsIconic(hwnd):
            return False
        _u32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
        _u32.SetWindowPos(hwnd, HWND_BOTTOM, 0, 0, 0, 0,
                          SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)
        time.sleep(0.2)
        logger.info("窗口最小化 → 已无激活还原并压到最底层（不抢焦点）")
        return True
    except Exception as e:
        logger.debug(f"ensure_renderable 失败: {e}")
        return False


def is_alive(hwnd: int) -> bool:
    try:
        return bool(hwnd) and bool(_u32.IsWindow(hwnd)) and bool(_u32.IsWindowVisible(hwnd))
    except Exception:
        return False


# ═══ 像素锚点：自检面板边界 ═══════════════════════════════════════════

def detect_panels(img: Image.Image, sample_step: int = 8,
                  bg_tolerance: int = 6, col_ratio: float = 0.30) -> Optional[dict]:
    """用像素锚点找回"面板底色/左右边界/输入框顶"（抄 jev 的 chat_area）。

    返回 ``{"bg": (r,g,b), "x0":.., "x1":.., "y0":.., "y1":..}``，认不出返回 None。

    用途是**自检**：config 里的 col1/col2 宽度是写死的像素值，
    这里算一遍实际边界，对不上就打警告 —— 只报警不自动改，
    免得自动检测认错时把能跑的扫描路径带偏。
    """
    arr = np.asarray(img.convert("RGB"))
    h, w, _ = arr.shape
    if h < 100 or w < 200:
        return None
    try:
        right = arr[::sample_step, w // 2::sample_step].reshape(-1, 3)
        vals, cnt = np.unique(right, axis=0, return_counts=True)
        bg = vals[cnt.argmax()]
        isbg = np.abs(arr.astype(np.int16) - bg.astype(np.int16)).sum(-1) <= bg_tolerance
        col = isbg[h // 4: h * 3 // 4].mean(0)
        x0 = int(np.argmax(col > col_ratio))
        x1 = w - int(np.argmax(col[::-1] > col_ratio))
        row = isbg[:, x0:x1].mean(1)
        y0 = int(np.argmax(row > 0.9))
        y1 = h - int(np.argmax(row[::-1] > 0.9))
        if x1 - x0 < 100 or y1 - y0 < 40:
            return None
        return {"bg": tuple(int(v) for v in bg), "x0": x0, "x1": x1, "y0": y0, "y1": y1}
    except Exception as e:
        logger.debug(f"detect_panels 失败: {e}")
        return None


def check_layout(img: Image.Image, chat_left_px: int, tolerance: int = 20) -> Optional[str]:
    """自检：config 里写死的聊天面板左边界（col1+col2）与像素锚点实测值是否一致。

    不一致返回一句警告（否则 None）。**只报警不自动改** —— 自动把检测值写回配置，
    万一检测认错就会把本来能跑的扫描带偏。实测本机 465 vs config 464，一致。
    """
    panels = detect_panels(img)
    if not panels:
        return None
    got = panels["x0"]
    if abs(got - chat_left_px) > tolerance:
        return (f"聊天面板左边界与 config 不符：实测 x0={got}，"
                f"config col1+col2={chat_left_px}（检查 columns.col1_width_px / col2_width_px）")
    return None
