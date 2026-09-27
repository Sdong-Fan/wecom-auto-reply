# gui/kb_edit.py
"""手动编辑 / 新增一条资料。

* FAQ 型（``客户问题: X`` / ``销售回答: Y``）拆成两个框，看得清哪句是问题
* 其它整段资料只给一个大框
* 保存前二次确认 —— 保存会**重新嵌入并替换**，不是改个文件那么轻
* 嵌入要加载模型（第一次 5~10 秒），所以放后台线程，界面不假死
"""

from __future__ import annotations

import logging
import threading
import tkinter as tk
from tkinter import messagebox, ttk

logger = logging.getLogger(__name__)


def open_editor(parent: tk.Misc, entry: dict, client, collection: str,
                on_saved=None, cfg: dict = None,
                prefill: dict = None) -> "EntryEditor":
    """``entry=None`` 表示新增一条；``prefill`` 只**预填输入框**（新增时用）。

    预填和 ``entry`` 是两件事：``entry`` 非空 = 编辑已有那条（保存走 update），
    ``prefill`` 是"新增，但问题框先替你写上"（比如从「最常转人工的问题」里点过来）。
    """
    return EntryEditor(parent, entry, client, collection, on_saved, cfg, prefill)


class EntryEditor:
    """``entry`` 是 ``rag.kb_tools._entry`` 那种字典（带 id / text / question / answer / faq）。

    ``entry`` 为空 = 新增模式。
    """

    def __init__(self, parent: tk.Misc, entry: dict, client, collection: str,
                 on_saved=None, cfg: dict = None, prefill: dict = None):
        self.entry = entry or {}
        self.adding = not entry
        self.client = client
        self.collection = collection
        self.on_saved = on_saved
        self.cfg = cfg or {}
        self.result = None
        self.faq = True if self.adding else bool(self.entry.get("faq"))
        # 输入框里先放什么（新增模式才用得上）
        initial = self.entry or (prefill or {})

        self.win = tk.Toplevel(parent)
        self.win.title("新增资料" if self.adding else "编辑资料")
        self.win.geometry("720x520")
        self.win.transient(parent)

        head = ttk.Frame(self.win)
        head.pack(fill=tk.X, padx=12, pady=(12, 4))
        if self.adding:
            hint, color = "填一句话问题和一句话答案 —— 客户这么问，机器人就这么答", "#1b5e20"
        elif self.faq:
            hint, color = "这条是问答格式 —— 问题和答案分开改", "#1b5e20"
        else:
            hint, color = "这条是整段资料 —— 直接改内容", "#8a6d00"
        ttk.Label(head, text=hint, foreground=color).pack(side=tk.LEFT)
        if not self.adding:
            ttk.Label(head, text=f"来源：{self.entry.get('source') or '(无)'}",
                      foreground="#666").pack(side=tk.RIGHT)

        self._q = None
        self._a = None
        body = ttk.Frame(self.win)
        body.pack(fill=tk.BOTH, expand=True, padx=12, pady=6)

        if self.faq:
            ttk.Label(body, text="客户问题").pack(anchor="w")
            self._q = tk.Text(body, height=3, wrap="word", font=("微软雅黑", 10))
            self._q.pack(fill=tk.X, pady=(0, 8))
            self._q.insert("1.0", initial.get("question", ""))

            ttk.Label(body, text="销售回答").pack(anchor="w")
            self._a = tk.Text(body, height=10, wrap="word", font=("微软雅黑", 10))
            self._a.pack(fill=tk.BOTH, expand=True)
            self._a.insert("1.0", initial.get("answer", ""))
        else:
            ttk.Label(body, text="资料内容").pack(anchor="w")
            self._a = tk.Text(body, height=16, wrap="word", font=("微软雅黑", 10))
            self._a.pack(fill=tk.BOTH, expand=True)
            self._a.insert("1.0", initial.get("text", ""))

        tip = ("提示：回答里的数字（价格、押金、天数）必须来自你自己的资料 —— "
               "编一个数字出去就是赔钱。改完建议去「试问一句」验证一下。")
        ttk.Label(self.win, text=tip, foreground="#8a6d00",
                  wraplength=680, justify="left").pack(anchor="w", padx=12)

        self._status = ttk.Label(self.win, text="", foreground="#666")
        self._status.pack(anchor="w", padx=12, pady=(4, 0))

        bottom = ttk.Frame(self.win)
        bottom.pack(fill=tk.X, padx=12, pady=10)
        ttk.Button(bottom, text="关闭", command=self.win.destroy).pack(side=tk.RIGHT)
        self._save_btn = ttk.Button(bottom, text="保存并生效", command=self._save)
        self._save_btn.pack(side=tk.RIGHT, padx=6)
        try:
            self.win.grab_set()
        except Exception:
            pass

    # ── 取内容 ────────────────────────────────────────────────────────

    def new_text(self) -> str:
        from rag.kb_tools import make_faq
        if self.faq:
            return make_faq(self._q.get("1.0", tk.END), self._a.get("1.0", tk.END))
        return self._a.get("1.0", tk.END).strip()

    def _save(self):
        from rag import kb_history, kb_tools

        text = self.new_text()
        if not text.strip():
            messagebox.showerror("保存失败", "内容不能是空的")
            return
        if self.adding:
            if not messagebox.askyesno(
                    "确认新增",
                    "新加的这条会立刻生效：之后机器人就能查到它了。\n\n"
                    "提示：问题写成客户**真的会问**的那句话，检索才容易命中。继续？"):
                return
        else:
            if text == (self.entry.get("text") or "").strip():
                self._status.config(text="内容没变，不用保存", foreground="#666")
                return
            if not messagebox.askyesno(
                    "确认修改",
                    "保存会立刻生效：这条资料会被重新生成索引，之后机器人就按新内容回答。\n\n"
                    "旧内容会留一份历史，可以撤销。继续？"):
                return

        self._save_btn.config(state="disabled")
        self._status.config(text="正在重新生成索引…（第一次要加载模型，约 5~10 秒）",
                            foreground="#8a6d00")

        old_text = self.entry.get("text") or ""
        old_id = self.entry.get("id")
        source = self.entry.get("source") or "手动添加"
        cfg = self.cfg
        adding = self.adding

        def work():
            try:
                from pipeline.embedder import chunk_id
                if adding:
                    res = kb_tools.add_entry(
                        self.client,
                        self._q.get("1.0", tk.END) if self.faq else "",
                        self._a.get("1.0", tk.END), source="手动添加",
                        collection=self.collection)
                else:
                    # 历史挂在**改完之后的新 id** 上：列表里选中一行就能撤销它
                    new_id = str(chunk_id(text))
                    kb_history.record(new_id, old_text, source, reason="手动编辑",
                                      keep=kb_history.keep_versions(cfg))
                    res = kb_tools.update_entry(self.client, old_id, text,
                                                self.collection)
            except Exception as e:
                logger.exception("保存资料失败")
                self.win.after(0, lambda: self._fail(str(e)))
                return
            self.win.after(0, lambda: self._done(res))

        threading.Thread(target=work, daemon=True).start()

    def _fail(self, why: str):
        self._save_btn.config(state="normal")
        self._status.config(text=f"✗ 保存失败：{why}", foreground="#b3261e")
        messagebox.showerror("保存失败", why)

    def _done(self, res):
        self.result = res
        self._status.config(
            text=("✓ 已新增，立即生效" if self.adding else "✓ 已保存，立即生效"),
            foreground="#1b5e20")
        if self.on_saved:
            try:
                self.on_saved(res)
            except Exception:
                pass
        self.win.after(400, self.win.destroy)
