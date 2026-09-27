# wxbot/window_mgr.py
"""Windows 版企业微信窗口管理器 — win32gui + psutil

参照 wecom-cs-mano 方案，每轮强制刷新窗口位置，自动检测列边界。
"""

import time, logging, psutil
import win32gui, win32con, win32process
from typing import Optional, Tuple
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)


class WindowManager:
    PROCESS_NAMES = ["WXWork.exe", "wxwork.exe"]
    WINDOW_CLASS = "WeWorkWindow"

    def __init__(self):
        self._hwnd: Optional[int] = None
        self._region: Optional[Tuple[int, int, int, int]] = None
        self._last_customer_tab_click = 0.0

    def find_window(self) -> Optional[int]:
        """查找企业微信主窗口句柄"""
        if self._hwnd and self._is_valid(self._hwnd):
            return self._hwnd

        wxwork_pids = []
        for proc in psutil.process_iter(['pid', 'name']):
            try:
                name = proc.info['name'] or ""
                if any(pn.lower() in name.lower()
                       for pn in self.PROCESS_NAMES):
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
                if cls == self.WINDOW_CLASS and len(title) > 2 \
                   and win32gui.IsWindowVisible(h):
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

    def get_region(self) -> Optional[Tuple[int, int, int, int]]:
        """获取窗口矩形（每轮强制刷新，不缓存）"""
        hwnd = self.find_window()
        if not hwnd:
            return None
        try:
            rect = win32gui.GetWindowRect(hwnd)
            left, top, right, bottom = rect
            w, h = right - left, bottom - top
            if w > 200 and h > 200 and left > -1000 and top > -1000:
                self._region = (left, top, w, h)
                return self._region
        except Exception:
            pass
        return self._region

    def get_col2_region(self) -> Optional[Tuple[int, int, int, int]]:
        """第二列：群聊列表。自动检测列边界"""
        r = self.get_region()
        if not r:
            return None

        # 使用 wecom-cs-mano 的列边界检测（从截图找分割线）
        col1_end = self._detect_col1_boundary(r)
        if col1_end is None:
            # fallback: 15.3%
            col1_end = r[0] + int(r[2] * 0.153)

        # col2 宽度约 23.8% 窗口宽
        left, top, w, h = r
        col2_x = col1_end
        col2_w = int(w * 0.238)
        return (col2_x, top, col2_w, h)

    def get_col3_region(self) -> Optional[Tuple[int, int, int, int]]:
        """第三列：会话窗口"""
        r = self.get_region()
        c2 = self.get_col2_region()
        if not r or not c2:
            return None
        left, top, w, h = r
        col3_x = c2[0] + c2[2]
        col3_w = w - (col3_x - left)
        return (col3_x, top, col3_w, h)

    def _detect_col1_boundary(self, region) -> Optional[int]:
        """自动检测第一列和第二列的分割线（wecom-cs-mano 方案）

        通过截取窗口左边缘区域，扫描相邻列的 RGB 差异找分割线。
        """
        try:
            from wxbot.capture import capture_region
            left, top, w, h = region
            # 截取左侧 100px 宽的区域
            strip_w = min(100, w // 3)
            img = capture_region(left, top, strip_w, h)
            arr = np.array(img.convert('RGB'))

            # 取中间 20%-80% 高度
            y_s = int(h * 0.2)
            y_e = int(h * 0.8)
            region_arr = arr[y_s:y_e, :, :].astype(float)

            # 每列平均颜色
            col_means = np.mean(region_arr, axis=0)  # (w, 3)
            diffs = np.sqrt(np.sum(
                (col_means[1:] - col_means[:-1]) ** 2, axis=1))

            # 找最后一个颜色突变（跳过导航栏内部的突变）
            last_boundary = None
            for i in range(min(80, len(diffs))):
                if diffs[i] > 30:
                    last_boundary = i

            if last_boundary is not None:
                return left + last_boundary + 1

            # 降低阈值再试
            for i in range(min(80, len(diffs))):
                if diffs[i] > 15:
                    last_boundary = i

            if last_boundary is not None:
                return left + last_boundary + 1
        except Exception:
            pass
        return None

    def focus(self):
        """将企微窗口置于前台并恢复（如最小化或移到屏幕外）"""
        hwnd = self.find_window()
        if hwnd:
            try:
                if win32gui.IsIconic(hwnd):
                    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                win32gui.SetForegroundWindow(hwnd)
                time.sleep(0.3)

                # 检查窗口是否在屏幕外，如果是则移回来
                rect = win32gui.GetWindowRect(hwnd)
                if rect[0] < -1000 or rect[1] < -1000:
                    w = rect[2] - rect[0]
                    h = rect[3] - rect[1]
                    win32gui.MoveWindow(hwnd, 100, 100, w, h, True)
                    time.sleep(0.3)
                    logger.info(f"窗口移回屏幕: (100, 100, {w}, {h})")
            except Exception as e:
                logger.warning(f"focus失败: {e}")

    def click_customer_tab(self):
        """点击第一列的「客户联系」/「外部群聊」过滤系统通知

        参照 wecom-cs-mano _click_external_chat_list。
        """
        region = self.get_region()
        if not region:
            return False
        left, top, w, h = region
        if left < -1000 or top < -1000:
            return False

        # 第一列中心 X，图标在底部 75-85%
        col1_x = left + int(w * 0.07)
        icon_y = top + int(h * 0.78)

        try:
            from pynput.mouse import Controller as M, Button
            m = M()
            m.position = (col1_x, icon_y)
            time.sleep(0.1)
            m.click(Button.left)
            time.sleep(0.5)
            logger.info(f"客户联系 ({col1_x}, {icon_y})")
            return True
        except Exception as e:
            logger.warning(f"点击失败: {e}")
            return False

    def get_chat_crop_params(self) -> Tuple[int, int, int]:
        """返回 col3 裁剪参数 (top_y, right_margin, bottom_y)

        按窗口高度比例计算，而非固定值。
        """
        r = self.get_region()
        if not r:
            return (80, 162, 526)
        h = r[3]
        # 标题栏约 12%，底部工具栏约 18%，右侧成员区约 15%
        return (int(h * 0.12), int(r[2] * 0.15), int(h * 0.82))

    def is_running(self) -> bool:
        return self.find_window() is not None
