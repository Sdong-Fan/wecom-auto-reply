# gui/unanswered_dialog.py
"""「最常转人工的问题」排行 —— 资料库的缺口清单。

机器人答不上来就会转人工。这些消息原来只躺在「待人工」里，处理完就没了，
同样的问题明天还会再来一次。这里把它们按次数排出来：

* 次数最多的排最前面 —— 补一条资料，能少转好多次人工
* 「加进资料库…」直接把这句话预填进新增资料的**问题**框，你只写答案
* 补过之后自动标「已补」；如果同一个问题**又**转人工了，会自动退回「待补」
  （说明那条资料没解决问题，得再看一眼）

统计写在 ``data/unanswered.json``，跟资料库档案无关（是全局的运营指标）。
"""

from __future__ import annotations

import datetime
import tkinter as tk
from tkinter import messagebox, ttk

from rag import unanswered

# 转人工的原因 → 一句人话
_WHY = (
    ("hallucinated_number", "回答里的数字资料里没有"),
    ("uncertainty_keyword", "回答不确定（说了「可能/大概」这类词）"),
    ("retrieval_low_confidence", "资料库里没找到相关内容"),
    ("low_confidence", "资料里有，但没把握"),
    ("generic_pattern", "答成了套话，没有实际内容"),
    ("non_text", "客户发的是图片/语音/文件"),
)


def why_text(reason: str) -> str:
    r = (reason or "").strip()
    if not r:
        return "(未记录)"
    for key, human in _WHY:
        if key in r:
            if key == "uncertainty_keyword":
                return f"{human}：{r.split(':', 1)[-1].strip()}"
            return human
    return r[:40]


def _when(ts) -> str:
    try:
        return datetime.datetime.fromtimestamp(float(ts)).strftime("%m-%d %H:%M")
    except Exception:
        return ""


def open_unanswered(parent: tk.Misc, qdrant=None, collection: str = None,
                    cfg: dict = None) -> "UnansweredDialog":
    return UnansweredDialog(parent, qdrant, collection, cfg)


class UnansweredDialog:

    def __init__(self, parent: tk.Misc, qdrant=None, collection: str = None,
                 cfg: dict = None):
        self.qdrant = qdrant
        self.collection = collection
        self.cfg = cfg or {}
        self._rows = {}                    # tree item id → 条目 dict

        self.win = tk.Toplevel(parent)
        self.win.title("最常转人工的问题")
        self.win.geometry("880x560")
        self.win.transient(parent)

        top = ttk.Frame(self.win)
        top.pack(fill=tk.X, padx=12, pady=(12, 2))
        ttk.Label(
            top,
            text="机器人答不上来的问题都在这儿。补一条资料，能少转很多次人工。",
            foreground="#1b5e20").pack(side=tk.LEFT)

        self._info = ttk.Label(self.win, text="", foreground="#666")
        self._info.pack(anchor="w", padx=12)

        cols = ("no", "count", "question", "why", "last", "state")
        self.tree = ttk.Treeview(self.win, columns=cols, show="headings",
                                 height=16)
        for c, t, w in (("no", "#", 40), ("count", "转人工次数", 90),
                        ("question", "客户问的", 300), ("why", "为什么转", 190),
                        ("last", "最近一次", 100), ("state", "状态", 60)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w")
        self.tree.tag_configure("done", foreground="#888")
        self.tree.bind("<Double-1>", lambda e: self._add_to_kb())
        sb = ttk.Scrollbar(self.win, orient=tk.VERTICAL,
                           command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(12, 0),
                       pady=6)
        sb.pack(side=tk.LEFT, fill=tk.Y, pady=6, padx=(0, 12))

        bottom = ttk.Frame(self.win)
        bottom.pack(fill=tk.X, side=tk.BOTTOM, padx=12, pady=10)
        self._status = ttk.Label(bottom, text="（双击一行＝加进资料库）",
                                 foreground="#666")
        self._status.pack(side=tk.LEFT)
        ttk.Button(bottom, text="关闭",
                   command=self.win.destroy).pack(side=tk.RIGHT)
        ttk.Button(bottom, text="清空统计", command=self._clear).pack(
            side=tk.RIGHT, padx=6)
        ttk.Button(bottom, text="标记已补", command=self._mark_done).pack(
            side=tk.RIGHT, padx=6)
        ttk.Button(bottom, text="加进资料库…", command=self._add_to_kb).pack(
            side=tk.RIGHT)

        self.reload()

    # ── 列表 ──────────────────────────────────────────────────────────

    def reload(self):
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self._rows.clear()
        items = unanswered.top(None)
        for i, it in enumerate(items, 1):
            iid = self.tree.insert(
                "", tk.END,
                values=(i, it.get("count", 0), it.get("question", ""),
                        why_text(it.get("reason", "")),
                        _when(it.get("last_ts")),
                        "已补" if it.get("done_ts") else "待补"),
                tags=("done",) if it.get("done_ts") else ())
            self._rows[iid] = it
        self._info.config(
            text=f"共 {unanswered.count()} 条不同的问题，累计转人工 "
                 f"{unanswered.total()} 次；还有 {unanswered.todo_count()} 条待补。")

    def _selected(self) -> dict:
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("先选一条", "先在上面点一行，再点这个按钮。",
                                parent=self.win)
            return {}
        return self._rows.get(sel[0], {})

    # ── 操作 ──────────────────────────────────────────────────────────

    def _add_to_kb(self):
        it = self._selected()
        q = it.get("question", "")
        if not q:
            return
        if self.qdrant is None:
            messagebox.showerror("加不了", "拿不到资料库连接（主程序没起来？）",
                                 parent=self.win)
            return
        from gui.kb_edit import open_editor
        open_editor(self.win, None, self.qdrant, self.collection,
                    on_saved=lambda res: self._added(q),
                    cfg=self.cfg, prefill={"question": q})

    def _added(self, question: str):
        unanswered.mark_done(question)
        self._status.config(text=f"✓ 已加进资料库：{question[:30]}", foreground="#1b5e20")
        self.reload()

    def _mark_done(self):
        it = self._selected()
        if not it:
            return
        unanswered.mark_done(it.get("question", ""))
        self.reload()

    def _clear(self):
        if not messagebox.askyesno(
                "确认清空",
                "清空这个排行（只清统计，不动资料库、不动「待人工」）。继续？",
                parent=self.win):
            return
        unanswered.clear()
        self.reload()
