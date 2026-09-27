# debug/visualizer.py
"""调试可视化模块 — 截图标注 + meta.json

职责：
- 保存每步截图
- 标注检测结果
- 生成结构化 meta.json
"""

import json
import os
import shutil
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont


class DebugVisualizer:
    """调试可视化器"""

    def __init__(self, output_dir: str = "debug", max_history: int = 10):
        self.output_dir = output_dir
        self.max_history = max_history
        self._session_dir: Optional[str] = None
        self._meta: Dict[str, Any] = {}

    def start_session(self) -> str:
        """开始新的调试会话，自动结束上一轮未完成会话"""
        # 先结束上一轮（防止 continue 跳过 finish 导致会话孤立）
        if self._session_dir is not None:
            self.finish()

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self._session_dir = os.path.join(self.output_dir, timestamp)
        os.makedirs(self._session_dir, exist_ok=True)

        self._meta = {
            "timestamp": datetime.now().isoformat(),
            "red_dots": [],
            "customer_name": "",
            "is_customer": False,
            "bubbles": [],
            "unreplied": [],
            "action": "",
            "reply_text": "",
        }

        # 清理旧会话
        self._cleanup_old_sessions()

        return self._session_dir

    def _cleanup_old_sessions(self):
        """清理旧的调试会话"""
        if not os.path.exists(self.output_dir):
            return

        sessions = []
        for name in os.listdir(self.output_dir):
            path = os.path.join(self.output_dir, name)
            if os.path.isdir(path) and name != "latest":
                sessions.append(path)

        sessions.sort(key=lambda p: os.path.getmtime(p), reverse=True)

        for path in sessions[self.max_history:]:
            try:
                shutil.rmtree(path)
            except Exception:
                pass

    def _get_path(self, filename: str) -> str:
        """获取文件路径"""
        if not self._session_dir:
            self.start_session()
        return os.path.join(self._session_dir, filename)

    def save_chat_list(self, img: Image.Image,
                       red_dots: List[int] = None) -> str:
        """保存聊天列表截图，标注红点"""
        path = self._get_path("01_chat_list.png")

        if red_dots:
            img = img.copy()
            draw = ImageDraw.Draw(img)
            w = img.size[0]
            scan_w = max(int(w * 0.05), 20)

            for y in red_dots:
                # 画红色矩形框
                draw.rectangle(
                    [0, y - 2, scan_w, y + 2],
                    outline="red", width=2
                )

            self._meta["red_dots"] = [{"y": y} for y in red_dots]

        img.save(path)
        return path

    def save_name_crop(self, img: Image.Image, name: str = "") -> str:
        """保存名字区域截图"""
        path = self._get_path("02_name_crop.png")
        img.save(path)

        if name:
            self._meta["customer_name"] = name

        return path

    def save_chat_area(self, img: Image.Image) -> str:
        """保存聊天区域截图"""
        path = self._get_path("03_chat_area.png")
        img.save(path)
        return path

    def save_bubble_mask(self, chat_img: Image.Image,
                         bubbles: List[Tuple[int, int, bool]]) -> str:
        """保存气泡 mask"""
        path = self._get_path("04_bubble_mask.png")

        # 创建 mask 图像
        mask = Image.new("RGB", chat_img.size, (255, 255, 255))
        draw = ImageDraw.Draw(mask)

        for top, bot, is_blue in bubbles:
            color = (100, 149, 237) if is_blue else (200, 200, 200)
            draw.rectangle([0, top, chat_img.size[0], bot], fill=color)

        mask.save(path)

        # 更新 meta
        self._meta["bubbles"] = [
            {
                "type": "blue" if is_blue else "gray",
                "top": top,
                "bottom": bot,
            }
            for top, bot, is_blue in bubbles
        ]

        return path

    def save_ocr_result(self, img: Image.Image, text: str = "") -> str:
        """保存 OCR 结果叠加"""
        path = self._get_path("05_ocr_result.png")

        if text:
            img = img.copy()
            draw = ImageDraw.Draw(img)

            # 在底部绘制文字
            try:
                font = ImageFont.truetype("msyh.ttc", 14)
            except Exception:
                font = ImageFont.load_default()

            # 背景矩形
            text_bbox = draw.textbbox((0, 0), text[:50], font=font)
            text_h = text_bbox[3] - text_bbox[1] + 10
            draw.rectangle(
                [0, img.size[1] - text_h, img.size[0], img.size[1]],
                fill=(0, 0, 0, 128),
            )

            # 文字
            draw.text(
                (5, img.size[1] - text_h + 5),
                text[:50],
                fill="white",
                font=font,
            )

        img.save(path)
        return path

    def update_meta(self, **kwargs):
        """更新 meta 信息"""
        self._meta.update(kwargs)

    def set_action(self, action: str, reply_text: str = ""):
        """设置动作"""
        self._meta["action"] = action
        if reply_text:
            self._meta["reply_text"] = reply_text

    def save_meta(self) -> str:
        """保存 meta.json"""
        path = self._get_path("meta.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._meta, f, ensure_ascii=False, indent=2)
        return path

    def create_latest_link(self):
        """创建 latest 软链接"""
        if not self._session_dir:
            return

        latest_path = os.path.join(self.output_dir, "latest")

        # Windows 下删除旧链接
        if os.path.exists(latest_path):
            if os.path.islink(latest_path):
                os.unlink(latest_path)
            elif os.path.isdir(latest_path):
                shutil.rmtree(latest_path)

        # 创建新链接
        try:
            os.symlink(
                os.path.basename(self._session_dir),
                latest_path,
                target_is_directory=True,
            )
        except Exception:
            # Windows 可能不支持软链接，复制一份
            try:
                shutil.copytree(self._session_dir, latest_path)
            except Exception:
                pass

    def finish(self):
        """完成调试会话"""
        self.save_meta()
        self.create_latest_link()
