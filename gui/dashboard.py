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
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from stats import aggregate as agg
from gui.theme import (COLORS, SPACE, apply_theme, badge, bar_row, card,
                       card_body, detect_dpi_scale, divider, font, hint, kpi_card,
                       px, section_title, set_scale)

# 护栏分组：这些是"我拦下了什么"，和"正常直答"分开显示
GUARD_PATHS = ("越权承诺", "拖延话术", "议价加码", "议价特批", "比价跟价", "押金特批",
               "费用特批", "政策未覆盖", "订单与发票查询", "订单变更", "退款与投诉",
               "库存与档期", "指代不明", "店主私事", "越权操作", "要人工",
               "非本店业务", "时间承诺", "越界婉拒", "模型要人工", "置信度不足")
NEUTRAL_PATHS = ("正常直答", "闲聊", "无信息量/收尾", "人工发出", "欢迎语", "本地直答",
                 "其他")

# 示例数据：由 scripts/make_demo_stats.py 把三批离线评测（各 200 条）回放生成。
# 存在的意义：新用户第一次打开看板是空的，看不出价值；勾一下就能看到"长什么样"。
# 它**不是**真实接待记录，界面上必须明确标出来（免得店主认成自己的数据）。
DEMO_ROOT = Path(__file__).resolve().parent.parent / "data" / "demo_stats"


def _fmt_seconds(v: float | None) -> str:
    if v is None:
        return "—"
    if v < 10:
        return f"{v:.1f}"
    if v < 60:
        return f"{v:.0f}"
    return f"{v / 60:.1f}"


def _short_reason(reason: str) -> str:
    """把转人工原因翻成店主看得懂的话并截短。

    日志里存的是内部说法（"置信度不足(0.48)" / "llm_requests_human" /
    "越权承诺（时效承诺）"），表格里放不下也没必要看括号里的分数。
    """
    r = (reason or "").strip()
    if not r:
        return "—"
    for sep in ("(", "（"):
        if sep in r:
            r = r.split(sep)[0]
    r = r.strip()
    alias = {
        "llm_requests_human": "模型不确定",
        "all_checks_passed": "分数不够",
        "retrieval_low_confidence": "检索分数低",
        "low_retrieval_confidence": "检索分数低",
        "must_escalate": "规则要求转人工",
        "deferral_phrase": "回复是拖延话术",
        "unsafe_promise": "越权承诺",
        "hold": "转人工",
    }
    return alias.get(r, r) or "—"


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
        self._demo_on = False          # 是否在看示例数据（见 _enter_demo）
        self._stats: agg.Stats | None = None

        self.win = tk.Toplevel(parent)
        self.win.title("运营看板")
        self.win.configure(background=COLORS["canvas_soft"])
        # ★ 缩放策略：以屏幕 DPI 为基准，再按**窗口宽度**相对设计宽度等比缩放 ——
        #   这样"框会随窗口放大缩小"，而不是写死像素。第一版写死了，于是高 DPI 下
        #   「近 7 天/自定义」被裁、窗口拉大后中间又一坨空白。
        #   设计稿宽 1220（theme 里 scale=1 时的样子）。
        self._base_scale = detect_dpi_scale(self.win)
        natural_w = int(1220 * self._base_scale)
        natural_h = int(880 * self._base_scale)
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        w = max(int(900 * self._base_scale), min(natural_w, sw - 80))
        h = max(int(640 * self._base_scale), min(natural_h, sh - 120))
        self.win.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 3}")
        self.win.minsize(int(780 * self._base_scale), int(520 * self._base_scale))
        self.win.protocol("WM_DELETE_WINDOW", self._close)
        apply_theme(self.win, self._base_scale)
        self._scale_now = self._base_scale
        self._resize_job = None
        # 窗口尺寸一变就重新按比例算缩放（防抖 180ms，避免拖拽时反复重建）
        self.win.bind("<Configure>", self._on_window_configure)

        self._build()
        self.refresh()

    # ── 界面骨架 ───────────────────────────────────────────────────────

    def _on_window_configure(self, event=None):
        """窗口大小变了 → 按新宽度重算缩放（防抖后再动手）。"""
        if event is not None and event.widget is not self.win:
            return
        if self._resize_job:
            try:
                self.win.after_cancel(self._resize_job)
            except Exception:
                pass
        self._resize_job = self.win.after(180, self._apply_resize)

    def _apply_resize(self):
        self._resize_job = None
        try:
            w = self.win.winfo_width()
        except Exception:
            return
        if w <= 1:
            return
        natural_w = max(1, int(1220 * self._base_scale))
        target = set_scale(self._base_scale * (w / natural_w))
        if abs(target - self._scale_now) < 0.06:
            return                     # 变化太小不重建，避免抖动
        self._scale_now = target
        apply_theme(self.win, target)
        self._rebuild_body()           # tk 控件不会自动改字号 → 重建内容区

    def _rebuild_body(self):
        for child in self._body.winfo_children():
            child.destroy()
        self._build_body()

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
        self._build_body()

    def _build_body(self):
        """内容区（可重复构建：窗口缩放后字号要跟着变，而 tk 控件不会自动改）。"""
        pad = tk.Frame(self._body, background=COLORS["canvas_soft"])
        pad.pack(fill="both", expand=True, padx=SPACE["lg"], pady=SPACE["lg"])
        self._build_kpis(pad)
        self._build_trend(pad)
        self._build_two_panels(pad)
        self._build_guards(pad)
        hint(pad, "口径：接待＝机器人发出的回复条数（以回复为单位）；"
                  "自动解决率＝真答 ÷（真答＋转人工占位语）；中位响应含占位语。"
                  "数据只在本机，不上传。").pack(anchor="w", pady=(SPACE["sm"], 0))
        # 缩放后所有子控件都是新的，得用当前数据重绘一遍
        if self._stats is not None:
            self._render_kpis()
            self._draw_chart()
            self._render_distribution()
            self._render_unanswered()
            self._render_guards()

    def _build_topbar(self):
        bar = tk.Frame(self.win, background=COLORS["canvas"])
        bar.pack(fill="x")
        inner = tk.Frame(bar, background=COLORS["canvas"])
        inner.pack(fill="x", padx=SPACE["lg"], pady=SPACE["md"])
        self._topbar_inner = inner

        # ★ 先 pack 右侧那组（导出/刷新/时间范围）：Tk 的 pack 是**先 pack 的先占空间**，
        #   右侧最后 pack 的话一超宽就被裁掉（上一版「导出 CSV」和日期都是这么没的）
        ttk.Button(inner, text="导出", style="Secondary.TButton",
                   command=self._export).pack(side="right")
        ttk.Button(inner, text="刷新 ↻", style="Ghost.TButton",
                   command=self.refresh).pack(side="right", padx=(0, SPACE["sm"]))
        self._range_label = tk.Label(inner, text="", font=font("small"),
                                     background=COLORS["canvas"],
                                     foreground=COLORS["ink_mute"])
        self._range_label.pack(side="right", padx=(0, SPACE["md"]))

        tk.Label(inner, text="运营看板", font=font("h1", True),
                 background=COLORS["canvas"], foreground=COLORS["ink"]).pack(side="left")

        # 分段控件（今日 / 近 7 天 / 自定义）—— 选中态是靛蓝浅底
        self._seg = {}
        seg = tk.Frame(inner, background=COLORS["canvas"])
        seg.pack(side="left", padx=(SPACE["lg"], 0))
        for key, text in (("today", "今日"), ("week", "近 7 天"), ("custom", "自定义")):
            b = ttk.Button(seg, text=text, style="Segment.TButton",
                           command=lambda k=key: self._pick_range(k))
            b.pack(side="left", padx=(0, SPACE["xs"]))
            self._seg[key] = b

        # 示例数据开关**不放顶栏**：顶栏要装 标题+时间范围+日期+刷新+导出，已经很挤，
        # 再塞一个复选框会互相挤掉（实测把日期和「导出 CSV」都挤没了）。
        # 它真正被需要的时刻只有一个：**面板空着、不知道这块讲什么的时候** ——
        # 入口放在空状态里（见 _render_distribution），进去后横幅上有「退出示例」。
        self._topbar_sep = tk.Frame(self.win, height=1, background=COLORS["hairline"])
        self._topbar_sep.pack(fill="x")

    def _enter_demo(self):
        """切到示例数据。当前范围没数据时自动放宽到近 7 天（不然还是空的）。"""
        self._demo_on = True
        probe = agg.aggregate(self._start, self._end, root=DEMO_ROOT)
        if probe.replies == 0 and self._range == "today":
            self._range = "week"
            self._end = date.today()
            self._start = self._end - timedelta(days=6)
        self.refresh()

    def _exit_demo(self):
        self._demo_on = False
        self.refresh()

    def _sync_demo_banner(self):
        """示例数据时必须**明说**这不是真实数据（免得店主认成自己的经营数据）。"""
        if self._demo_on:
            if not getattr(self, "_demo_box", None):
                self._demo_box = tk.Frame(self.win, background=COLORS["info_bg"])
                # ★ 按钮先 pack：Tk 是先 pack 的先占空间，标签后 pack 才会被裁
                #   （顶栏那次的教训一样）
                ttk.Button(self._demo_box, text="退出示例", style="Ghost.TButton",
                           command=self._exit_demo).pack(side="right", padx=SPACE["sm"])
                tk.Label(self._demo_box,
                         text="示例数据（600 条离线评测回放）· 不是你的真实接待记录",
                         font=font("small"), background=COLORS["info_bg"],
                         foreground=COLORS["info"], anchor="w").pack(
                    side="left", padx=SPACE["md"], pady=SPACE["sm"])
            self._demo_box.pack(fill="x", after=self._topbar_sep)
        elif getattr(self, "_demo_box", None):
            self._demo_box.pack_forget()

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
        self._chart = tk.Canvas(body, height=px(132), background=COLORS["canvas"],
                                highlightthickness=0, bd=0)
        self._chart.pack(fill="x", padx=SPACE["lg"], pady=(0, SPACE["lg"]))
        self._chart.bind("<Configure>", lambda e: self._draw_chart())

    def _build_two_panels(self, parent):
        row = tk.Frame(parent, background=COLORS["canvas_soft"], height=px(330))
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
        for cid, text, width, anchor in (("q", "客户原话", px(168), "w"),
                                         ("n", "次数", px(44), "center"),
                                         ("why", "转人工原因", px(116), "w")):
            self._tree.heading(cid, text=text)
            self._tree.column(cid, width=width, minwidth=40, anchor=anchor,
                              stretch=(cid == "q"))
        self._tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self._tree.yview)
        sb.pack(side="right", fill="y")
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.bind("<Double-1>", lambda e: self._add_to_kb())
        self._tree.bind("<<TreeviewSelect>>", lambda e: self._show_reason())
        # 列宽按面板实际宽度按比例分配（不是写死像素）——窗口一变还能保持协调
        self._tree_wrap = wrap
        wrap.bind("<Configure>", lambda e: self._fit_tree_columns(e.width))

    def _fit_tree_columns(self, width: int):
        """按面板宽度分配三列：原话 46% / 次数 14% / 原因 40%。"""
        if width <= 1:
            return
        for cid, ratio, minw in (("q", 0.46, px(120)), ("n", 0.14, px(40)),
                                 ("why", 0.40, px(90))):
            try:
                self._tree.column(cid, width=max(minw, int(width * ratio)))
            except Exception:
                pass

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
        # 同年时省掉年份，让顶部那排更省空间（差一年才写全）
        same_year = self._start.year == date.today().year
        fmt = "%m-%d" if same_year else "%Y-%m-%d"
        span = (self._start.strftime(fmt) if days == 1
                else f"{self._start.strftime(fmt)} ~ {self._end.strftime(fmt)}")
        self._range_label.configure(text=f"{span}（{days} 天）")

    def _fit_topbar(self):
        """顶栏放不下时先藏日期标签 —— 趋势图右上角也有时间提示，不会丢信息。

        （写死尺寸那版就是这里出问题：高 DPI 下「近 7 天/自定义」直接被裁掉。）
        """
        try:
            self.win.update_idletasks()
            avail = self.win.winfo_width() - px(2 * SPACE["lg"]) - px(8)
            need = self._topbar_inner.winfo_reqwidth()
            shown = bool(self._range_label.winfo_ismapped())
        except Exception:
            return
        if need > avail and shown:
            self._range_label.pack_forget()
        elif need < avail - px(70) and not shown:
            self._range_label.pack(side="right", padx=(0, SPACE["md"]))

    # ── 刷新与绘制 ─────────────────────────────────────────────────────

    def refresh(self):
        self._sync_segments()
        self._sync_demo_banner()
        root = DEMO_ROOT if self._demo_on else None
        self._stats = agg.aggregate(self._start, self._end, root=root)
        self._render_kpis()
        self._draw_chart()
        self._render_distribution()
        self._render_unanswered()
        self._render_guards()
        self._fit_topbar()

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
             "含占位语", "ink"),
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
        last_label_x = None
        min_gap = px(96)          # 两个横轴标签之间的最小间距（按缩放走）
        for i, b in enumerate(st.buckets):
            x = pad_l + slot * i + slot / 2
            bar_h = (b.replies / max_v) * plot_h
            if b.replies:
                cv.create_rectangle(x - bar_w / 2, h - pad_b - bar_h,
                                    x + bar_w / 2, h - pad_b,
                                    fill=COLORS["primary_subtle"], outline="")
                cv.create_text(x, h - pad_b - bar_h - px(8), text=str(b.replies),
                               fill=COLORS["ink_mute"], font=font("micro"))
            if b.solve_rate is not None:
                y = h - pad_b - b.solve_rate * plot_h
                pts.extend([x, y])
            # 横轴标签：**按实际间距决定要不要画**（写死"隔几个画一个"在窗口变宽后
            # 仍会挤成一团，比如 24 个小时标签全挤在一起）
            if last_label_x is None or (x - last_label_x) >= min_gap:
                cv.create_text(x, h - pad_b + px(10), text=b.label,
                               fill=COLORS["ink_faint"], font=font("micro"))
                last_label_x = x
        if last_label_x is not None and (pad_l + slot * (n - 1) + slot / 2
                                         - last_label_x) >= min_gap * 0.6:
            cv.create_text(pad_l + slot * (n - 1) + slot / 2, h - pad_b + px(10),
                           text=st.buckets[-1].label, fill=COLORS["ink_faint"],
                           font=font("micro"))
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
        if total == 0:
            hint(self._dist_host, "这段时间没有接待记录").pack(anchor="w")
            if not self._demo_on:
                ttk.Button(self._dist_host, text="用示例数据看看效果",
                           style="Secondary.TButton",
                           command=self._enter_demo).pack(anchor="w",
                                                          pady=(SPACE["sm"], 0))
                hint(self._dist_host,
                     "示例数据＝把 600 条离线评测回放成接待记录，不是你的真实数据").pack(
                    anchor="w", pady=(SPACE["xs"], 0))
            return
        items = [kv for kv in st.by_path.most_common(10)]
        # 条形宽度按面板实际宽度取比例（不写死像素，窗口变宽它就跟着变宽）
        panel_w = self._dist_host.winfo_width()
        bar_w = max(px(60), int((panel_w if panel_w > 1 else px(300)) * 0.26))
        for name, count in items:
            row = bar_row(self._dist_host, name, count, total, width=bar_w,
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
