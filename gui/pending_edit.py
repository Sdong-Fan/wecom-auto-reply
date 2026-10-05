# gui/pending_edit.py
"""「待人工」里点【编辑】弹出的改写窗口。

★ 为什么要单独一个模块（原来写在 `main_window._handle_pending_edit` 里）：
布局 bug 没法测。店主报过"编辑的框太小、下面两个按钮被吞了" ——
根因是**按钮最后 pack**：窗口高度不够时，Tk 按 pack 顺序切空间，
最后那个（按钮条）只剩 1px，等于消失。而窗口高度是**硬编码 480x280**、
完全没走 `px()` 缩放，字体却是全局 1.5 倍 → 内容需要的高度超过窗口。

修法两条，都很关键：
1. **按钮条先 pack、`side=BOTTOM`** —— 先占位，空间不够时让**编辑框**去缩
   （编辑框可以滚动、可以把窗口拉大；按钮没了就直接没法操作了）。
2. 尺寸/内边距/字号全部走 `px()` / `dialog_geometry()` / `font()`，
   并允许拉大窗口 —— 高 DPI 下别的对话框都这么做，这个漏了。

把窗口放在 `+5000+5000`（屏幕外）也能正确测量布局，所以测试不用弹窗。
"""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import ttk

from gui.theme import COLORS, apply_theme, dialog_geometry, font, px, scale

logger = logging.getLogger(__name__)

EMPTY_HINT = ("AI 没给草稿（这个问题在资料库里找不到依据）。\n"
              "自己写一句发出去，或者把它加进资料库"
              "（知识库 → 资料库 → 新增一条）。")


def open_editor(parent: tk.Misc, customer: str, question: str = "",
                current: str = "") -> "PendingEditor":
    return PendingEditor(parent, customer, question, current)


class PendingEditor:
    """改完点「发送」→ `result` 是文本；点「取消」/关窗 → `result` 为 None。"""

    def __init__(self, parent: tk.Misc, customer: str, question: str = "",
                 current: str = ""):
        self.result = None
        self.win = tk.Toplevel(parent)
        self.win.title(f"编辑回复 — {customer}")
        # ★ 尺寸按缩放算、并夹在屏幕内（以前写死 480x280，1.5 倍字体装不下）
        self.win.geometry(dialog_geometry(self.win, 560, 380))
        self.win.transient(parent)
        apply_theme(self.win, scale())
        self.win.resizable(True, True)
        self.win.minsize(px(420), px(260))

        # ── 先 pack 按钮条（side=BOTTOM）：空间不够时保住它，让编辑框去缩 ──
        self._btn_frame = ttk.Frame(self.win)
        self._btn_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=px(12), pady=px(10))
        ttk.Button(self._btn_frame, text="发送", command=self._confirm).pack(side=tk.LEFT)
        ttk.Button(self._btn_frame, text="取消", command=self.win.destroy).pack(
            side=tk.LEFT, padx=px(8))

        head = ttk.Frame(self.win)
        head.pack(fill=tk.X, padx=px(12), pady=(px(12), px(4)))
        ttk.Label(head, text=f"客户: {customer}").pack(anchor="w")
        if question:
            # wraplength：客户消息可能很长，不换行会把窗口横向撑爆（然后被裁掉）
            ttk.Label(head, text=f"客户问的是: {question}",
                      foreground=COLORS["ink_mute"], justify="left",
                      wraplength=px(500)).pack(anchor="w", pady=(px(2), 0))

        if current.strip():
            ttk.Label(self.win, text="AI 推荐回复（可修改后发送）:").pack(
                anchor="w", padx=px(12))
        else:
            ttk.Label(self.win, text=EMPTY_HINT, foreground=COLORS["warning"],
                      justify="left", wraplength=px(500)).pack(
                anchor="w", padx=px(12))

        # 编辑框**最后 pack** —— 剩多少空间它拿多少；不够就缩它（可以滚动）
        self.text_box = tk.Text(self.win, height=8, wrap=tk.WORD,
                                font=font("small"))
        self.text_box.pack(fill=tk.BOTH, expand=True, padx=px(12), pady=px(8))
        self.text_box.insert("1.0", current)
        self.text_box.focus_set()

        # Esc = 取消，Ctrl+Enter = 发送（长文本里点按钮麻烦）
        self.win.bind("<Escape>", lambda _e: self.win.destroy())
        self.win.bind("<Control-Return>", lambda _e: self._confirm())

        try:
            self.win.grab_set()
        except Exception:
            pass

    def _confirm(self):
        self.result = self.text_box.get("1.0", tk.END).strip()
        self.win.destroy()

    def wait(self) -> str:
        """阻塞等用户操作完，返回结果文本（取消返回空串）。"""
        try:
            self.win.wait_window()
        except Exception:
            pass
        return self.result or ""
