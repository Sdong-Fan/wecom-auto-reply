# gui/learn_dialog.py
"""「这次改动，要学哪些？」—— 学习前让店主勾一遍。

**为什么必须勾一遍再学**：学到的语气会直接影响之后**所有**回复。自动学进去、
学歪了再去删，比事先看一眼要麻烦得多；更麻烦的是"一次性的说法"
（"这次给你免押"）被当成通用政策。

所以：每条候选后面一个框，勾中的才写进去，点「确认」才生效；
点「取消」＝这次什么都不学（不是"稍后再说"，是真的不学）。
"""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import ttk

logger = logging.getLogger(__name__)


def open_review(parent: tk.Misc, proposal: dict, on_done) -> "LearnReview":
    """``on_done(keys)``：``keys`` 是勾中的候选；取消时传空集合。"""
    return LearnReview(parent, proposal, on_done)


class LearnReview:
    def __init__(self, parent: tk.Misc, proposal: dict, on_done):
        self.proposal = proposal or {}
        self.on_done = on_done
        self._vars: dict[str, tk.BooleanVar] = {}
        self.done = False

        self.win = tk.Toplevel(parent)
        self.win.title("这次改动，要学哪些？")
        self.win.geometry("660x580")
        self.win.transient(parent)
        self.win.protocol("WM_DELETE_WINDOW", self._cancel)

        self._build()

        try:
            self.win.grab_set()
        except Exception:
            pass

    # ── 界面 ──────────────────────────────────────────────────────────

    def _build(self):
        p = self.proposal
        head = ttk.LabelFrame(self.win, text="这次对话")
        head.pack(fill=tk.X, padx=12, pady=(12, 6))
        for label, value in (("客户问", p.get("customer_question") or "（无）"),
                             ("AI 草稿", p.get("draft") or "（空）"),
                             ("你发出去", p.get("human") or "")):
            row = ttk.Frame(head)
            row.pack(fill=tk.X, padx=8, pady=1)
            ttk.Label(row, text=f"{label}：", width=8, foreground="#666").pack(side=tk.LEFT,
                                                                             anchor="n")
            ttk.Label(row, text=value[:200], wraplength=520,
                      justify="left").pack(side=tk.LEFT, anchor="w")

        body = ttk.Frame(self.win)
        body.pack(fill=tk.BOTH, expand=True, padx=12, pady=6)

        n = 0
        n += self._section(body, "语气习惯（以后所有回复都按这个说话）",
                           [(r["key"], r["text"],
                             f"（已经学过：{r['already']}）" if r.get("already") else "")
                            for r in p.get("rules", [])])
        if p.get("tone"):
            n += self._section(body, "口吻样本（以后照着这句话的味道说）",
                               [(p["tone"]["key"], p["tone"]["text"],
                                 "（已经学过）" if p["tone"].get("already") else "")])
        n += self._section(body, "新说法（会写进资料库，以后按这个回答客户）",
                           [(f["key"], f["claim"],
                             f"　客户问「{f['question'][:24]}」→ 回答这句"
                             if f.get("question") else "")
                            for f in p.get("facts", [])],
                           note="写错就是报错价，先核一遍数字")

        if n == 0:
            ttk.Label(body, text="这次没有可学的东西（可能就是一次纯粹的措辞润色）。",
                      foreground="#666").pack(anchor="w", pady=10)

        ttk.Label(self.win,
                  text="勾中的才会学；点「取消」＝这次什么都不学。"
                       "资料库里的改动之后可以在「资料库」页撤销。",
                  foreground="#8a6d00", wraplength=620,
                  justify="left").pack(anchor="w", padx=12)

        bottom = ttk.Frame(self.win)
        bottom.pack(fill=tk.X, padx=12, pady=10)
        ttk.Button(bottom, text="取消不学习",
                   command=self._cancel).pack(side=tk.RIGHT)
        ttk.Button(bottom, text="确认学习选中的",
                   command=self._confirm).pack(side=tk.RIGHT, padx=6)

    def _section(self, parent, title: str, items, note: str = "") -> int:
        if not items:
            return 0
        box = ttk.LabelFrame(parent, text=title)
        box.pack(fill=tk.X, pady=(0, 8))
        if note:
            ttk.Label(box, text=note, foreground="#b3261e").pack(anchor="w", padx=8)
        for key, text, extra in items:
            var = tk.BooleanVar(value=True)      # 默认全勾，不想学的自己去掉
            self._vars[key] = var
            row = ttk.Frame(box)
            row.pack(fill=tk.X, padx=8, pady=2)
            ttk.Checkbutton(row, variable=var).pack(side=tk.LEFT)
            ttk.Label(row, text=text, wraplength=520,
                      justify="left").pack(side=tk.LEFT, anchor="w")
            if extra:
                ttk.Label(row, text=extra, foreground="#666",
                          wraplength=520).pack(side=tk.LEFT, anchor="w")
        return len(items)

    # ── 结果 ──────────────────────────────────────────────────────────

    def selected(self) -> set:
        return {k for k, v in self._vars.items() if v.get()}

    def _confirm(self):
        self._finish(self.selected())

    def _cancel(self):
        self._finish(set())

    def _finish(self, keys):
        self.done = True
        try:
            self.win.destroy()
        except Exception:
            pass
        if self.on_done:
            try:
                self.on_done(keys)
            except Exception:
                logger.exception("处理勾选结果失败")
