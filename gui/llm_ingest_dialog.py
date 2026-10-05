# gui/llm_ingest_dialog.py
"""导入预览：让 LLM 把资料整理成问答，**人工过一眼再写入**。

流程：`LLM 整理 → 自动校验（数字/型号/单位） → 人工勾选 → 写入`

为什么必须有人工这一步：自动校验拦得住"数字被改"，拦不住**纯文字幻觉**——
原文没提库存，LLM 照样敢答"有现货，明天就能发"。这是价格库，错一个价格就是事故。
校验不过的条目**默认不勾选、标红**，但仍然显示出来（你可能一眼看出该怎么改）。

界面分两栏对照：左边"这条问答是从哪段原文来的"，右边"整理成的问题 / 回答"。
出问题时你能立刻看出是 LLM 编的，还是原文本身就含糊。

跑在**后台线程**里（每块一次 LLM 调用，几十块要等一会儿）。
整理期间只显示进度（"已整理 3/7 块"），全部完成后一次性列出结果 ——
逐条回填要改 `build_entries` 的返回结构，暂时不做。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import List

from gui.theme import COLORS, apply_theme, dialog_geometry, px, scale

logger = logging.getLogger(__name__)

COL_NO = "no"
COL_OK = "pick"
COL_Q = "question"
COL_A = "answer"
COL_WHY = "why"

# 默认整理多少块。★ 为什么要有上限、而且**必须明说**：
#   一块 = 一次 LLM 调用。5000 行的表格（pipeline.upload.MAX_TABLE_ROWS=5000）
#   全量整理要跑几小时、烧掉不少 token，不能一声不吭就开始。
#   但**截断必须说出来** —— 静默只整理前 N 块是最糟的行为：
#   用户以为全导进去了，实际一半没进，而且看不出来。
DEFAULT_MAX_CHUNKS = 100


def open_ingest(parent: tk.Misc, paths: List[str], qdrant=None,
                collection: str = None, on_done=None,
                model: str = None) -> "LlmIngestDialog":
    return LlmIngestDialog(parent, paths, qdrant, collection, on_done, model)


def plan_chunks(all_chunks: List[str], cap: int = DEFAULT_MAX_CHUNKS):
    """→ ``(这次要整理的块, 被截断的块数)``。

    单独抽出来是为了能直接测 —— 截断这件事**必须被算清楚并说出来**，
    不能在界面代码里靠一句切片蒙过去。
    """
    return all_chunks[:cap], max(0, len(all_chunks) - cap)


class LlmIngestDialog:
    """整理 + 预览 + 写入。构造即开始整理（后台线程）。"""

    def __init__(self, parent: tk.Misc, paths: List[str], qdrant=None,
                 collection: str = None, on_done=None, model: str = None):
        # ★ 一次只整理**一个文件**：source 要精确对应文件名
        #   （pipeline.upload.delete_source_points 靠它删索引），
        #   混着多个文件就说不清某条是哪个文件来的，出问题也没法回滚。
        if len(paths) != 1:
            raise ValueError("AI 整理一次只处理一个文件")
        self.paths = list(paths)
        self._source_name = Path(self.paths[0]).name
        self.qdrant = qdrant
        self.collection = collection
        self.on_done = on_done
        self.model = model
        self.entries: List[dict] = []
        self._picked = {}                      # tree iid → bool（勾选状态）
        self._stop = False
        self.truncated = 0                     # 因上限没整理的块数（会在界面上说明）

        self.win = tk.Toplevel(parent)
        self.win.title("AI 整理导入 —— 确认后再写入")
        self.win.geometry(dialog_geometry(self.win, 1040, 700))
        self.win.transient(parent)
        apply_theme(self.win, scale())
        self.win.minsize(px(820), px(520))
        self._build()
        self.win.protocol("WM_DELETE_WINDOW", self._on_close)
        try:
            self.win.grab_set()
        except Exception:
            pass
        threading.Thread(target=self._work, daemon=True).start()

    # ── 界面 ──────────────────────────────────────────────────────────

    def _build(self):
        top = ttk.Frame(self.win)
        top.pack(fill=tk.X, padx=12, pady=(10, 4))
        ttk.Label(top, text="AI 整理导入", font=("", 11, "bold")).pack(side=tk.LEFT)
        self._status = ttk.Label(top, text="准备中…", foreground=COLORS["ink_mute"])
        self._status.pack(side=tk.LEFT, padx=12)

        tip = ttk.Label(
            self.win,
            text="✱ 只保留原文有的事实：数字/单位/型号与原文对不上的，自动标红并默认不勾选。\n"
                 "  校验拦不住「原文没提、AI 自己加的整句话」，写入前请扫一眼。",
            foreground=COLORS["ink_mute"], justify=tk.LEFT)
        tip.pack(fill=tk.X, padx=12, pady=(0, 6))

        ops = ttk.Frame(self.win)
        ops.pack(fill=tk.X, padx=12)
        ttk.Button(ops, text="全选", command=lambda: self._set_all(True)).pack(side=tk.LEFT)
        ttk.Button(ops, text="全不选", command=lambda: self._set_all(False)).pack(side=tk.LEFT, padx=6)
        ttk.Button(ops, text="只勾通过校验的",
                   command=self._pick_ok_only).pack(side=tk.LEFT, padx=6)
        self._count = ttk.Label(ops, text="", foreground=COLORS["ink_mute"])
        self._count.pack(side=tk.LEFT, padx=12)
        self._write_btn = ttk.Button(ops, text="写入选中项", command=self._write,
                                     state=tk.DISABLED)
        self._write_btn.pack(side=tk.RIGHT)

        body = ttk.Frame(self.win)
        body.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

        cols = (COL_NO, COL_OK, COL_Q, COL_A, COL_WHY)
        widths = (COL_NO, 40), (COL_OK, 44), (COL_Q, 300), (COL_A, 380), (COL_WHY, 200)
        self.tree = ttk.Treeview(body, columns=cols, show="headings", height=18)
        heads = {COL_NO: "#", COL_OK: "写入", COL_Q: "客户问题", COL_A: "销售回答",
                 COL_WHY: "校验"}
        for c, w in widths:
            self.tree.heading(c, text=heads[c])
            self.tree.column(c, width=w, anchor="w")
        # 校验不过的整行标红；正常行走默认色
        self.tree.tag_configure("bad", foreground=COLORS["danger"])
        self.tree.tag_configure("ok", foreground=COLORS["ink"])
        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<Double-1>", self._show_source)

        sb = ttk.Scrollbar(body, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.LEFT, fill=tk.Y)

        src = ttk.LabelFrame(self.win, text="选中行的原文（双击行也能看）")
        src.pack(fill=tk.X, padx=12, pady=(0, 10))
        self._src = tk.Text(src, height=5, wrap=tk.WORD)
        self._src.pack(fill=tk.X, padx=8, pady=6)
        self._src.configure(state=tk.DISABLED)

    # ── 后台整理 ──────────────────────────────────────────────────────

    def _work(self):
        try:
            from config.settings_store import looks_like_placeholder
            from rag.llm_client import resolve_config
            from rag.llm_ingest import build_entries, chunks_from_file
        except Exception as e:                            # 依赖缺失也要说人话
            self._ui(lambda: self._fail("初始化失败：%s" % e))
            return

        key, _base, cfg_model = resolve_config()
        if not key or looks_like_placeholder(key):
            self._ui(lambda: self._fail(
                "还没配模型密钥，AI 整理用不了。\n"
                "点「设置」填好 LLM 接口密钥再回来。"))
            return

        p = self.paths[0]
        try:
            all_chunks = chunks_from_file(p, limit=100000)      # 先全读出来数一下
        except Exception as e:
            self._ui(lambda: self._fail("%s 读不了：%s" % (Path(p).name, e)))
            return
        if not all_chunks:
            self._ui(lambda: self._fail("这个文件里没有可整理的文字。"))
            return

        total_chunks = len(all_chunks)
        chunks, self.truncated = plan_chunks(all_chunks)

        # 块数多 → **先说清代价再开始**（一次调用一块，慢且花 token）
        if total_chunks > 20:
            note = ("这份资料共 %d 块，将整理**前 %d 块**（每块一次 AI 调用，"
                    "可能要几分钟）。" % (total_chunks, len(chunks)))
            if self.truncated:
                note += "\n\n剩余 %d 块这次不整理（要不要继续？\n"\
                        "想全整理请拆成几个文件分批来）。" % self.truncated
            if not self._ask(note, "开始 AI 整理？"):
                self._ui(self._on_close)
                return

        model = self.model or cfg_model
        msg = "共 %d 块" % len(chunks)
        if self.truncated:
            msg += "（该文件共 %d 块，只整理前 %d 块）" % (total_chunks, len(chunks))
        self._ui(lambda m=msg: self._status.config(
            text=m + "，正在交给 AI 整理…", foreground=COLORS["warning"]))

        try:
            entries = asyncio.run(build_entries(chunks, model=model,
                                                on_progress=self._progress))
        except Exception as e:
            self._ui(lambda: self._fail("整理失败：%s" % e))
            return
        self._ui(lambda: self._finish(entries))

    def _progress(self, done, total):
        """在整理线程里被调用 → 必须跳回 UI 线程改控件。"""
        self._ui(lambda: self._status.config(
            text="已整理 %d/%d 块…" % (done, total),
            foreground=COLORS["warning"]))

    def _ui(self, fn):
        try:
            self.win.after(0, fn)
        except Exception:
            pass

    def _ask(self, question: str, title: str) -> bool:
        """在**后台线程里**问一句（messagebox 必须跑在主线程）。

        做法：把弹窗丢给 `after(0, …)` 在主线程执行，这里等一个 Event。
        超时就当"不继续" —— 窗口被关掉时 after 会失败，不能让线程挂死。
        """
        box = {"v": False}
        ev = threading.Event()

        def ask():
            try:
                box["v"] = bool(messagebox.askyesno(title, question))
            finally:
                ev.set()

        self._ui(ask)
        ev.wait(timeout=120)
        return box["v"]

    def _fail(self, msg):
        self._status.config(text=msg.split("\n")[0], foreground=COLORS["danger"])
        messagebox.showerror("AI 整理", msg)

    def _finish(self, entries):
        self.entries = entries
        ok = sum(1 for e in entries if e["ok"])
        for n, e in enumerate(entries, 1):
            iid = self.tree.insert("", tk.END, values=(
                n, "☑" if e["ok"] else "☐",
                e["question"] or "(没整理出问题)",
                e["answer"],
                "通过" if e["ok"] else " / ".join(e["problems"])[:80],
            ), tags=("ok" if e["ok"] else "bad",))
            self._picked[iid] = e["ok"]
        self._status.config(
            text="整理完成：%d 条，通过 %d，标红 %d%s"
                 % (len(entries), ok, len(entries) - ok,
                    "（另有 %d 块未整理，受上限限制）" % self.truncated
                    if self.truncated else ""),
            foreground=COLORS["success"] if ok else COLORS["warning"])
        self._write_btn.config(state=tk.NORMAL if ok else tk.DISABLED)
        self._refresh_count()

    # ── 勾选 ──────────────────────────────────────────────────────────

    def _refresh_count(self):
        n = sum(1 for v in self._picked.values() if v)
        self._count.config(text="已勾选 %d 条" % n)

    def _paint(self, iid):
        picked = self._picked.get(iid, False)
        vals = list(self.tree.item(iid, "values"))
        vals[1] = "☑" if picked else "☐"
        self.tree.item(iid, values=vals)

    def _on_click(self, evt):
        """点「写入」那一列 = 切换勾选；点别的列 = 只选中看原文。"""
        if self.tree.identify("column", evt.x, evt.y) != 1:      # 0-based：第 2 列
            self.win.after(1, self._show_source)
            return
        iid = self.tree.identify_row(evt.y)
        if not iid:
            return
        self._picked[iid] = not self._picked.get(iid, False)
        self._paint(iid)
        self._refresh_count()
        return "break"

    def _set_all(self, value: bool):
        for iid in self.tree.get_children():
            self._picked[iid] = value
            self._paint(iid)
        self._refresh_count()

    def _pick_ok_only(self):
        for n, iid in enumerate(self.tree.get_children()):
            self._picked[iid] = self.entries[n]["ok"] if n < len(self.entries) else False
            self._paint(iid)
        self._refresh_count()

    def _show_source(self, _evt=None):
        sel = self.tree.selection()
        if not sel:
            return
        n = self.tree.index(sel[0])
        if n >= len(self.entries):
            return
        self._src.configure(state=tk.NORMAL)
        self._src.delete("1.0", tk.END)
        self._src.insert("1.0", self.entries[n]["chunk"])
        self._src.configure(state=tk.DISABLED)

    # ── 写入 ──────────────────────────────────────────────────────────

    def _write(self):
        picked = [e for n, iid in enumerate(self.tree.get_children())
                  if self._picked.get(iid) and n < len(self.entries)
                  for e in [self.entries[n]]]
        if not picked:
            messagebox.showinfo("还没勾选", "先勾几条再写。")
            return
        bad = [e for e in picked if not e["ok"]]
        if bad and not messagebox.askyesno(
                "有没通过校验的",
                "勾选里含 %d 条**没通过数字/型号校验**的条目。\n"
                "很可能是 AI 改了原文的数字，建议去掉。\n\n仍要写入吗？" % len(bad)):
            return
        if not messagebox.askyesno(
                "确认写入",
                "把勾选的 %d 条写进资料库？\n"
                "（写入后可在列表里逐条编辑，也能「撤销上次修改」）" % len(picked)):
            return

        self._write_btn.config(state=tk.DISABLED)
        self._status.config(text="正在写入 %d 条…" % len(picked),
                            foreground=COLORS["warning"])
        threading.Thread(target=self._write_work, args=(picked,), daemon=True).start()

    def _write_work(self, picked):
        try:
            from rag.llm_ingest import to_faq
            from pipeline.embedder import embed_and_store
            texts = [to_faq(e) for e in picked]
            n = embed_and_store(texts, self.qdrant, self.collection,
                                source=getattr(self, "_source_name", "AI整理"),
                                extra={"origin": "AI整理"})
        except Exception as e:
            self._ui(lambda: self._fail("写入失败：%s" % e))
            return
        self._ui(lambda: self._written(n))

    def _written(self, n):
        self._status.config(text="已写入 %d 条" % n, foreground=COLORS["success"])
        if self.on_done:
            try:
                self.on_done(n)
            except Exception:
                logger.exception("on_done 回调失败")
        messagebox.showinfo("写入完成", "已写入 %d 条资料。\n列表已刷新。" % n)
        self._on_close()

    def _on_close(self):
        self._stop = True
        try:
            self.win.grab_release()
        except Exception:
            pass
        try:
            self.win.destroy()
        except Exception:
            pass
