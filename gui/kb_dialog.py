# gui/kb_dialog.py
"""前端「知识库」面板：编辑提示词 + 管理资料库。

三页：**提示词**（可编辑）、**资料库**（问题/答案分列看 + 上传 + 手动改/删/撤销 + 重建索引）、
**试问一句**（走一遍线上检索，看分数、命中的问答、会不会直发）。

设计要点：
* **可自由编辑**：业务提示词、闲聊提示词、占位语提示词、口吻样本
* **受保护区只读**：安全规则（不许编造/用词必须确定…）单独展示，不参与编辑 ——
  用户手一抖删了这些，guard 会把大量回复拦成"需要人工处理"，
  他会以为程序坏了，其实是规则没了。
* 保存前**校验占位符**（缺 `{context}` 会导致生成回复时 KeyError）
* **恢复默认**＝删掉文件，读取时自动回退出厂值
* 保存后**立即生效**（下次生成就用到新提示词，不用重启）
* 上传**必须留一份源**（重建索引会清空 Qdrant，源才是资产）+ 导入跑后台线程
* 单条**手动编辑/删除/撤销**：改前先存历史（`rag/kb_history.py`），改完立即生效
* 列表按「客户问题 / 销售回答」**分列**：挤一列里 Treeview 只显示第一行，
  用户会看到满屏"客户问题"、以为答案没导进来
"""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import messagebox, ttk

from rag import prompt_store as ps
from gui.theme import COLORS, apply_theme, dialog_geometry, font, px, scale

logger = logging.getLogger(__name__)

TABS = [
    ("system", "业务回复"),
    ("smalltalk", "闲聊"),
    ("hold", "占位语"),
    ("tone_samples", "口吻样本"),
    ("welcome", "欢迎语"),
    ("nontext", "非文本应答"),
]


def open_kb(parent: tk.Misc, cfg: dict = None, on_saved=None,
            qdrant=None, collection: str = None) -> "KbDialog":
    return KbDialog(parent, cfg, on_saved, qdrant, collection)


class KbDialog:
    def __init__(self, parent: tk.Misc, cfg: dict = None, on_saved=None,
                 qdrant=None, collection: str = None):
        self.cfg = cfg or {}
        self.on_saved = on_saved
        # 复用主程序已经打开的 Qdrant 客户端（本地文件模式不能开第二个）
        self.qdrant = qdrant
        # 显式给了就用它（测试）；否则**每次现读当前档案**（界面上切了立刻生效）
        self._collection_override = collection
        ps.ensure_files()

        self.win = tk.Toplevel(parent)
        self.win.title("知识库")
        self.win.geometry(dialog_geometry(self.win, 980, 720))
        self.win.transient(parent)
        apply_theme(self.win, scale())
        self.win.resizable(True, True)
        self.win.minsize(px(760), px(520))
        self._build()
        try:
            self.win.grab_set()
        except Exception:
            pass

    @property
    def collection(self) -> str:
        """当前档案的 collection。**不是构造时定死的** —— 中途切档案要跟着换。"""
        if self._collection_override:
            return self._collection_override
        from rag import archives
        return archives.active_collection()

    # ── 界面 ──────────────────────────────────────────────────────────

    def _build(self):
        # 底部先建：提示词页初始化时要用到 self._status
        bottom = ttk.Frame(self.win)
        bottom.pack(fill=tk.X, side=tk.BOTTOM, padx=10, pady=10)
        self._status = ttk.Label(bottom, text="", foreground=COLORS["success"])
        self._status.pack(side=tk.LEFT)
        ttk.Button(bottom, text="关闭", command=self.win.destroy).pack(side=tk.RIGHT, padx=6)
        ttk.Button(bottom, text="保存", command=self._save).pack(side=tk.RIGHT)
        ttk.Button(bottom, text="恢复默认", command=self._reset).pack(side=tk.RIGHT, padx=6)

        # ══ 档案栏（放在标签页**上面**，四页都看得见当前在哪个库）══
        if not self._collection_override:
            self._build_archive_bar()

        nb = ttk.Notebook(self.win)
        nb.pack(fill=tk.BOTH, expand=True, padx=10, pady=(6, 0))
        self._nb = nb

        # ══ 提示词页 ══
        page = ttk.Frame(nb)
        nb.add(page, text="提示词")
        self._build_prompt_page(page)

        # ══ 资料库：看现在库里有什么 ══
        f_kb = ttk.Frame(nb)
        nb.add(f_kb, text="资料库")
        self._build_kb_page(f_kb)

        # ══ 试问一句：检索验证 ══
        f_probe = ttk.Frame(nb)
        nb.add(f_probe, text="试问一句")
        self._build_probe_page(f_probe)

        # ══ 学到的：从人工改稿里学到什么 ══
        f_learn = ttk.Frame(nb)
        nb.add(f_learn, text="学到的")
        self._build_learn_page(f_learn)

    # ── 学到的页 ──────────────────────────────────────────────────────

    def _build_learn_page(self, page):
        """人工改稿学到了什么 —— 透明、可删、可关。

        这里是"程序自己长大"的那部分，所以三件事必须都能做：
        看得见（列出来 + 预览实际注入的内容）、删得掉（+「这条别学」）、关得上。
        """
        from rag import learn_store as ls

        top = ttk.Frame(page)
        top.pack(fill=tk.X, padx=10, pady=(10, 4))
        self._learn_on = tk.BooleanVar(
            value=ls.enabled(self.cfg))
        ttk.Checkbutton(top, text="自动学习（人工改完发出去就学）",
                        variable=self._learn_on,
                        command=self._toggle_learn).pack(side=tk.LEFT)
        ttk.Button(top, text="刷新", command=self._learn_load).pack(side=tk.LEFT, padx=8)
        ttk.Button(top, text="预览实际注入给 AI 的内容",
                   command=self._learn_preview).pack(side=tk.LEFT)
        self._learn_info = ttk.Label(top, text="", foreground=COLORS["ink_mute"])
        self._learn_info.pack(side=tk.LEFT, padx=10)

        tip = ("**出厂默认是关闭的** —— 想让它开始学，先勾上左边那个框。\n"
               "开起来之后只自动学「怎么说话」；人工回复里带出的**新说法**"
               "（价格/时效/政策）一律要你确认才进资料库 —— "
               "不然一次性的「这次给你免押」会变成通用政策。")
        ttk.Label(page, text=tip, foreground=COLORS["warning"],
                  wraplength=780, justify="left").pack(anchor="w", padx=12)

        # 待确认的新知识（最重要，放最上面）
        f_facts = ttk.LabelFrame(
            page, text="待确认的说法（改稿学到的，或你在待人工页直接发送确认过的）")
        f_facts.pack(fill=tk.BOTH, expand=True, padx=10, pady=(6, 4))
        cols = ("claim", "question", "src", "when")
        self._fact_tree = ttk.Treeview(f_facts, columns=cols, show="headings", height=6)
        for c, t, w in (("claim", "回答 / 新说法", 380),
                        ("question", "客户问的是", 210),
                        ("src", "来源", 80), ("when", "时间", 120)):
            self._fact_tree.heading(c, text=t)
            self._fact_tree.column(c, width=w, anchor="w")
        sb = ttk.Scrollbar(f_facts, orient=tk.VERTICAL, command=self._fact_tree.yview)
        self._fact_tree.configure(yscrollcommand=sb.set)
        self._fact_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(6, 0), pady=6)
        sb.pack(side=tk.LEFT, fill=tk.Y, pady=6, padx=(0, 6))
        f_btns = ttk.Frame(f_facts)
        f_btns.pack(side=tk.LEFT, fill=tk.Y, padx=6, pady=6)
        ttk.Button(f_btns, text="采纳进资料库",
                   command=self._fact_accept).pack(fill=tk.X, pady=2)
        ttk.Button(f_btns, text="丢弃", command=self._fact_reject).pack(fill=tk.X, pady=2)

        # 语气规则 + 口吻样本
        f_tone = ttk.LabelFrame(page, text="已学到的语气（自动生效，可删）")
        f_tone.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        cols2 = ("kind", "text", "hits")
        self._learn_tree = ttk.Treeview(f_tone, columns=cols2, show="headings", height=8)
        for c, t, w in (("kind", "类型", 70), ("text", "内容", 660), ("hits", "学了几次", 70)):
            self._learn_tree.heading(c, text=t)
            self._learn_tree.column(c, width=w, anchor="w")
        sb2 = ttk.Scrollbar(f_tone, orient=tk.VERTICAL, command=self._learn_tree.yview)
        self._learn_tree.configure(yscrollcommand=sb2.set)
        self._learn_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(6, 0), pady=6)
        sb2.pack(side=tk.LEFT, fill=tk.Y, pady=6, padx=(0, 6))
        f_btns2 = ttk.Frame(f_tone)
        f_btns2.pack(side=tk.LEFT, fill=tk.Y, padx=6, pady=6)
        ttk.Button(f_btns2, text="这条别学",
                   command=self._learn_block).pack(fill=tk.X, pady=2)
        ttk.Button(f_btns2, text="删掉这条",
                   command=self._learn_delete).pack(fill=tk.X, pady=2)

        self._learn_load()

    def _learn_rows(self) -> dict:
        return getattr(self, "_learn_map", {})

    def _learn_load(self):
        from rag import learn_store as ls
        if hasattr(self, "_learn_map"):
            for i in self._learn_tree.get_children():
                self._learn_tree.delete(i)
        self._learn_map = {}
        for r in ls.style_rules():
            k = self._learn_tree.insert("", tk.END, values=("语气规则", r.get("rule", ""),
                                                            r.get("hits", 1)))
            self._learn_map[k] = (ls.STYLE_RULES, r)
        for t in ls.tone_samples():
            k = self._learn_tree.insert("", tk.END, values=("口吻样本", t.get("text", ""),
                                                            t.get("hits", 1)))
            self._learn_map[k] = (ls.TONE_SAMPLES, t)
        for i in self._fact_tree.get_children():
            self._fact_tree.delete(i)
        self._fact_map = {}
        for f in ls.facts("pending"):
            k = self._fact_tree.insert("", tk.END, values=(f.get("claim", ""),
                                                           f.get("question", "")[:60],
                                                           f.get("source", "改稿学到"),
                                                           f.get("ts", "")))
            self._fact_map[k] = f
        c = ls.counts()
        on = ls.enabled(self.cfg)
        self._learn_info.config(
            text=(f"学习开关：{'已开启' if on else '未开启（出厂默认是关的）'}"
                  f"｜语气规则 {c['rules']} 条｜口吻样本 {c['tones']} 条｜"
                  f"待确认 {c['pending_facts']} 条｜别学 {c['blocked']} 条｜"
                  f"采纳过 {c['accepts']} 个问题"),
            foreground=COLORS["success"] if on else COLORS["warning"])

    # ── 学到的页：动作 ────────────────────────────────────────────────

    def _toggle_learn(self):
        """开关写回 config.json（重启也记得）。"""
        from config.settings_store import save_config
        want = bool(self._learn_on.get())
        self.cfg.setdefault("kb", {}).setdefault("learn", {})["enabled"] = want
        try:
            save_config(self.cfg)
        except Exception as e:
            messagebox.showerror("保存失败", f"改不了配置：{e}")
            return
        # 走一遍 _learn_load，让"开关状态 + 条数"这一行和实际配置保持一致
        # （只在这里写死一句话的话，下次刷新就被条数覆盖掉了）
        self._learn_load()

    def _learn_preview(self):
        """把真正注入给 AI 的那段显示出来 —— 学偏了要能一眼看出来。"""
        from rag.learn import learned_block
        block = learned_block(self.cfg)
        w = tk.Toplevel(self.win)
        w.title("实际注入给 AI 的内容")
        w.geometry("760x520")
        w.transient(self.win)
        t = tk.Text(w, wrap="word", font=font("small"))
        t.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        t.insert("1.0", block or "（现在什么都没注入 —— 要么还没学到，要么学习关着）")
        t.configure(state="disabled")

    def _learn_block(self):
        from rag import learn_store as ls
        sel = self._learn_tree.selection()
        if not sel:
            messagebox.showinfo("先选一条", "先在上面点一行。")
            return
        kind, row = self._learn_map.get(sel[0], (None, None))
        if not kind:
            return
        if messagebox.askyesno("这条别学",
                               f"删掉这条，并且以后不再学进来？\n\n{row.get('rule') or row.get('text')}"):
            ls.drop_and_block(kind, row["id"])
            self._learn_load()

    def _learn_delete(self):
        from rag import learn_store as ls
        sel = self._learn_tree.selection()
        if not sel:
            messagebox.showinfo("先选一条", "先在上面点一行。")
            return
        kind, row = self._learn_map.get(sel[0], (None, None))
        if kind and ls.delete_item(kind, row["id"]):
            self._learn_load()

    def _fact_accept(self):
        """采纳 → 进资料库（走手动新增那条路，可撤销）。"""
        from rag import kb_history, learn_store as ls, kb_tools
        sel = self._fact_tree.selection()
        if not sel:
            messagebox.showinfo("先选一条", "先在上面点一行。")
            return
        f = self._fact_map.get(sel[0])
        if not f or self.qdrant is None:
            return
        question = f.get("question") or ""
        claim = f.get("claim") or ""
        if not messagebox.askyesno(
                "采纳进资料库",
                f"客户问：{question}\n\n准备写进资料库的回答：\n{claim}\n\n"
                f"（数字和型号要自己核对清楚 —— 写错就是报错价）\n\n"
                f"采纳之后，下次同样的问题检索分就高了，不用再转人工。确认？"):
            return
        self._learn_info.config(text="正在写进资料库…", foreground=COLORS["warning"])
        self.win.update_idletasks()
        import threading

        def work():
            try:
                res = kb_tools.add_entry(self.qdrant, question, claim,
                                         source="人工改稿学到", collection=self.collection)
                kb_history.record(res["id"], "", "人工改稿学到", reason="学到的说法入库")
                ls.set_fact_status(f["id"], "accepted")
            except Exception as e:
                logger.exception("学到的说法入库失败")
                self.win.after(0, lambda: self._fact_failed(str(e)))
                return
            self.win.after(0, self._fact_done)

        # 嵌入是本机 CPU 跑的，第一次要加载模型（5~10 秒）—— 不能卡界面
        threading.Thread(target=work, daemon=True).start()

    def _fact_failed(self, why: str):
        messagebox.showerror("入库失败", why)
        self._learn_info.config(text=f"✗ 入库失败：{why}", foreground=COLORS["danger"])

    def _fact_done(self):
        self._learn_load()
        self._kb_load(0)
        self._learn_info.config(text="已写进资料库，立即生效（可在资料库页改/撤）",
                                foreground=COLORS["success"])

    def _fact_reject(self):
        from rag import learn_store as ls
        sel = self._fact_tree.selection()
        if not sel:
            return
        f = self._fact_map.get(sel[0])
        if f and ls.set_fact_status(f["id"], "rejected"):
            self._learn_load()

    # ── 档案栏（多资料库切换） ────────────────────────────────────────

    def _build_archive_bar(self):
        from rag import archives

        bar = ttk.LabelFrame(self.win, text="资料库档案（每个档案互不影响，切了立刻生效）")
        bar.pack(fill=tk.X, padx=10, pady=(10, 0))
        row = ttk.Frame(bar)
        row.pack(fill=tk.X, padx=8, pady=6)

        self._archives = archives.all_archives()
        self._arch_combo = ttk.Combobox(
            row, state="readonly", width=24,
            values=[a.get("name", a["id"]) for a in self._archives])
        active = archives.active_id()
        idx = next((i for i, a in enumerate(self._archives) if a["id"] == active), 0)
        self._arch_combo.current(idx)
        self._arch_combo.pack(side=tk.LEFT)
        self._arch_combo.bind("<<ComboboxSelected>>", self._on_archive_change)

        ttk.Button(row, text="新建", command=self._archive_new).pack(side=tk.LEFT, padx=(6, 2))
        ttk.Button(row, text="重命名", command=self._archive_rename).pack(side=tk.LEFT, padx=2)
        ttk.Button(row, text="复制档案", command=self._archive_copy).pack(side=tk.LEFT, padx=2)
        ttk.Button(row, text="删除", command=self._archive_delete).pack(side=tk.LEFT, padx=2)
        self._arch_info = ttk.Label(row, text="", foreground=COLORS["ink_mute"])
        self._arch_info.pack(side=tk.LEFT, padx=10)
        self._arch_tip()

    def _arch_tip(self):
        from rag import archives
        n = len(self._archives)
        self._arch_info.config(text=f"共 {n} 个档案｜当前：{archives.active_name()}")
        self.win.title(f"知识库 —— {archives.active_name()}")

    def _archive_index(self) -> int:
        return max(0, self._arch_combo.current())

    def _on_archive_change(self, _evt=None):
        """切换档案：改清单里的 active，然后把四页全部按新档案刷一遍。"""
        from rag import archives
        a = self._archives[self._archive_index()]
        if not archives.set_active(a["id"]):
            return
        self._arch_tip()
        self._status.config(text=f"已切换到「{a['name']}」—— 机器人下一条回复就按这个库",
                            foreground=COLORS["success"])
        self._refresh_all()

    def _refresh_all(self):
        """切档案后要刷新的东西：提示词、资料列表、试问一句的信息、学到的。"""
        try:
            ps.ensure_files()
            self._load(self._which.get())
        except Exception:
            pass
        try:
            self._kb_load(0)
        except Exception:
            pass
        try:
            self._learn_load()
        except Exception:
            pass
        if hasattr(self, "_probe_info"):
            self._probe_info.config(text="（换了档案，重新试问一句看看）")

    def _reload_archives(self):
        from rag import archives
        self._archives = archives.all_archives()
        self._arch_combo.config(values=[a.get("name", a["id"]) for a in self._archives])
        aid = archives.active_id()
        self._arch_combo.current(
            next((i for i, a in enumerate(self._archives) if a["id"] == aid), 0))
        self._arch_tip()

    def _archive_new(self):
        """新建空档案。提示词和语气样本从当前档案拷一份 —— 说话方式沿用，事实从零开始。"""
        from tkinter import simpledialog
        from rag import archives
        name = simpledialog.askstring("新建资料库档案",
                                      "给这个资料库起个名字（比如「服装租赁」）：",
                                      parent=self.win)
        if not name:
            return
        src = archives.active_id()
        try:
            entry = archives.create(name)
            archives.copy_dirs(src, entry["id"], kinds=("prompts", "learned"))
        except Exception as e:
            messagebox.showerror("新建失败", str(e))
            return
        self._reload_archives()
        self._status.config(
            text=f"已新建「{entry['name']}」：空的资料库，提示词从「{archives.get(src)['name']}」拷了一份",
            foreground=COLORS["success"])
        if messagebox.askyesno("切过去？", f"现在切到「{entry['name']}」开始加资料？"):
            self._arch_combo.current(len(self._archives) - 1)
            self._on_archive_change()

    def _archive_rename(self):
        from tkinter import simpledialog
        from rag import archives
        a = self._archives[self._archive_index()]
        name = simpledialog.askstring("重命名", "新的名字：", initialvalue=a.get("name", ""),
                                      parent=self.win)
        if not name:
            return
        archives.rename(a["id"], name)
        self._reload_archives()
        self._status.config(text=f"已改名为「{name}」", foreground=COLORS["success"])

    def _archive_copy(self):
        """整份复制（条目 + 源文件 + 提示词 + 学到的语气）—— 拿来做变体最省事。"""
        from tkinter import simpledialog
        from rag import archives, kb_tools
        src = self._archives[self._archive_index()]
        name = simpledialog.askstring(
            "复制档案", f"把「{src['name']}」整份复制成（填新名字）：", parent=self.win)
        if not name:
            return
        if self.qdrant is None:
            messagebox.showerror("复制不了", "拿不到资料库连接")
            return
        self._status.config(text="正在复制…（条目多的话要几秒）", foreground=COLORS["warning"])
        self.win.update_idletasks()
        try:
            entry = archives.create(name, note=f"从「{src['name']}」复制")
            archives.copy_dirs(src["id"], entry["id"],
                               kinds=("uploads", "prompts", "learned"))
            n = kb_tools.copy_collection(self.qdrant,
                                         archives.collection_of(src["id"]),
                                         entry["collection"])
        except Exception as e:
            messagebox.showerror("复制失败", str(e))
            return
        self._reload_archives()
        self._status.config(text=f"已复制成「{entry['name']}」：{n} 条资料", foreground=COLORS["success"])

    def _archive_delete(self):
        from rag import archives
        a = self._archives[self._archive_index()]
        if a["id"] == archives.active_id():
            messagebox.showinfo("先切档案",
                                f"「{a['name']}」是当前正在用的档案。\n"
                                f"先切到别的档案，再回来删它 —— 免得删完程序没库可用。")
            return
        if not messagebox.askyesno(
                "删除档案",
                f"删掉「{a['name']}」？\n\n"
                f"它的资料条目会被删除，源文件/提示词/历史会挪到回收目录\n"
                f"（{archives.trash_dir()}）而不是真删。\n\n确认？"):
            return
        try:
            if self.qdrant is not None:
                try:
                    self.qdrant.delete_collection(archives.collection_of(a["id"]))
                except Exception as e:
                    logger.warning(f"删 collection 失败（可能本来就没有）: {e}")
            archives.delete(a["id"])
        except Exception as e:
            messagebox.showerror("删除失败", str(e))
            return
        self._reload_archives()
        self._status.config(text=f"已删除「{a['name']}」（源文件在回收目录里）",
                            foreground=COLORS["success"])

    # ── 资料库页 ──────────────────────────────────────────────────────

    def _build_kb_page(self, page):
        # ── 上传区 ────────────────────────────────────────────────
        up = ttk.LabelFrame(page, text="上传资料（txt / md / csv / Excel / Word）")
        up.pack(fill=tk.X, padx=10, pady=(10, 4))
        r1 = ttk.Frame(up)
        r1.pack(fill=tk.X, padx=8, pady=6)
        ttk.Button(r1, text="选择文件…", command=self._kb_pick_files).pack(side=tk.LEFT)
        # AI 整理：陈述句/表格 → 问答。整理结果先预览，勾选后才写入。
        ttk.Button(r1, text="用 AI 整理成问答…",
                   command=self._kb_ai_ingest).pack(side=tk.LEFT, padx=6)
        ttk.Button(r1, text="重建索引", command=self._kb_rebuild).pack(side=tk.LEFT)
        self._kb_up_status = ttk.Label(r1, text="", foreground=COLORS["ink_mute"])
        self._kb_up_status.pack(side=tk.LEFT, padx=10)

        top = ttk.Frame(page)
        top.pack(fill=tk.X, padx=10, pady=(4, 4))
        ttk.Label(top, text="搜关键词：").pack(side=tk.LEFT)
        self._kb_kw = ttk.Entry(top, width=24)
        self._kb_kw.pack(side=tk.LEFT, padx=4)
        self._kb_kw.bind("<Return>", lambda e: self._kb_load(0))
        ttk.Button(top, text="搜索", command=lambda: self._kb_load(0)).pack(side=tk.LEFT)
        ttk.Button(top, text="刷新", command=lambda: self._kb_load(0)).pack(side=tk.LEFT, padx=4)
        self._kb_page = 0
        ttk.Button(top, text="← 上一页",
                   command=lambda: self._kb_load(self._kb_page - 1)).pack(side=tk.LEFT, padx=(12, 2))
        ttk.Button(top, text="下一页 →",
                   command=lambda: self._kb_load(self._kb_page + 1)).pack(side=tk.LEFT, padx=2)
        self._kb_info = ttk.Label(top, text="", foreground=COLORS["ink_mute"])
        self._kb_info.pack(side=tk.LEFT, padx=10)

        # 单条操作：手动改资料全靠这一排
        ops = ttk.Frame(page)
        ops.pack(fill=tk.X, padx=10, pady=(0, 2))
        ttk.Button(ops, text="新增一条…", command=self._kb_add).pack(side=tk.LEFT)
        ttk.Button(ops, text="编辑这条…", command=self._kb_edit).pack(side=tk.LEFT, padx=6)
        ttk.Button(ops, text="看全文", command=self._kb_view).pack(side=tk.LEFT, padx=6)
        ttk.Button(ops, text="撤销上次修改", command=self._kb_undo).pack(side=tk.LEFT, padx=6)
        ttk.Button(ops, text="删除这条", command=self._kb_delete).pack(side=tk.LEFT, padx=6)
        # ★ 最常转人工的问题 = 资料库缺什么。放这里顺手就能补。
        ttk.Button(ops, text="最常转人工的问题…",
                   command=self._open_unanswered).pack(side=tk.LEFT, padx=(18, 6))
        ttk.Label(ops, text="（双击一行＝看全文）", foreground=COLORS["ink_mute"]).pack(side=tk.LEFT, padx=8)

        # 问题和答案**必须分两列**：挤在一列里 Treeview 只显示第一行，
        # 用户看到满屏"客户问题"，会以为答案根本没导进来。
        cols = ("no", "question", "answer")
        self._kb_tree = ttk.Treeview(page, columns=cols, show="headings", height=15)
        for c, t, w in (("no", "#", 40), ("question", "客户问题", 330),
                        ("answer", "销售回答", 420)):
            self._kb_tree.heading(c, text=t)
            self._kb_tree.column(c, width=w, anchor="w")
        self._kb_tree.tag_configure("plain", foreground="#555")
        self._kb_tree.bind("<Double-1>", lambda e: self._kb_view())
        sb = ttk.Scrollbar(page, orient=tk.VERTICAL, command=self._kb_tree.yview)
        self._kb_tree.configure(yscrollcommand=sb.set)
        self._kb_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0), pady=4)
        sb.pack(side=tk.LEFT, fill=tk.Y, pady=4, padx=(0, 10))
        self._kb_load(0)

    # ── 单条操作 ──────────────────────────────────────────────────────

    def _kb_selected(self) -> dict:
        """当前选中的条目（完整数据，不是表格里那截）；没选中就弹提示。"""
        sel = self._kb_tree.selection()
        if not sel:
            messagebox.showinfo("先选一条", "先在上面点一行，再点这个按钮。")
            return {}
        got = self._kb_rows.get(sel[0])
        if not got:
            messagebox.showinfo("刷新一下", "列表变过了，点「刷新」再试。")
            return {}
        return got

    def _kb_view(self):
        """看全文 —— 列表里是截断的，长答案得开窗看。"""
        e = self._kb_selected()
        if not e:
            return
        w = tk.Toplevel(self.win)
        w.title("资料全文")
        w.geometry("760x520")
        w.transient(self.win)
        t = tk.Text(w, wrap="word", font=font("small"))
        t.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        t.insert("1.0", e["text"] if not e["faq"]
                 else f"客户问题：\n{e['question']}\n\n销售回答：\n{e['answer']}")
        t.configure(state="disabled")
        ttk.Label(w, text=f"来源：{e['source'] or '(无)'}    id：{e['id']}",
                  foreground=COLORS["ink_mute"]).pack(anchor="w", padx=10, pady=(0, 8))

    def _kb_edit(self):
        e = self._kb_selected()
        if not e:
            return
        if self.qdrant is None:
            messagebox.showerror("改不了", "拿不到资料库连接（主程序没起来？）")
            return
        from gui.kb_edit import open_editor
        open_editor(self.win, e, self.qdrant, self.collection,
                    on_saved=self._after_edit, cfg=self.cfg)

    def _kb_add(self):
        """新增一条。不用回去改 Excel 再重传。"""
        if self.qdrant is None:
            messagebox.showerror("加不了", "拿不到资料库连接（主程序没起来？）")
            return
        from gui.kb_edit import open_editor
        open_editor(self.win, None, self.qdrant, self.collection,
                    on_saved=self._after_add, cfg=self.cfg)

    def _open_unanswered(self):
        """最常转人工的问题 = 资料库缺什么。补一条能少转很多次人工。"""
        from gui.unanswered_dialog import open_unanswered
        open_unanswered(self.win, self.qdrant, self.collection, self.cfg)

    def _after_add(self, res):
        self._kb_load(self._kb_page)
        self._kb_up_status.config(
            text=f"已新增 1 条（{res.get('chunks', 0)} 块入库），立即生效",
            foreground=COLORS["success"])

    def _after_edit(self, res):
        self._kb_load(self._kb_page)
        self._kb_up_status.config(
            text=f"已改 1 条（{res.get('chunks', 0)} 块重新入库），立即生效",
            foreground=COLORS["success"])

    def _kb_undo(self):
        from rag import kb_history
        e = self._kb_selected()
        if not e or self.qdrant is None:
            return
        from rag.kb_tools import get_entry
        if not kb_history.versions(e["id"]):
            messagebox.showinfo("没有可撤销的",
                                "这条没有改过的记录（可能是刚加的，或者已经退到底了）。")
            return
        if not messagebox.askyesno("撤销上次修改",
                                   f"把这条退回上一版？\n\n现在：{e['text'][:120]}…"):
            return
        self._kb_up_status.config(text="正在退回…", foreground=COLORS["warning"])
        self.win.update_idletasks()
        try:
            res = kb_history.undo(self.qdrant, e["id"], self.collection)
        except Exception as ex:
            messagebox.showerror("撤销失败", str(ex))
            self._kb_up_status.config(text=f"✗ 撤销失败：{ex}", foreground=COLORS["danger"])
            return
        self._kb_load(self._kb_page)
        self._kb_up_status.config(
            text=f"已退回 {res.get('restored_at', '')} 那一版", foreground=COLORS["success"])

    def _kb_delete(self):
        from rag import kb_history
        e = self._kb_selected()
        if not e or self.qdrant is None:
            return
        if not messagebox.askyesno(
                "删除这条",
                f"删掉这条资料？机器人以后就查不到它了。\n\n{e['text'][:150]}…\n\n"
                f"（会留一份历史，改错了可以拿回来）"):
            return
        from rag.kb_tools import delete_entry
        if not delete_entry(self.qdrant, e["id"], self.collection):
            messagebox.showerror("删除失败", "删不掉，看看日志。")
            return
        kb_history.record(e["id"], e["text"], e["source"], reason="删除")
        self._kb_load(self._kb_page)
        self._kb_up_status.config(text="已删除 1 条（历史里留了一份）", foreground=COLORS["success"])

    # ── 上传 / 重建索引 ───────────────────────────────────────────────

    def _kb_pick_files(self):
        from tkinter import filedialog
        paths = filedialog.askopenfilenames(
            title="选择要导入的资料",
            filetypes=[("表格与文档", "*.xlsx *.xlsm *.xls *.docx"),
                       ("文本资料", "*.txt *.md *.csv"),
                       ("所有文件", "*.*")])
        if paths:
            self._kb_import(list(paths))

    def _kb_ai_ingest(self):
        """用 AI 把一份资料整理成问答 —— 先预览、人工勾选、再写入。

        和「选择文件…」的区别：那条路是**原文照搬进库**；
        这条是让 LLM 把陈述句/表格拆成多条"客户会怎么问"的问答。
        为什么要拆：问答型 chunk 只嵌入"问题"部分，问法直接决定召回
        （实测同一段内容，问句一字不差时问答式 0.941 / 陈述句 0.724）。
        """
        if self.qdrant is None:
            self._kb_up_status.config(text="拿不到资料库连接", foreground=COLORS["danger"])
            return
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="选一份资料，让 AI 整理成问答（一次一个文件）",
            filetypes=[("表格与文档", "*.xlsx *.xlsm *.xls *.docx"),
                       ("文本资料", "*.txt *.md *.csv"),
                       ("所有文件", "*.*")])
        if not path:
            return
        from gui.llm_ingest_dialog import open_ingest
        open_ingest(self.win, [path], qdrant=self.qdrant, collection=self.collection,
                    on_done=lambda n: (self._kb_load(0),
                                       self._kb_up_status.config(
                                           text="AI 整理写入 %d 条" % n,
                                           foreground=COLORS["success"])))

    def _kb_import(self, paths):
        """导入跑在**后台线程**：嵌入要加载模型、几十块可能十几秒，不能卡界面。"""
        if self.qdrant is None:
            self._kb_up_status.config(text="拿不到资料库连接", foreground=COLORS["danger"])
            return
        import threading
        from pipeline.upload import import_files
        self._kb_up_status.config(text=f"正在导入 {len(paths)} 个文件…",
                                  foreground=COLORS["warning"])

        def work():
            def prog(msg):
                self.win.after(0, lambda m=msg: self._kb_up_status.config(
                    text=m, foreground=COLORS["warning"]))
            try:
                res = import_files(paths, self.qdrant, self.collection, progress=prog)
            except Exception as e:
                self.win.after(0, lambda: self._kb_up_status.config(
                    text=f"导入出错: {e}", foreground=COLORS["danger"]))
                return

            def done():
                from pipeline.upload import list_uploaded
                parts = [f"成功 {len(res['ok'])} 个（共 {res['chunks']} 块）"]
                overwritten = [o for o in res["ok"] if o["replaced"]]
                if overwritten:
                    parts.append(f"其中 {len(overwritten)} 个覆盖了旧版本")
                if res["failed"]:
                    parts.append(f"失败 {len(res['failed'])} 个")
                parts.append(f"源目录现有 {len(list_uploaded())} 个文件")
                self._kb_up_status.config(
                    text="导入完成：" + "，".join(parts),
                    foreground=COLORS["success"] if not res["failed"] else COLORS["warning"])
                if res["failed"]:
                    messagebox.showwarning(
                        "部分文件失败",
                        "\n".join(f"{f['file']}: {f['reason']}" for f in res["failed"]))
                self._kb_load(0)
            self.win.after(0, done)

        threading.Thread(target=work, daemon=True).start()

    def _kb_rebuild(self):
        """重建索引：清空 Qdrant 后把 data/chat_raw 下的**所有源**重新嵌入。

        ⚠️ 上传的源文件是**原文件原样**备份的（pipeline.upload.replace_source），
        所以重建时表格还会按行切、结构不丢 —— 这正是"源必须留一份"的原因。
        """
        from pipeline.upload import import_files, source_files
        files = source_files()
        if not files:
            self._kb_up_status.config(text="data/chat_raw 下没有源文件", foreground=COLORS["danger"])
            return
        if not messagebox.askyesno(
                "重建索引",
                f"将清空现有索引，然后用 {len(files)} 个源文件重新生成。\n"
                f"（源文件不受影响；嵌入较慢，请耐心等）\n\n继续？"):
            return
        import threading
        self._kb_up_status.config(text=f"重建中…（{len(files)} 个源文件）",
                                  foreground=COLORS["warning"])

        def work():
            try:
                from rag.retriever import ensure_collection
                self.qdrant.delete_collection(self.collection)
                ensure_collection(self.qdrant, self.collection)
            except Exception as e:
                self.win.after(0, lambda: self._kb_up_status.config(
                    text=f"清空索引失败: {e}", foreground=COLORS["danger"]))
                return
            res = import_files([str(f) for f in files], self.qdrant, self.collection,
                               progress=lambda m: self.win.after(
                                   0, lambda mm=m: self._kb_up_status.config(text=mm)))
            self.win.after(0, lambda: (
                self._kb_up_status.config(
                    text=f"重建完成：{len(res['ok'])} 个源、{res['chunks']} 块",
                    foreground=COLORS["success"]),
                self._kb_load(0)))

        threading.Thread(target=work, daemon=True).start()

    def _kb_load(self, page: int):
        from rag import kb_tools
        if self.qdrant is None:
            self._kb_info.config(text="拿不到资料库连接（主程序未启动？）")
            return
        page = max(0, int(page))
        size = 100
        kw = self._kb_kw.get().strip()
        rows = kb_tools.list_entries(self.qdrant, self.collection,
                                     offset=page * size, limit=size, keyword=kw)
        # ★ 翻过界时（点「下一页」越过最后一页）**不要显示空白页** ——
        #   空白页看起来就像"翻页坏了"（店主反馈过"上一页下一页是假的"）。
        #   停回上一页并明说。带关键词过滤时不回退：那一页空可能只是都被滤掉了。
        over = False
        if not rows and page > 0 and not kw:
            page -= 1
            over = True
            rows = kb_tools.list_entries(self.qdrant, self.collection,
                                         offset=page * size, limit=size)
        self._kb_page = page
        for i in self._kb_tree.get_children():
            self._kb_tree.delete(i)
        # 行 key → 完整条目。表格里是截断的，编辑/看全文要用完整数据
        self._kb_rows = {}
        for n, r in enumerate(rows, 1):
            if r["faq"]:
                q, a, tag = r["question"][:120], r["answer"][:200], ()
            else:
                # 非问答格式的整段资料，标一下，免得以为"答案怎么没了"
                q, a, tag = "[整段资料] " + r["text"][:110], "", ("plain",)
            key = self._kb_tree.insert("", tk.END, tags=tag,
                                       values=(page * size + n, q, a))
            self._kb_rows[key] = r
        total = kb_tools.count(self.qdrant, self.collection)
        self._kb_info.config(
            text=f"共 {total} 条 | 第 {page + 1} 页显示 {len(rows)} 条"
                 + ("（已按关键词过滤本页）" if kw else "")
                 + ("　← 已经是最后一页了" if over else ""))

    # ── 试问一句页 ────────────────────────────────────────────────────

    def _build_probe_page(self, page):
        top = ttk.Frame(page)
        top.pack(fill=tk.X, padx=10, pady=(10, 4))
        ttk.Label(top, text="客户问题：").pack(side=tk.LEFT)
        self._probe_q = ttk.Entry(top, width=48)
        self._probe_q.pack(side=tk.LEFT, padx=4)
        self._probe_q.bind("<Return>", lambda e: self._probe_run())
        ttk.Button(top, text="试问", command=self._probe_run).pack(side=tk.LEFT)
        self._probe_info = ttk.Label(top, text="", foreground=COLORS["ink_mute"])
        self._probe_info.pack(side=tk.LEFT, padx=10)

        ttk.Label(page, text="下面就是线上会检索到的内容和分数。分数高于门槛才会直接回客户，"
                            "否则转人工。答案也要看 —— 检索对了但资料本身答错，照样发错。",
                  foreground=COLORS["warning"]).pack(anchor="w", padx=12)

        cols = ("rank", "score", "verdict", "question", "answer")
        self._probe_tree = ttk.Treeview(page, columns=cols, show="headings", height=16)
        for c, t, w in (("rank", "#", 35), ("score", "分数", 55),
                        ("verdict", "会不会直发", 170), ("question", "检索到的客户问题", 250),
                        ("answer", "对应的销售回答", 330)):
            self._probe_tree.heading(c, text=t)
            self._probe_tree.column(c, width=w, anchor="w")
        self._probe_tree.tag_configure("ok", foreground=COLORS["success"])
        self._probe_tree.tag_configure("low", foreground=COLORS["danger"])
        self._probe_tree.tag_configure("plain", foreground="#555")
        sb = ttk.Scrollbar(page, orient=tk.VERTICAL, command=self._probe_tree.yview)
        self._probe_tree.configure(yscrollcommand=sb.set)
        self._probe_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0), pady=4)
        sb.pack(side=tk.LEFT, fill=tk.Y, pady=4, padx=(0, 10))

    def _probe_run(self):
        """真的走一遍线上检索链路。**要加载嵌入模型，第一次约 5~10 秒**。"""
        from rag import kb_tools
        from rag.guard import high_threshold
        q = self._probe_q.get().strip()
        if not q or self.qdrant is None:
            return
        self._probe_info.config(text="检索中…（第一次要加载模型，约 5~10 秒）")
        self.win.update_idletasks()
        thr = high_threshold(self.cfg)
        hits = kb_tools.probe(self.qdrant, q, top_k=5, collection=self.collection)
        for i in self._probe_tree.get_children():
            self._probe_tree.delete(i)
        if not hits:
            self._probe_info.config(text="没检索到任何内容（资料库是空的？）")
            return
        for n, h in enumerate(hits, 1):
            tags = ["ok" if h["score"] > thr else "low"]
            if h["faq"]:
                qq, aa = h["question"][:120], h["answer"][:300]
            else:
                tags.append("plain")
                qq, aa = "[整段资料] " + h["text"][:110], ""
            self._probe_tree.insert("", tk.END, tags=tuple(tags),
                                    values=(n, f"{h['score']:.3f}",
                                            kb_tools.verdict(h["score"], thr), qq, aa))
        top = hits[0]
        shown = top["question"] if top["faq"] else top["text"][:40]
        self._probe_info.config(
            text=f"最高分 {top['score']:.3f}｜直发门槛 {thr:.2f}"
                 f"｜{'会直发' if top['score'] > thr else '会转人工'}"
                 f"｜命中的是「{shown[:20]}」")

    def _build_prompt_page(self, page):
        top = ttk.Frame(page)
        top.pack(fill=tk.X, padx=10, pady=(10, 4))
        ttk.Label(top, text="选择要编辑的提示词：").pack(side=tk.LEFT)
        self._which = tk.StringVar(value="system")
        self._combo = ttk.Combobox(top, state="readonly", width=14,
                                   values=[f"{label}" for _k, label in TABS])
        self._combo.current(0)
        self._combo.pack(side=tk.LEFT, padx=6)
        self._combo.bind("<<ComboboxSelected>>", self._on_switch)
        self._hint = ttk.Label(top, text="", foreground=COLORS["ink_mute"])
        self._hint.pack(side=tk.LEFT, padx=10)

        tip = ("提示：`{context}`、`{conversation_history}`、`{tone_samples}` 是程序填内容的"
               "占位符，别改名。写成别的花括号（比如 `{contex}`）保存时会被拦住 —— "
               "存下去会让机器人变成「全部转人工」。")
        ttk.Label(page, text=tip, foreground=COLORS["warning"],
                  wraplength=600, justify="left").pack(anchor="w", padx=12)

        # 编辑区
        box = ttk.Frame(page)
        box.pack(fill=tk.BOTH, expand=True, padx=10, pady=6)
        self._text = tk.Text(box, wrap="word", font=font("small"), undo=True)
        sb = ttk.Scrollbar(box, orient=tk.VERTICAL, command=self._text.yview)
        self._text.configure(yscrollcommand=sb.set)
        self._text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        # 受保护区（只读）
        safe = ttk.LabelFrame(page, text="受保护区（只读 —— 这些规则改了会答错，不参与编辑）")
        safe.pack(fill=tk.X, padx=10, pady=(0, 10))
        t = tk.Text(safe, height=7, wrap="word", foreground=COLORS["ink_mute"],
                    background="#f5f5f5", relief="flat")
        t.insert("1.0", ps.SAFETY_RULES)
        t.configure(state="disabled")
        t.pack(fill=tk.X, padx=6, pady=6)

        self._load("system")

    # ── 读写 ──────────────────────────────────────────────────────────

    def _on_switch(self, _evt=None):
        key = TABS[self._combo.current()][0]
        self._load(key)

    def _load(self, key: str):
        self._which.set(key)
        self._text.delete("1.0", tk.END)
        self._text.insert("1.0", ps.get(key))
        label = dict(TABS)[key]
        custom = "（已自定义）" if ps.is_customized(key) else "（出厂默认）"
        self._hint.config(text=f"{label} {custom}")
        self._status.config(text="")

    def _save(self) -> bool:
        key = self._which.get()
        text = self._text.get("1.0", tk.END).rstrip("\n") + "\n"
        ok, why = ps.set_prompt(key, text)
        if not ok:
            messagebox.showerror("保存失败", why)
            self._status.config(text="✗ " + why[:60], foreground=COLORS["danger"])
            return False
        self._status.config(text=f"✓ 已保存（{dict(TABS)[key]}），立即生效",
                            foreground=COLORS["success"])
        self._load(key)
        if self.on_saved:
            try:
                self.on_saved(key)
            except Exception:
                pass
        return True

    def _reset(self):
        key = self._which.get()
        if not ps.is_customized(key):
            self._status.config(text="这一项已经是出厂默认了", foreground=COLORS["ink_mute"])
            return
        if not messagebox.askyesno("恢复默认",
                                   f"把「{dict(TABS)[key]}」恢复成出厂默认？"
                                   f"你改过的内容会被删除。"):
            return
        ps.reset(key)
        self._load(key)
        self._status.config(text="✓ 已恢复出厂默认", foreground=COLORS["success"])
