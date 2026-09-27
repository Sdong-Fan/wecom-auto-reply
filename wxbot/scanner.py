# wxbot/scanner.py
"""Scanner 模块 — 窗口管理 + 截图 + 点击

所有布局/裁切/时序参数通过 config dict 配置。
"""

import hashlib
import os
import re
import subprocess
import time
import logging
from typing import Optional, Tuple

import mss
import numpy as np
import psutil
import pyautogui
import win32con
import win32gui
import win32process
from PIL import Image
from pynput.mouse import Controller as Mouse, Button
from pynput.keyboard import Controller as Kb, Key

logger = logging.getLogger(__name__)


def _force_foreground(hwnd: int) -> bool:
    """把 hwnd 切到前台，绕过 Windows 的前台锁定限制。

    背景：SetForegroundWindow 只允许"当前前台进程"或有最近输入事件的进程调用，
    后台进程调用会返回 0（GetLastError 常见 5=拒绝访问 / 18=无更多文件）。
    本程序的扫描跑在后台线程里，因此经常被拒绝。

    标准绕法：把自己的线程输入队列 AttachThreadInput 到「当前前台线程」和
    「目标窗口线程」，让调用方在系统看来拥有输入焦点，SetForegroundWindow
    就会被放行；最后无论成败都要 detach，否则两边线程的输入队列会一直粘连。

    所有 Win32 函数都显式声明 argtypes/restype —— 默认 c_int 返回值在 Win64
    上会把 64 位句柄截断成 32 位。
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                                ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD,
                                         wintypes.BOOL]
    user32.AttachThreadInput.restype = wintypes.BOOL
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.restype = wintypes.BOOL
    user32.SwitchToThisWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
    user32.SwitchToThisWindow.restype = None
    kernel32.GetCurrentThreadId.argtypes = []
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    attached = []
    try:
        if user32.GetForegroundWindow() == hwnd:
            return True

        cur_thread = kernel32.GetCurrentThreadId()
        fg_hwnd = user32.GetForegroundWindow()
        fg_thread = (user32.GetWindowThreadProcessId(fg_hwnd, None)
                     if fg_hwnd else 0)
        target_thread = user32.GetWindowThreadProcessId(hwnd, None)

        for t in {fg_thread, target_thread}:
            if t and t != cur_thread:
                if user32.AttachThreadInput(cur_thread, t, True):
                    attached.append(t)

        user32.BringWindowToTop(hwnd)
        if user32.SetForegroundWindow(hwnd):
            return True

        # 最后手段：未公开 API，Win10/11 仍有效
        user32.SwitchToThisWindow(hwnd, True)
        return user32.GetForegroundWindow() == hwnd
    except Exception as e:
        logger.debug(f"_force_foreground 失败: {e}")
        return False
    finally:
        for t in attached:
            try:
                user32.AttachThreadInput(cur_thread, t, False)
            except Exception:
                pass


class Scanner:
    """企业微信窗口管理 + 截图 + 点击"""

    def __init__(self, config: dict = None):
        cfg = config or {}

        # --- 窗口 ---
        wc = cfg.get("wecom", {})
        self._process_names = wc.get("process_names", ["WXWork.exe", "wxwork.exe"])
        self._window_class = wc.get("window_class", "WeWorkWindow")
        # 发送要抢前台时，按标题找窗口 —— 必须跟着 profile 走，
        # 写死「企业微信」会让微信模式把回复粘进企业微信（实测会踩）。
        self._title_hint = wc.get("title_hint", "企业微信")
        self._min_title_len = wc.get("min_title_length", 2)
        self._min_w = wc.get("min_window_width", 200)
        self._min_h = wc.get("min_window_height", 200)
        self._off_screen = wc.get("off_screen_threshold", -1000)
        self._restore_pos = tuple(wc.get("restore_position", [100, 100]))

        # --- 列布局 ---
        cols = cfg.get("columns", {})
        # 绝对像素优先：企业微信的左侧导航栏与会话列表都是**固定像素宽**，
        # 不随窗口宽度变化（实测窗口 1440→2019 时两者像素位置完全不变）。
        # 用比例会在用户改变窗口尺寸后严重错位：
        #   col2 起点漂移 → 会话行裁切混入聊天区内容
        #   col3 起点漂移 → 左对齐的客户灰气泡被切掉 → 判定"无气泡/已回复"
        self._col1_px = cols.get("col1_width_px") or None
        self._col2_px = cols.get("col2_width_px") or None
        self._col1_ratio = cols.get("col1_width_ratio", 0.153)
        self._col2_ratio = cols.get("col2_width_ratio", 0.238)
        self._bound_strip = cols.get("boundary_strip_width", 100)
        self._bound_y_start = cols.get("boundary_scan_start_ratio", 0.2)
        self._bound_y_end = cols.get("boundary_scan_end_ratio", 0.8)
        self._bound_limit = cols.get("boundary_search_limit", 80)
        self._bound_strong = cols.get("boundary_strong_threshold", 30)
        self._bound_weak = cols.get("boundary_weak_threshold", 15)

        # --- 裁切 ---
        crop = cfg.get("crop", {})
        ca = crop.get("chat_area", {})
        self._chat_rm_ratio = ca.get("right_margin_ratio", 0.30)
        self._chat_top_ratio = ca.get("top_crop_ratio", 0.12)
        self._chat_bot_ratio = ca.get("bottom_crop_ratio", 0.70)
        # 绝对像素边距优先：聊天区顶部标题栏高度固定，底部输入框则是
        # **从窗口底部固定偏移**。用高度比例会在窗口改尺寸后错位 ——
        # 实测 bottom_crop_ratio=0.70 会把最新消息（永远在列表底部）切掉，
        # 导致程序读到的是历史消息（旧文本被反复识别）。
        self._chat_top_px = ca.get("top_margin_px") or None
        self._chat_bot_px = ca.get("bottom_margin_px") or None

        cp = crop.get("click_position", {})
        self._click_col2_x = cp.get("col2_row_x_ratio", 0.333)
        self._click_input_x = cp.get("input_box_x_ratio", 0.333)
        self._click_input_bot = cp.get("input_box_bottom_offset", 60)
        self._click_scroll_x = cp.get("scroll_center_x_ratio", 0.5)
        self._click_scroll_y = cp.get("scroll_center_y_ratio", 0.5)

        # --- 时序 ---
        tmg = cfg.get("timing", {})
        self._t_focus = tmg.get("focus_restore_delay", 0.3)
        self._t_click = tmg.get("click_before_delay", 0.1)

        self._hwnd: Optional[int] = None
        self._region: Optional[Tuple[int, int, int, int]] = None
        self._col2: Optional[Tuple[int, int, int, int]] = None
        self._col3: Optional[Tuple[int, int, int, int]] = None
        self.mouse = Mouse()
        self.kb = Kb()
        self._cfg = cfg      # 发送前定位会话要用到行几何/动态行开关

        # --- 采集方式 ---
        # native_window=True：抓窗口用 PrintWindow、切会话用 PostMessage，
        # 全程不抢前台（探针证据 docs/探针-采集方式.md）。抓到的画面天然不含
        # 我们自己的 GUI，也不受"用户正在别的窗口打字"影响。
        # 发送那一路仍要抢前台（Ctrl+V/Enter 需要键盘焦点），不受此开关影响。
        cap = cfg.get("capture", {})
        self._native = bool(cap.get("native_window", True))
        self._grab_cache_ms = cap.get("grab_cache_ms", 40)
        self._post_click_hold_ms = cap.get("post_click_hold_ms", 60)
        self._grab_cache = (0.0, None)  # (时间戳, 整窗图)

    # ═══ 窗口管理 ═══════════════════════════

    def find_window(self) -> Optional[int]:
        if self._hwnd and self._is_valid(self._hwnd):
            return self._hwnd

        wxwork_pids = []
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                name = proc.info['name'] or ""
                if any(pn.lower() in name.lower()
                       for pn in self._process_names):
                    wxwork_pids.append(proc.info['pid'])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        if not wxwork_pids:
            return None

        found = []

        def enum_cb(h, _):
            try:
                _, pid = win32process.GetWindowThreadProcessId(h)
                if pid not in wxwork_pids:
                    return
                cls = win32gui.GetClassName(h)
                title = win32gui.GetWindowText(h)
                if (cls == self._window_class
                        and len(title) > self._min_title_len
                        and win32gui.IsWindowVisible(h)):
                    found.append(h)
            except Exception:
                pass

        win32gui.EnumWindows(enum_cb, None)

        if found:
            self._hwnd = found[0]
            return self._hwnd
        return None

    def _is_valid(self, hwnd: int) -> bool:
        try:
            return win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd)
        except Exception:
            return False

    def focus(self):
        """让企业微信可以被扫描。

        native 模式（默认）：不抢前台 —— PrintWindow 抓窗口、PostMessage 点击
        都不需要前台。只处理"最小化"（Windows 不渲染最小化窗口，什么都抓不到）。
        legacy 模式：保持原来的抢前台行为。
        """
        hwnd = self.find_window()
        if not hwnd:
            return False

        if self._native:
            try:
                from wxbot import native_win
                native_win.ensure_renderable(hwnd)
                return True
            except Exception as e:
                logger.debug(f"native focus 失败，回退抢前台: {e}")

        try:
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            # 后台线程调用会撞上 Windows 前台锁定（错误码 5=拒绝访问 /
            # 18=无更多文件）。pywin32 有时抛异常、有时只返回 0，
            # 两种都要覆盖，然后走 AttachThreadInput 兜底。
            try:
                fg_ok = bool(win32gui.SetForegroundWindow(hwnd))
            except Exception as e:
                logger.debug(f"SetForegroundWindow 抛异常: {e}")
                fg_ok = False
            if not fg_ok:
                logger.debug("SetForegroundWindow 未成功，改用 AttachThreadInput 兜底")
                if not _force_foreground(hwnd):
                    raise RuntimeError("无法将企业微信窗口切到前台")
            time.sleep(self._t_focus)

            rect = win32gui.GetWindowRect(hwnd)
            if rect[0] < self._off_screen or rect[1] < self._off_screen:
                w = rect[2] - rect[0]
                h = rect[3] - rect[1]
                win32gui.MoveWindow(
                    hwnd, self._restore_pos[0], self._restore_pos[1],
                    w, h, True)
                time.sleep(self._t_focus)
                logger.info(f"窗口移回屏幕: {self._restore_pos}")
            return True
        except Exception as e:
            logger.warning(f"focus失败: {e}")
            return False

    def is_running(self) -> bool:
        return self.find_window() is not None

    # ═══ 窗口区域 ═══════════════════════════

    def get_region(self) -> Optional[Tuple[int, int, int, int]]:
        hwnd = self.find_window()
        if not hwnd:
            return None
        try:
            rect = win32gui.GetWindowRect(hwnd)
            left, top, right, bottom = rect
            w, h = right - left, bottom - top
            if (w > self._min_w and h > self._min_h
                    and left > self._off_screen
                    and top > self._off_screen):
                self._region = (left, top, w, h)
                return self._region
        except Exception:
            pass
        return self._region

    def get_col2_region(self) -> Optional[Tuple[int, int, int, int]]:
        r = self.get_region()
        if not r:
            return None

        left, top, w, h = r

        if self._col1_px:
            # 固定像素：与窗口宽度无关
            col2_x = left + self._col1_px
        else:
            col1_end = self._detect_col1_boundary(r)
            if col1_end is None:
                col1_end = r[0] + int(r[2] * self._col1_ratio)
            col2_x = col1_end

        col2_w = self._col2_px or int(w * self._col2_ratio)
        self._col2 = (col2_x, top, col2_w, h)
        return self._col2

    def get_col3_region(self) -> Optional[Tuple[int, int, int, int]]:
        r = self.get_region()
        c2 = self.get_col2_region()
        if not r or not c2:
            return None
        left, top, w, h = r
        col3_x = c2[0] + c2[2]
        col3_w = w - (col3_x - left)
        self._col3 = (col3_x, top, col3_w, h)
        return self._col3

    def _detect_col1_boundary(self, region) -> Optional[int]:
        try:
            left, top, w, h = region
            strip_w = min(self._bound_strip, w // 3)
            img = self.capture_region(left, top, strip_w, h)
            arr = np.array(img.convert('RGB'))

            y_s = int(h * self._bound_y_start)
            y_e = int(h * self._bound_y_end)
            region_arr = arr[y_s:y_e, :, :].astype(float)

            col_means = np.mean(region_arr, axis=0)
            diffs = np.sqrt(np.sum(
                (col_means[1:] - col_means[:-1]) ** 2, axis=1))

            last_boundary = None
            limit = min(self._bound_limit, len(diffs))
            for i in range(limit):
                if diffs[i] > self._bound_strong:
                    last_boundary = i
            if last_boundary is not None:
                return left + last_boundary + 1

            for i in range(limit):
                if diffs[i] > self._bound_weak:
                    last_boundary = i
            if last_boundary is not None:
                return left + last_boundary + 1
        except Exception:
            pass
        return None

    # ═══ 截图 ════════════════════════════

    def _grab_window_cached(self) -> Optional[Image.Image]:
        """PrintWindow 抓整窗（带 40ms 缓存）。一次扫描里会抓好几个区域，
        没必要每次都重新 PrintWindow 一遍 2019x1728。失败返回 None（调用方回退 mss）。"""
        now = time.perf_counter()
        ts, img = self._grab_cache
        if img is not None and (now - ts) * 1000 < self._grab_cache_ms:
            return img
        hwnd = self.find_window()
        if not hwnd:
            return None
        try:
            from wxbot import native_win
            full = native_win.grab_window(hwnd)
        except Exception as e:
            logger.debug(f"PrintWindow 抓窗口失败: {e}")
            return None
        if full is None:
            return None
        self._grab_cache = (now, full)
        return full

    def capture_screen(self, region: Optional[Tuple[int, int, int, int]] = None) -> Image.Image:
        # native 模式：从窗口位图里裁，不碰屏幕、不抢前台。区域越界就回退 mss。
        if self._native and region:
            full = self._grab_window_cached()
            if full is not None:
                rect = win32gui.GetWindowRect(self.find_window())
                cx, cy = region[0] - rect[0], region[1] - rect[1]
                x2, y2 = cx + region[2], cy + region[3]
                if cx >= 0 and cy >= 0 and x2 <= full.size[0] and y2 <= full.size[1]:
                    return full.crop((cx, cy, x2, y2))
                logger.debug(f"区域 {region} 超出窗口位图 {full.size}，回退屏幕截图")
        with mss.mss() as sct:
            if region:
                monitor = {
                    "left": region[0], "top": region[1],
                    "width": region[2], "height": region[3],
                }
            else:
                monitor = sct.monitors[1]
            screenshot = sct.grab(monitor)
            return Image.frombytes("RGB", screenshot.size, screenshot.rgb)

    def capture_region(self, left: int, top: int, width: int, height: int) -> Image.Image:
        return self.capture_screen((left, top, width, height))

    def capture_chat_list(self) -> Optional[Image.Image]:
        c2 = self.get_col2_region()
        if not c2:
            return None
        return self.capture_region(*c2)

    def capture_chat_area(self) -> Optional[Image.Image]:
        c3 = self.get_col3_region()
        if not c3:
            return None
        img = self.capture_region(*c3)
        cw, ch = img.size
        rm = int(cw * self._chat_rm_ratio)
        top = self._chat_top_px or int(ch * self._chat_top_ratio)
        bot = (ch - self._chat_bot_px) if self._chat_bot_px \
            else int(ch * self._chat_bot_ratio)
        return img.crop((0, top, cw - rm, bot))

    def chat_fingerprint(self) -> str:
        """聊天区图像的指纹，用于判断是否已重新渲染。"""
        img = self.capture_chat_area()
        if img is None:
            return ""
        return hashlib.md5(img.tobytes()).hexdigest()

    def wait_chat_update(self, before_fp: str, timeout: float,
                         min_wait: float = 0.25,
                         stable_samples: int = 3,
                         poll: float = 0.05) -> float:
        """等聊天区渲染完成，返回实际等待秒数。

        实测企微点击会话行后：0.21s 出首帧，完全稳定要 0.4~1.0s。
        固定 sleep 1.0~1.2s 是保守值 —— 快时白等，慢时又不够。

        这里改成"画面变了、且连续 stable_samples 次采样不变就返回"：
        * ``min_wait`` 地板，避免在首帧切换的瞬间提前截图；
        * ``timeout`` 上限取原配置值 —— 万一画面始终不变（例如点的就是
          当前已打开的会话），等待时长与改前完全一致，不会更差。
        """
        t0 = time.perf_counter()
        last = None
        stable = 0
        while True:
            elapsed = time.perf_counter() - t0
            if elapsed >= timeout:
                return elapsed
            time.sleep(poll)
            if time.perf_counter() - t0 < min_wait:
                continue
            fp = self.chat_fingerprint()
            if fp != before_fp:
                if fp == last:
                    stable += 1
                    if stable >= stable_samples:
                        return time.perf_counter() - t0
                else:
                    stable = 0
            last = fp

    # ═══ 点开指定会话（发送前必须做）══════════

    @staticmethod
    def _name_core(name: str) -> str:
        """会话名去掉 @微信 后缀之类的尾巴，用来做宽松比对。"""
        if not name:
            return ""
        return re.split(r"[@（(]", name.strip())[0].strip()

    # 发送目标校验强度（config: send_target_verify）
    #   strict  名字必须完整包含（最安全）
    #   lenient 允许 OCR 错一两个字（按字符命中率）
    #   off     完全不校验 —— 省掉 1~2 秒定位，但失去"防发错人"
    LENIENT_HIT = 0.75

    def _verify_mode(self) -> str:
        mode = str(self._cfg.get("send_target_verify", "strict")).lower() \
            if hasattr(self, "_cfg") else "strict"
        return mode if mode in ("strict", "lenient", "off") else "strict"

    def _name_matches(self, want: str, got: str) -> bool:
        """``got`` 这段 OCR 文本算不算就是 ``want`` 这个会话。

        lenient 用**字符命中率**而不是整体相似度：行裁剪里除了名字还有消息预览，
        整体相似度会被预览文字拖低，正常的名字也判不过。
        命中率 0.75 的含义：4 个字错 1 个放行；读成完全另一个人（≈0）拒绝。
        """
        mode = self._verify_mode()
        if mode == "off":
            return True
        if not want or not got:
            return False
        if want in got:
            return True
        if mode == "lenient":
            hit = sum(1 for ch in want if ch in got) / len(want)
            if hit >= self.LENIENT_HIT:
                logger.info(f"会话名宽松匹配通过：{want!r} ≈ {got[:30]!r}（命中 {hit:.0%}）")
                return True
            logger.debug(f"会话名宽松匹配不通过：{want!r} vs {got[:30]!r}（命中 {hit:.0%}）")
        return False

    def _list_rows(self, c2_img) -> list:
        """会话列表里每一行的 y。优先动态切分（微信行高不固定）。"""
        scan = self._cfg.get("scan", {}) if hasattr(self, "_cfg") else {}
        fb = self._cfg.get("fallback", {}) if hasattr(self, "_cfg") else {}
        cr = self._cfg.get("crop", {}).get("name_row", {}) if hasattr(self, "_cfg") else {}
        max_rows = fb.get("max_scan_rows", 6)
        if scan.get("dynamic_rows"):
            try:
                from wxbot.list_rows import detect_list_rows
                got = detect_list_rows(
                    c2_img, max_rows=max_rows,
                    x_from_ratio=scan.get("row_x_from_ratio", 0.28),
                    x_to_ratio=scan.get("row_x_to_ratio", 0.92),
                    skip_top_px=scan.get("list_top_skip_px", 0))
                if got:
                    return [a for a, _b in got]
            except Exception as e:
                logger.debug(f"动态行定位失败，退回固定行高: {e}")
        if fb.get("start_y_px"):
            start = int(fb["start_y_px"])
        else:
            start = int(c2_img.size[1] * fb.get("start_y_ratio", 0.09))
        row_h = fb.get("row_height_px", 94)
        return [start + i * row_h for i in range(max_rows)
                if start + i * row_h + cr.get("bottom_offset", 56) <= c2_img.size[1]]

    def open_conversation(self, customer_name: str, read_row_name) -> bool:
        """把 ``customer_name`` 的会话切到打开状态，**并校验确实打开了它**。

        为什么必须有这一步：发送路径是"点输入框 → 粘贴 → 回车"，
        它只作用于**当前打开的那个会话**。如果列表里当前开的是别人，
        回复就会发错人。所以发送前先定位并点开目标会话，再读一次聊天区
        顶部的会话名做校验；**校验不通过就返回 False，调用方绝不能发**。

        Args:
            read_row_name: ``(img) -> (crop, text)``，通常是 MessageDetector.read_row_name。
        Returns:
            True = 目标会话已确认打开；False = 没找到/没打开成功 → 不要发送。
        """
        want = self._name_core(customer_name)
        if not want:
            return False
        c2_img = self.capture_chat_list()
        if c2_img is None:
            logger.warning("打开会话失败：抓不到会话列表")
            return False
        cr = self._cfg.get("crop", {}).get("name_row", {}) if hasattr(self, "_cfg") else {}
        row_top = cr.get("top_offset", -16)
        row_bot = cr.get("bottom_offset", 56)
        x0 = int(cr.get("x_from_ratio", 0.0) * c2_img.size[0])
        x1 = int(cr.get("x_to_ratio", 1.0) * c2_img.size[0])

        for y in self._list_rows(c2_img):
            row_img = c2_img.crop((x0, max(0, y + row_top), x1, y + row_bot))
            if row_img.size[0] <= 0 or row_img.size[1] <= 0:
                continue
            _crop, text = read_row_name(row_img)
            if not self._name_matches(want, text or ""):
                continue
            fp = self.chat_fingerprint()
            if not self.click_col2_row(y):
                logger.warning(f"打开会话失败：点击 y={y} 没成功")
                continue
            self.wait_chat_update(fp, self._t_focus + 1.0)
            if self.verify_open(customer_name, read_row_name):
                logger.info(f"已确认打开会话: {customer_name} (y={y})")
                return True
            logger.warning(f"点开了 y={y} 但顶部会话名不是 {want!r}，继续找")
        logger.error(f"没找到会话 {customer_name!r} —— 拒绝发送，避免发错人")
        return False

    def read_open_name(self, read_row_name) -> str:
        """读聊天区顶部的会话名，返回 OCR 原文（读不出来返回空串）。

        **为什么要单独有这个**：`verify_open` 只回 True/False，分不清"读到了别人"
        和"根本没读出来"。这两者的处理必须不同 ——
        读到别人＝点错了/列表在动，**不能**把聊天区内容算到这个人头上；
        读不出来＝能力问题，退回原来的行为（不能因为读不到就不干活）。
        """
        c3 = self.get_col3_region()
        if not c3:
            return ""
        ca = self._cfg.get("crop", {}).get("chat_area", {}) if hasattr(self, "_cfg") else {}
        hdr = int(ca.get("top_margin_px") or max(40, c3[3] * 0.10))
        try:
            header = self.capture_region(c3[0], c3[1], c3[2], max(30, hdr))
            _crop, text = read_row_name(header)
        except Exception as e:
            logger.debug(f"读会话名失败: {e}")
            return ""
        return (text or "").strip()

    def confirm_opened(self, customer_name: str, read_row_name,
                       retry_click=None) -> str:
        """点开之后确认"现在打开的确实是这个客户"。

        返回 ``"ok"`` / ``"mismatch"`` / ``"unknown"``：

        * ``ok``      名字对得上 → 可以读聊天区、可以发
        * ``mismatch`` 读到了**别人的**名字 → 点错了或列表在滚动，**别动**
        * ``unknown`` 名字读不出来 → 退回原来的行为（照旧继续）

        实测踩过的坑：点了 y=223（客户B）那一行，但聊天区还停在 客户A 的会话
        没刷新，于是把 A 的「老板你多大了」算到 客户B 头上，占位语发错了人。
        """
        want = self._name_core(customer_name)
        for attempt in (1, 2):
            got = self.read_open_name(read_row_name)
            if not got:
                return "unknown"
            if self._name_matches(want, got):
                return "ok"
            logger.warning(f"会话名校验不通过（第{attempt}次）："
                           f"期望 {want!r}，实际读到 {got[:40]!r}")
            if attempt == 1 and retry_click is not None:
                try:
                    retry_click()          # 再点一次，给界面时间刷新
                except Exception as e:
                    logger.debug(f"重试点击失败: {e}")
        return "mismatch"

    def verify_open(self, customer_name: str, read_row_name) -> bool:
        """读聊天区顶部的会话名，确认当前打开的确实是这个客户。"""
        c3 = self.get_col3_region()
        if not c3:
            return False
        ca = self._cfg.get("crop", {}).get("chat_area", {}) if hasattr(self, "_cfg") else {}
        hdr = int(ca.get("top_margin_px") or max(40, c3[3] * 0.10))
        try:
            header = self.capture_region(c3[0], c3[1], c3[2], max(30, hdr))
            _crop, text = read_row_name(header)
        except Exception as e:
            logger.debug(f"校验会话名失败: {e}")
            return False
        want = self._name_core(customer_name)
        got = text or ""
        ok = self._name_matches(want, got)
        if not ok:
            logger.warning(f"会话名校验不通过：期望含 {want!r}，实际读到 {got[:40]!r}")
        return ok

    # ═══ 点击 ════════════════════════════

    def click_position(self, x: int, y: int):
        self.mouse.position = (x, y)
        time.sleep(self._t_click)
        self.mouse.click(Button.left)

    def click_col2_row(self, y: int):
        """点聊天列表的某一行（会话行 y 是 col2 区域内的相对 y）。

        native 模式：PostMessage 后台点击 —— 不移动真实光标、不抢前台，
        用户正在别的窗口打字也不会被打断（探针已验：能切会话）。
        """
        c2 = self.get_col2_region()
        if not c2:
            return False
        x = c2[0] + int(c2[2] * self._click_col2_x)
        y_abs = c2[1] + y
        if self._native:
            hwnd = self.find_window()
            if hwnd:
                try:
                    from wxbot import native_win
                    if native_win.post_click(hwnd, x, y_abs, self._post_click_hold_ms):
                        return True
                    logger.debug("post_click 失败，回退真实鼠标点击")
                except Exception as e:
                    logger.debug(f"post_click 异常，回退真实鼠标: {e}")
        self.click_position(x, y_abs)
        return True

    def click_input_box(self):
        c3 = self.get_col3_region()
        if not c3:
            return False
        input_x = c3[0] + int(c3[2] * self._click_input_x)
        input_y = c3[1] + c3[3] - self._click_input_bot
        self.click_position(input_x, input_y)
        return True

    def scroll_to_bottom(self):
        c3 = self.get_col3_region()
        if not c3:
            return
        cx = c3[0] + int(c3[2] * self._click_scroll_x)
        cy = c3[1] + int(c3[3] * self._click_scroll_y)
        self.mouse.position = (cx, cy)
        time.sleep(self._t_click)
        self.mouse.click(Button.left)
        time.sleep(0.3)  # 点击后等待焦点稳定再 Ctrl+End
        self.kb.press(Key.ctrl)
        self.kb.press(Key.end)
        self.kb.release(Key.end)
        self.kb.release(Key.ctrl)

    # ═══ 前台切换 + 键盘发送 ═══════════════

    def _switch_to_wecom_and_back(self, func):
        """切到目标软件窗口 → 执行 func（通常是粘贴+回车）→ 切回原来的窗口。

        窗口标题必须来自 profile，不能写死「企业微信」——
        否则切到微信模式后，这里会把**企业微信**激活，回复粘到错误的地方去。
        """
        prev_window = pyautogui.getActiveWindow()
        prev_pos = pyautogui.position()
        hint = self._title_hint
        windows = pyautogui.getWindowsWithTitle(hint) if hint else []
        if not windows:
            logger.warning(f"未找到标题含 {hint!r} 的窗口，跳过操作")
            raise RuntimeError(f"未找到 {hint} 窗口")
        target_window = windows[0]
        try:
            target_window.activate()
            time.sleep(0.2)
        except Exception as e:
            logger.warning(f"切换前台失败: {e}，跳过操作")
            raise
        try:
            func()
        finally:
            try:
                if prev_window:
                    prev_window.activate()
                pyautogui.moveTo(prev_pos)
            except Exception:
                pass

    def send_message_via_keyboard(self, text: str):
        """Copy text to clipboard and send via Ctrl+V + Enter.

        Uses ctypes for direct clipboard access on Windows (no subprocess).
        """
        self._set_clipboard(text)
        # Click input box to ensure cursor is in the right place
        self.click_input_box()
        time.sleep(0.1)
        pyautogui.hotkey('ctrl', 'v')
        time.sleep(0.1)
        pyautogui.press('enter')

    @staticmethod
    def _set_clipboard(text: str):
        """Set clipboard text using ctypes (no subprocess, no shell injection).

        All Win32 functions have restype/argtypes set to prevent 64-bit
        pointer truncation (default c_int return is 32-bit on Win64).
        """
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        # ── Declare function signatures (critical for 64-bit) ──────────
        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.EmptyClipboard.restype = wintypes.BOOL
        user32.CloseClipboard.restype = wintypes.BOOL
        user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        user32.SetClipboardData.restype = wintypes.HANDLE

        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = wintypes.LPVOID
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.restype = wintypes.BOOL

        CF_UNICODETEXT = 13
        GMEM_MOVEABLE = 0x0002

        if not user32.OpenClipboard(0):
            raise RuntimeError("Failed to open clipboard")
        try:
            user32.EmptyClipboard()
            data = text.encode("utf-16-le") + b"\x00\x00"
            h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
            if not h:
                raise RuntimeError("GlobalAlloc failed")
            p = kernel32.GlobalLock(h)
            if not p:
                raise RuntimeError("GlobalLock failed (NULL pointer)")
            ctypes.memmove(p, data, len(data))
            kernel32.GlobalUnlock(h)
            if not user32.SetClipboardData(CF_UNICODETEXT, h):
                raise RuntimeError("SetClipboardData failed")
        finally:
            user32.CloseClipboard()
