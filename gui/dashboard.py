# gui/dashboard.py
"""运营看板（独立窗口）—— docs/看板-PRD.md 的实现。

给谁看：**店主本人**。所以它不是 BI，而是"日报 + 待办清单"：
先看今天机器人替我做了什么，再看**我该补哪条资料**（双击就能补），
最后看它有没有乱来（护栏拦下了什么）。

时间范围：今日 / 近 7 天 / 自定义（日历选起止，粒度按天数自适应）。
数据全部来自本机落盘（只读），不上传任何东西。
"""

from __future__ import annotations

import calendar
import csv
import tkinter as tk
from datetime import date, datetime, timedelta
from tkinter import filedialog, messagebox, ttk

from stats import aggregate as agg
from gui.theme import (COLORS, SPACE, apply_theme, badge, bar_row, card,
                       card_body, divider, font, hint, kpi_card, section_title)

# 护栏分组：这些是"我拦下了什么"，和"正常直答"分开显示
GUARD_PATHS = ("越权承诺", "拖延话术", "议价加码", "议价特批", "比价跟价", "押金特批",
               "费用特批", "政策未覆盖", "订单与发票查询", "订单变更", "退款与投诉",
               "库存与档期", "指代不明", "店主私事", "越权操作", "要人工",
               "非本店业务", "时间承诺", "越界婉拒", "模型要人工", "置信度不足")
NEUTRAL_PATHS = ("正常直答", "闲聊", "无信息量/收尾", "人工发出", "欢迎语", "本地直答",
                 "其他")


def _fmt_seconds(v: float | None) -> str:
    if v is None:
        return "—"
    if v < 10:
        return f"{v:.1f}"
    if v < 60:
        return f"{v:.0f}"
    return f"{v / 60:.1f}"


def _short_reason(reason: str) -> str:
    """把"置信度不足(0.48)"截成"置信度不足" —— 表格里括号里的分数挤没了正文。"""
    r = (reason or "").strip()
    if not r:
        return "—"
    for sep in ("(", "（"):
        if sep in r:
            r = r.split(sep)[0]
    return r.strip() or "—"


class DashboardWindow:
    """看板窗口。用法：``DashboardWindow(parent, qdrant=..., collection=..., cfg=...)``"""

    def __init__(self, parent: tk.Misc, *, qdrant=None, collection: str | None = None,
                 cfg: dict | None = None, on_close=None):
        self.qdrant = qdrant
        self.collection = collection
        self.cfg = cfg or {}
        self.on_close = on_close
        self._range = "today"          # today | week | custom
        self._start = date.today()
        self._end = date.today()
        self._stats: agg.Stats | None = None

        self.win = tk.Toplevel(parent)
        self.win.title("运营看板")
        self.win.configure(background=COLORS["canvas_soft"])
        self.win.geometry("1220x860")
        self.win.minsize(1040, 680)
        self.win.protocol("WM_DELETE_WINDOW", self._close)
        apply_theme(self.win)

        self._build()
        self.refresh()

    # ── 界面骨架 ───────────────────────────────────────────────────────

    def _build(self):
        self._build_topbar()
        # ★ 内容区可滚动：窗口再小也不会把"踩刹车"那块切掉
        #   （第一版直接把底部挤出可视区，截图才发现 —— 看板绝不能藏信息）
        shell = tk.Frame(self.win, background=COLORS["canvas_soft"])
        shell.pack(fill="both", expand=True)
        self._canvas = tk.Canvas(shell, background=COLORS["canvas_soft"],
                                 highlightthickness=0, bd=0)
        vsb = ttk.Scrollbar(shell, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)
        body = tk.Frame(self._canvas, background=COLORS["canvas_soft"])
        self._body_id = self._canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda e: self._canvas.configure(
            scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>", lambda e: self._canvas.itemconfigure(
            self._body_id, width=e.width))
        self.win.bind("<MouseWheel>",
                      lambda e: self._canvas.yview_scroll(int(-e.delta / 120), "units"))
        self._body = body

        pad = tk.Frame(body, background=COLORS["canvas_soft"])
        pad.pack(fill="both", expand=True, padx=SPACE["lg"], pady=SPACE["lg"])
        self._build_kpis(pad)
        self._build_trend(pad)
        self._build_two_panels(pad)
        self._build_guards(pad)
        hint(pad, "口径：接待＝机器人发出的回复条数（以回复为单位）；"
                  "自动解决率＝真答 ÷（真答＋转人工占位语）；中位响应含占位语。"
                  "数据只在本机，不上传。").pack(anchor="w", pady=(SPACE["sm"], 0))

    def _build_topbar(self):
        bar = tk.Frame(self.win, background=COLORS["canvas"])
        bar.pack(fill="x")
        inner = tk.Frame(bar, background=COLORS["canvas"])
        inner.pack(fill="x", padx=SPACE["lg"], pady=SPACE["md"])

        tk.Label(inner, text="运营看板", font=font("h1", True),
                 background=COLORS["canvas"], foreground=COLORS["ink"]).pack(side="left")

        # 分段控件（今日 / 近 7 天 / 自定义）—— 选中态是靛蓝浅底
        self._seg = {}
        seg = tk.Frame(inner, background=COLORS["canvas"])
        seg.pack(side="left", padx=(SPACE["xl"], 0))
        for key, text in (("today", "今日"), ("week", "近 7 天"), ("custom", "自定义 ▾")):
            b = ttk.Button(seg, text=text, style="Segment.TButton",
                           command=lambda k=key: self._pick_range(k))
            b.pack(side="left", padx=(0, SPACE["xs"]))
            self._seg[key] = b

        self._range_label = tk.Label(inner, text="", font=font("small"),
                                     background=COLORS["canvas"],
                                     foreground=COLORS["ink_mute"])
        self._range_label.pack(side="left", padx=(SPACE["md"], 0))

        ttk.Button(inner, text="导出 CSV", style="Secondary.TButton",
                   command=self._export).pack(side="right")
        ttk.Button(inner, text="刷新 ↻", style="Ghost.TButton",
                   command=self.refresh).pack(side="right", padx=(0, SPACE["sm"]))
        tk.Frame(self.win, height=1, background=COLORS["hairline"]).pack(fill="x")

    def _build_kpis(self, parent):
        row = tk.Frame(parent, background=COLORS["canvas_soft"])
        row.pack(fill="x")
        self._kpi_host = row
        self._kpi_cards: list[tk.Frame] = []
        for _ in range(4):
            c = card(row)
            c.pack(side="left", fill="both", expand=True,
                   padx=(0, SPACE["md"]) if len(self._kpi_cards) < 3 else 0)
            self._kpi_cards.append(c)

    def _build_trend(self, parent):
        c = card(parent)
        c.pack(fill="x", pady=(SPACE["lg"], SPACE["md"]))
        body = card_body(c)
        head = tk.Frame(body, background=COLORS["canvas"])
        head.pack(fill="x", padx=SPACE["lg"], pady=(SPACE["lg"], SPACE["sm"]))
        section_title(head, "接待量与自动解决率").pack(side="left")
        self._trend_note = hint(head, "")
        self._trend_note.pack(side="right")
        self._chart = tk.Canvas(body, height=132, background=COLORS["canvas"],
                                highlightthickness=0, bd=0)
        self._chart.pack(fill="x", padx=SPACE["lg"], pady=(0, SPACE["lg"]))
        self._chart.bind("<Configure>", lambda e: self._draw_chart())

    def _build_two_panels(self, parent):
        row = tk.Frame(parent, background=COLORS["canvas_soft"], height=330)
        # ★ 这两块**不参与纵向拉伸**：否则它们会把下面的"踩刹车"卡片挤出可视区
        #   （第一版就踩了：截图里底部那块直接看不见）
        row.pack(fill="x", pady=(0, SPACE["md"]))
        row.pack_propagate(False)

        # 左：客户在问什么
        left = card(row)
        left.pack(side="left", fill="both", expand=True, padx=(0, SPACE["md"]))
        lb = card_body(left)
        section_title(lb, "客户在问什么（Top 10）").pack(
            anchor="w", padx=SPACE["lg"], pady=(SPACE["lg"], SPACE["xs"]))
        hint(lb, "按「命中了哪份资料 / 走的哪条路」归类；点一行看客户原话").pack(
            anchor="w", padx=SPACE["lg"], pady=(0, SPACE["sm"]))
        self._dist_host = tk.Frame(lb, background=COLORS["canvas"])
        self._dist_host.pack(fill="both", expand=True, padx=SPACE["lg"],
                             pady=(0, SPACE["sm"]))
        # 原话样例框：**有数据时才显示**（空着占一大块灰，很丑）
        self._samples_host = tk.Frame(lb, background=COLORS["canvas"])
        self._samples_host.pack(fill="x", padx=SPACE["lg"], pady=(0, SPACE["lg"]))
        self._samples = tk.Text(self._samples_host, height=4, wrap="word",
                                relief="flat", background=COLORS["canvas_soft"],
                                foreground=COLORS["ink_secondary"],
                                font=font("small"), padx=SPACE["sm"], pady=SPACE["sm"],
                                highlightthickness=1,
                                highlightbackground=COLORS["hairline"])
        self._samples.pack(fill="x")
        self._samples.configure(state="disabled")
        self._samples_host.pack_forget()

        # 右：我该补什么（双击预填）—— 固定宽度，别让左边把"次数/原因"列挤没了
        right = card(row)
        right.pack(side="left", fill="both")
        rb = card_body(right)
        section_title(rb, "我该补什么（常被转人工的问题）").pack(
            anchor="w", padx=SPACE["lg"], pady=(SPACE["lg"], SPACE["xs"]))
        hint(rb, "双击一行 → 直接预填进「新增资料」").pack(
            anchor="w", padx=SPACE["lg"], pady=(0, SPACE["sm"]))
        wrap = tk.Frame(rb, background=COLORS["canvas"])
        wrap.pack(fill="both", expand=True, padx=SPACE["lg"], pady=(0, SPACE["lg"]))
        cols = ("q", "n", "why")
        self._tree = ttk.Treeview(wrap, columns=cols, show="headings", height=11)
        for cid, text, width, anchor in (("q", "客户原话", 168, "w"),
                                         ("n", "次数", 44, "center"),
                                         ("why", "转人工原因", 116, "w")):
            self._tree.heading(cid, text=text)
            self._tree.column(cid, width=width, minwidth=40, anchor=anchor,
                              stretch=(cid == "q"))
        self._tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self._tree.yview)
        sb.pack(side="right", fill="y")
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.bind("<Double-1>", lambda e: self._add_to_kb())
        self._tree.bind("<<TreeviewSelect>>", lambda e: self._show_reason())

    def _build_guards(self, parent):
        c = card(parent)
        c.pack(fill="x")
        body = card_body(c)
        head = tk.Frame(body, background=COLORS["canvas"])
        head.pack(fill="x", padx=SPACE["lg"], pady=(SPACE["lg"], SPACE["xs"]))
        section_title(head, "它替我踩了哪些刹车").pack(side="left")
        self._guard_note = hint(head, "")
        self._guard_note.pack(side="right")
        self._guard_host = tk.Frame(body, background=COLORS["canvas"])
        self._guard_host.pack(fill="x", padx=SPACE["lg"], pady=(0, SPACE["lg"]))

    # ── 时间范围 ───────────────────────────────────────────────────────

    def _pick_range(self, key: str):
        if key == "today":
            self._range, self._start, self._end = "today", date.today(), date.today()
        elif key == "week":
            self._range = "week"
            self._end = date.today()
            self._start = self._end - timedelta(days=6)
        else:
            picked = CalendarPopup(self.win, self._start, self._end).show()
            if not picked:
                return
            self._range = "custom"
            self._start, self._end = picked
        self.refresh()

    def _sync_segments(self):
        for key, btn in self._seg.items():
            btn.configure(style="SegmentOn.TButton" if key == self._range
                          else "Segment.TButton")
        days = (self._end - self._start).days + 1
        span = (self._start.strftime("%Y-%m-%d") if days == 1
                else f"{self._start.strftime('%Y-%m-%d')} ~ {self._end.strftime('%Y-%m-%d')}")
        self._range_label.configure(text=f"{span}（{days} 天）")

    # ── 刷新与绘制 ─────────────────────────────────────────────────────

    def refresh(self):
        self._sync_segments()
        self._stats = agg.aggregate(self._start, self._end)
        self._render_kpis()
        self._draw_chart()
        self._render_distribution()
        self._render_unanswered()
        self._render_guards()

    def _render_kpis(self):
        st = self._stats
        assert st is not None
        values = [
            ("接待", str(st.replies), "条",
             f"{st.customers} 位客户（含你人工回的）", "ink"),
            ("自动解决率", (f"{st.solve_rate * 100:.0f}" if st.solve_rate is not None else "—"),
             "%", f"真答 {st.auto} · 转人工 {st.hold}", "primary"),
            ("待人工", str(st.pending_now), "条", "实时积压，与时间范围无关", "warning"),
            ("中位响应", _fmt_seconds(st.median_reply_seconds), "秒",
             "含转人工占位语", "ink"),
        ]
        for card_widget, (label, value, unit, note, tone) in zip(self._kpi_cards, values):
            for child in card_body(card_widget).winfo_children():
                child.destroy()
            self._fill_kpi(card_body(card_widget), label, value, unit, note, tone)

    def _fill_kpi(self, body, label, value, unit, note, tone):
        tk.Label(body, text=label, font=font("small"), background=COLORS["canvas"],
                 foreground=COLORS["ink_mute"], anchor="w").pack(
            anchor="w", padx=SPACE["lg"], pady=(SPACE["md"], 0))
        row = tk.Frame(body, background=COLORS["canvas"])
        row.pack(anchor="w", padx=SPACE["lg"])
        # 空数据的占位符"—"不能用 KPI 大字号：26pt 的破折号看起来像一条横线
        placeholder = value in ("—", "-", "")
        tk.Label(row, text=value,
                 font=font("h2" if placeholder else "kpi", True),
                 background=COLORS["canvas"],
                 foreground=(COLORS["ink_faint"] if placeholder
                             else COLORS.get(tone, COLORS["ink"]))).pack(side="left")
        if unit and not placeholder:
            tk.Label(row, text=unit, font=font("small"), background=COLORS["canvas"],
                     foreground=COLORS["ink_mute"]).pack(side="left", padx=(3, 0),
                                                          pady=(9, 0))
        tk.Label(body, text=note, font=font("micro"), background=COLORS["canvas"],
                 foreground=COLORS["ink_faint"], anchor="w").pack(
            anchor="w", padx=SPACE["lg"], pady=(0, SPACE["md"]))

    def _draw_chart(self):
        cv = self._chart
        cv.delete("all")
        st = self._stats
        if st is None:
            return
        w = max(cv.winfo_width(), 400)
        h = int(cv.winfo_height() or 150)
        pad_l, pad_b, pad_t = 34, 22, 12
        n = len(st.buckets)
        if n == 0:
            return
        max_v = max((b.replies for b in st.buckets), default=0)
        if max_v == 0:
            # 空数据：别画一张全 0 的图，直接说清楚，并给一句"怎么办"
            cv.create_text(w // 2, h // 2 - 6, text="这段时间没有接待记录",
                           fill=COLORS["ink_mute"], font=font("body"))
            cv.create_text(w // 2, h // 2 + 14,
                           text="先点主界面的「开始」，或把时间范围调大一点看看",
                           fill=COLORS["ink_faint"], font=font("small"))
            self._trend_note.configure(text="")
            return
        max_v = max(max_v, 4)                      # 别让空数据把柱子画成顶格
        plot_w = w - pad_l - 12
        plot_h = h - pad_b - pad_t
        slot = plot_w / n
        bar_w = max(4, min(26, slot * 0.55))
        cv.create_line(pad_l, h - pad_b, w - 12, h - pad_b, fill=COLORS["hairline"])
        # 柱：接待量（靛蓝浅色）；线：自动解决率
        pts = []
        for i, b in enumerate(st.buckets):
            x = pad_l + slot * i + slot / 2
            bar_h = (b.replies / max_v) * plot_h
            if b.replies:
                cv.create_rectangle(x - bar_w / 2, h - pad_b - bar_h,
                                    x + bar_w / 2, h - pad_b,
                                    fill=COLORS["primary_subtle"], outline="")
                cv.create_text(x, h - pad_b - bar_h - 8, text=str(b.replies),
                               fill=COLORS["ink_mute"], font=font("micro"))
            if b.solve_rate is not None:
                y = h - pad_b - b.solve_rate * plot_h
                pts.extend([x, y])
            # 轴标签：太多就隔几个画一个
            step = max(1, n // 8)
            if i % step == 0 or i == n - 1:
                cv.create_text(x, h - pad_b + 10, text=b.label,
                               fill=COLORS["ink_faint"], font=font("micro"))
        if len(pts) >= 4:
            cv.create_line(*pts, fill=COLORS["primary"], width=2, smooth=True)
        else:
            # 点太少（比如只有一两天有数据）画不出线，改成点，不然看不见解决率
            for i in range(0, len(pts), 2):
                x, y = pts[i], pts[i + 1]
                cv.create_oval(x - 3, y - 3, x + 3, y + 3,
                               fill=COLORS["primary"], outline="")
        cv.create_text(pad_l - 6, pad_t, text=str(max_v), anchor="ne",
                       fill=COLORS["ink_faint"], font=font("micro"))
        cv.create_text(pad_l - 6, h - pad_b, text="0", anchor="se",
                       fill=COLORS["ink_faint"], font=font("micro"))
        self._trend_note.configure(
            text=f"柱＝{'每小时' if st.days == 1 else '每天'}接待量　线＝自动解决率")

    def _render_distribution(self):
        for child in self._dist_host.winfo_children():
            child.destroy()
        st = self._stats
        assert st is not None
        total = sum(st.by_path.values())
        if not total:
            hint(self._dist_host, "这段时间没有接待记录").pack(anchor="w")
            return
        items = [kv for kv in st.by_path.most_common(10)]
        for name, count in items:
            row = bar_row(self._dist_host, name, count, total,
                          color=(COLORS["primary_soft"] if name in NEUTRAL_PATHS
                                 else COLORS["warning"]))
            row.pack(fill="x", pady=1)
            row.bind("<Button-1>", lambda e, n=name: self._show_samples(n))
            for child in row.winfo_children():
                child.bind("<Button-1>", lambda e, n=name: self._show_samples(n))

    def _show_samples(self, name: str):
        st = self._stats
        assert st is not None
        msgs = st.sample_messages.get(name, [])
        text = "、".join(m[:24] for m in msgs[:12]) if msgs else "（这一类没有留下客户原话）"
        self._samples_host.pack(fill="x", padx=SPACE["lg"], pady=(0, SPACE["lg"]))
        self._samples.configure(state="normal")
        self._samples.delete("1.0", "end")
        self._samples.insert("1.0", f"「{name}」客户原话样例：{text}")
        self._samples.configure(state="disabled")

    def _render_unanswered(self):
        for iid in self._tree.get_children():
            self._tree.delete(iid)
        st = self._stats
        assert st is not None
        if not st.unanswered_top:
            self._tree.insert("", "end", values=("（暂无）", "", ""))
            return
        for item in st.unanswered_top:
            self._tree.insert("", "end", values=(item["question"], item["count"],
                                                 _short_reason(item["reason"])))

    def _render_guards(self):
        for child in self._guard_host.winfo_children():
            child.destroy()
        st = self._stats
        assert st is not None
        hits = [(k, v) for k, v in st.by_path.most_common() if k in GUARD_PATHS and v]
        if not hits:
            hint(self._guard_host, "这段时间没有需要拦下的消息 —— 都是常规问答").pack(anchor="w")
            return
        for name, count in hits:
            cell = tk.Frame(self._guard_host, background=COLORS["canvas"])
            cell.pack(side="left", padx=(0, SPACE["md"]), pady=2)
            tone = "danger" if name in ("越权承诺", "越权操作", "退款与投诉") else "warning"
            badge(cell, name, tone).pack(side="left")
            tk.Label(cell, text=f" {count}", font=font("body", True),
                     background=COLORS["canvas"], foreground=COLORS["ink"]).pack(side="left")
        self._guard_note.configure(
            text=f"共拦下 {sum(c for _, c in hits)} 条　"
                 f"正常直答 {st.by_path.get('正常直答', 0)} 条")

    # ── 动作 ───────────────────────────────────────────────────────────

    def _add_to_kb(self):
        """双击 → 把客户原话预填进「新增资料」（复用现有的 kb_edit）。"""
        sel = self._tree.selection()
        if not sel:
            return
        question = self._tree.item(sel[0], "values")[0]
        if not question or question == "（暂无）":
            return
        if self.qdrant is None:
            messagebox.showerror("加不了", "拿不到资料库连接（主程序没起来？）",
                                 parent=self.win)
            return
        from gui.kb_edit import open_editor
        open_editor(self.win, None, self.qdrant, self.collection,
                    on_saved=lambda res, q=question: self._added(q),
                    cfg=self.cfg, prefill={"question": question})

    def _added(self, question: str):
        try:
            from rag import unanswered
            unanswered.mark_done(question)
        except Exception:
            pass
        messagebox.showinfo("已加进资料库",
                            f"「{question[:24]}」已加进资料库，这条会从排行榜消失。",
                            parent=self.win)
        self.refresh()

    def _show_reason(self):
        sel = self._tree.selection()
        if not sel:
            return
        values = self._tree.item(sel[0], "values")
        if len(values) >= 3 and values[2] and values[2] != "—":
            self._samples.configure(state="normal")
            self._samples.delete("1.0", "end")
            self._samples.insert("1.0", f"「{values[0]}」被转人工的原因：{values[2]}")
            self._samples.configure(state="disabled")

    def _export(self):
        st = self._stats
        if st is None: 
            return
        if not messagebox.askokcancel(
                "导出会带客户原话",
                "导出的 CSV 含有客户原话与你的回复，注意不要外发。继续？",
                parent=self.win):
            return
        path = filedialog.asksaveasfilename(
            parent=self.win, title="导出看板数据", defaultextension=".csv",
            initialfile=f"客服数据_{self._start}_{self._end}.csv",
            filetypes=[("CSV 文件", "*.csv")])
        if not path:
            return
        rows = agg.read_reply_log(agg.ROOT / "logs" / "auto_replies.jsonl")
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["时间", "客户", "客户消息", "处理结果", "归类", "回复内容"])
            for r in sorted(rows, key=lambda x: x["ts"]):
                if not (self._start <= r["ts"].date() <= self._end):
                    continue
                w.writerow([r["ts"].strftime("%Y-%m-%d %H:%M:%S"), r["customer"],
                            r["message"], r["source"], agg.classify_path(r), r["reply"]])
        messagebox.showinfo("导出完成", f"已导出到：\n{path}", parent=self.win)

    def _close(self):
        if self.on_close:
            try:
                self.on_close()
            except Exception:
                pass
        self.win.destroy()


# ═══ 日历弹窗（自绘，零依赖）══════════════════════════════════════════════

class CalendarPopup:
    """日历选起止日期。第一次点=开始，第二次点=结束（反序自动交换）。"""

    def __init__(self, parent: tk.Misc, start: date, end: date):
        self.start = start
        self.end = end
        self._cursor = date(start.year, start.month, 1)
        self.result: tuple[date, date] | None = None
        self.win = tk.Toplevel(parent)
        self.win.title("选择时间范围")
        self.win.configure(background=COLORS["canvas"])
        self.win.transient(parent)
        self.win.resizable(False, False)
        apply_theme(self.win)
        self._build()

    def _build(self):
        head = tk.Frame(self.win, background=COLORS["canvas"])
        head.pack(fill="x", padx=SPACE["lg"], pady=(SPACE["lg"], SPACE["sm"]))
        ttk.Button(head, text="‹", width=3, style="Ghost.TButton",
                   command=lambda: self._shift(-1)).pack(side="left")
        self._title = tk.Label(head, text="", font=font("h2", True), width=12,
                               background=COLORS["canvas"], foreground=COLORS["ink"])
        self._title.pack(side="left", padx=SPACE["sm"])
        ttk.Button(head, text="›", width=3, style="Ghost.TButton",
                   command=lambda: self._shift(1)).pack(side="left")

        quick = tk.Frame(self.win, background=COLORS["canvas"])
        quick.pack(fill="x", padx=SPACE["lg"])
        for text, fn in (("本月", self._this_month), ("上月", self._last_month),
                         ("近 30 天", self._last_30)):
            ttk.Button(quick, text=text, style="Ghost.TButton",
                       command=fn).pack(side="left", padx=(0, SPACE["xs"]))

        self._grid = tk.Frame(self.win, background=COLORS["canvas"])
        self._grid.pack(padx=SPACE["lg"], pady=SPACE["sm"])
        self._sel_label = tk.Label(self.win, text="", font=font("small"),
                                   background=COLORS["canvas"],
                                   foreground=COLORS["primary_deep"])
        self._sel_label.pack(pady=(SPACE["xs"], 0))

        foot = tk.Frame(self.win, background=COLORS["canvas"])
        foot.pack(fill="x", padx=SPACE["lg"], pady=SPACE["lg"])
        ttk.Button(foot, text="取消", style="Ghost.TButton",
                   command=self.win.destroy).pack(side="right")
        ttk.Button(foot, text="确定", style="Primary.TButton",
                   command=self._confirm).pack(side="right", padx=(0, SPACE["sm"]))
        self._render()

    def _shift(self, months: int):
        y, m = self._cursor.year, self._cursor.month + months
        y += (m - 1) // 12
        m = (m - 1) % 12 + 1
        self._cursor = date(y, m, 1)
        self._render()

    def _this_month(self):
        self._cursor = date.today().replace(day=1)
        self._render()

    def _last_month(self):
        first = date.today().replace(day=1)
        self._cursor = (first - timedelta(days=1)).replace(day=1)
        self._render()

    def _last_30(self):
        self.end = date.today()
        self.start = self.end - timedelta(days=29)
        self._cursor = self.start.replace(day=1)
        self._render()

    def _pick(self, d: date):
        if d > date.today():          # 未来没有数据
            return
        if self._picking == "start":
            self.start, self.end = d, d
            self._picking = "end"
        else:
            if d < self.start:
                self.start, self.end = d, self.start
            else:
                self.end = d
            self._picking = "start"
        self._render()

    _picking = "start"

    def _render(self):
        for c in self._grid.winfo_children():
            c.destroy()
        self._title.configure(text=f"{self._cursor.year} 年 {self._cursor.month} 月")
        for i, wd in enumerate(("一", "二", "三", "四", "五", "六", "日")):
            tk.Label(self._grid, text=wd, width=4, font=font("micro"),
                     background=COLORS["canvas"],
                     foreground=COLORS["ink_faint"]).grid(row=0, column=i, pady=(0, 2))
        cal = calendar.Calendar(firstweekday=0)
        today = date.today()
        for r, week in enumerate(cal.monthdatescalendar(self._cursor.year,
                                                        self._cursor.month), start=1):
            for c, d in enumerate(week):
                in_month = d.month == self._cursor.month
                selected = self.start <= d <= self.end
                future = d > today
                bg = COLORS["primary_subtle"] if selected else COLORS["canvas"]
                fg = (COLORS["ink_faint"] if future or not in_month
                      else (COLORS["primary_deep"] if selected else COLORS["ink"]))
                lbl = tk.Label(self._grid, text=str(d.day), width=4, pady=3,
                               font=font("small", selected), background=bg,
                               foreground=fg, cursor="hand2")
                lbl.grid(row=r, column=c)
                if not future:
                    lbl.bind("<Button-1>", lambda e, dd=d: self._pick(dd))
        days = (self.end - self.start).days + 1
        self._sel_label.configure(
            text=f"已选 {self.start} ~ {self.end}（{days} 天）　第一次点＝开始，第二次点＝结束")

    def _confirm(self):
        self.result = (self.start, self.end)
        self.win.destroy()

    def show(self):
        self.win.grab_set()
        self.win.wait_window()
        return self.result


def open_dashboard(parent: tk.Misc, **kw) -> DashboardWindow:
    """给 main.py 用的入口。"""
    return DashboardWindow(parent, **kw)
