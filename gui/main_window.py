# gui/main_window.py
"""主控窗口模块 — tkinter GUI

职责：
- 实时显示每条消息的处理结果
- 状态栏显示统计信息
- 「待人工」页列出置信度不足的消息 + AI 推荐回复，可选择直接发送或编辑后发送
- 支持导出记录为 CSV

分流为二档（见 rag/responder.py）：分数够就直发，否则转人工、自动回一句
占位语，并把 AI 草稿放进「待人工」等你处理。
"""

import csv
import logging
import os
import tkinter as tk
from datetime import datetime
from tkinter import ttk, messagebox
from typing import Callable, Dict, List, Optional

from gui.theme import (COLORS, SPACE, apply_theme, detect_dpi_scale, font, px,
                       scale as _ui_scale)

logger = logging.getLogger(__name__)


class MessageRecord:
    """消息记录"""

    def __init__(self, timestamp: str, customer: str, message: str,
                 action: str, reply: str = ""):
        self.timestamp = timestamp
        self.customer = customer
        self.message = message
        self.action = action
        self.reply = reply

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "customer": self.customer,
            "message": self.message,
            "action": self.action,
            "reply": self.reply,
        }


class MainWindow:
    """主控窗口"""

    def __init__(self, on_pause: Callable = None, on_resume: Callable = None,
                 on_settings: Callable = None, on_kb: Callable = None,
                 on_dashboard: Callable = None, can_start: Callable = None):
        self.on_pause = on_pause
        self.on_resume = on_resume
        # 「设置」常驻在头部，任何状态（未启动/运行中/已配置过）都能点开重配
        self.on_settings = on_settings
        # 「知识库」：编辑提示词 / 管理资料库（常驻，任何时候可点）
        self.on_kb = on_kb
        # 「数据」：运营看板（今日/近7天/自定义；我该补什么资料）
        self.on_dashboard = on_dashboard
        # ★ 「能不能开始」的守卫（由 main.py 注入）：返回 (是否允许, 原因)。
        #   没配模型接口时不许开始 —— 否则机器人答不了业务问题，却照样回客户
        #   "稍等，我帮您确认一下"；没人处理「待人工」就等于替店主许了个空头承诺。
        self.can_start = can_start
        self._records: List[MessageRecord] = []
        # 默认**未启动**：打开程序不会自动开始扫描/回复，先让用户确认配置再点「开始」。
        # 已有的暂停机制本来就同时挡住两条通道（截图扫描与 API 轮询），所以直接复用。
        # 想开机就跑（或脚本化启动）：设环境变量 WECOM_AUTOSTART=1。
        import os as _os
        self._autostart = _os.getenv("WECOM_AUTOSTART", "0").strip() == "1"
        if self._autostart and not self._start_allowed()[0]:
            # 没配好就不许偷偷跑起来（脚本化启动也一样）
            self._autostart = False
        self._paused = not self._autostart
        self._should_exit = False

        # 待人工页的动作回调（由 main.py 注册）
        self._on_pending_send: Optional[Callable] = None
        self._on_pending_edit: Optional[Callable] = None
        self._on_pending_ignore: Optional[Callable] = None
        self._on_get_reply: Optional[Callable] = None

        # Stats：只统计真实会发生的两种结果
        #   replied   = 已发给客户（含直发和转人工时的占位语）
        #   escalated = 转人工（同时在待人工页可见）
        self._stats = {
            "replied": 0,
            "escalated": 0,
        }

        # Create window
        self._create_window()

    def _create_window(self):
        """创建窗口"""
        self.root = tk.Tk()
        self.root.title("企业微信智能客服")
        # 先给个大致位置（建控件期间别在屏幕角落闪）
        sw = self.root.winfo_screenwidth()
        self.root.geometry(f"{px(760)}x{px(320)}+{max(0, sw - px(780))}+{px(10)}")
        self.root.attributes('-topmost', True)
        # ★ 最小尺寸：以前没设，用户可以把窗口拖到 600px 以下，
        #   那时横幅、标签页、表格会互相盖住（用户反馈改窗口大小就盖住按钮）
        self.root.minsize(px(680), px(420))
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # ★ 先把设计系统装上（ttk 样式必须在创建控件之前生效）
        apply_theme(self.root)
        # 窗口被拖动改大小 → 重排头部 + 表格列按比例（防抖，见 _on_root_configure）
        self.root.bind("<Configure>", self._on_root_configure)
        self.root.configure(background=COLORS["canvas_soft"])

        # 顶部标题栏
        self._create_header()

        # 状态横幅：未配置 / 目标软件不在 / 正常，都显示在这里
        self._create_banner()

        # 状态栏
        self._create_status_bar()

        # 标签页
        self._create_tabs()

        # 底部按钮
        self._create_footer()

        # ★ 按内容自适应尺寸：换主题后标题与按钮都变宽了，
        #   在 125%/150% 缩放的屏幕上固定 720px 会把「知识库/设置」裁掉
        #   （打包版实测界面右边被切）。这里量一次控件要求的尺寸再定窗口大小。
        self._fit_window(sw)

    def _fit_window(self, screen_w: int):
        """窗口保持**紧凑**：够装内容就够，但不无限撑大；放不下时由头部换行解决。

        ★ 上一版为了"按钮不被裁"改成按内容无限撑开，结果在 150% 缩放的屏幕上
          撑到了 1600+ px（用户反馈"主页面怎么变这么大"）。正确做法是**两条一起**：
          ① 窗口有个紧凑上限；② 装不下时头部**换行重排**（见 _reflow_header）。
        """
        try:
            self.root.update_idletasks()
            need_h = self.root.winfo_reqheight()
            screen_h = self.root.winfo_screenheight()
        except Exception:
            return
        # 紧凑上限：头部会换行（_reflow_header），所以不必为了塞下按钮把窗口撑宽。
        # 820×520 设计像素 ≈ 标题一行 + 按钮一行 + 表格 4~5 行，够用又不占屏。
        w = min(px(820), max(px(700), screen_w - px(40)))
        h = max(px(320), min(need_h + px(8), px(520), screen_h - px(80)))
        x = max(0, screen_w - w - px(20))
        self.root.geometry(f"{w}x{h}+{x}+{px(10)}")
        # ★ 多次重排：首次（80ms）此时窗口可能还没真正映射，winfo_width() 不可靠；
        #   映射之后（400ms / 1.2s）再用**真实几何**复核一遍 —— 打包版在 150% 缩放
        #   的机器上就是"按请求宽度算能放下、实际放不下"，把「知识库/设置」裁到了屏幕外。
        for delay in (80, 400, 1200):
            self.root.after(delay, self._reflow_header)

    def _header_bar_overflows(self) -> bool:
        """按钮条实际画出来之后，右边缘有没有超出窗口？（比 reqwidth 可靠）"""
        try:
            self.root.update_idletasks()
            right = self._btn_bar.winfo_x() + self._btn_bar.winfo_width()
            return right > self.root.winfo_width() - px(SPACE["sm"])
        except Exception:
            return False

    def _reflow_header(self):
        """窄窗口时把次要按钮**换到第二行** —— 而不是让它们被裁掉/盖住。

        用 grid 而不是 pack：pack 要求控件必须 pack 在自己的 master 里，
        没法"从第一行挪到第二行"；grid 只要还在同一个 master 里，
        grid_configure 就能换行换列（踩过一次：can't pack ... inside ...）。
        """
        try:
            self.root.update_idletasks()
            avail = self.root.winfo_width() - px(2 * SPACE["md"]) - px(8)
            need = (self._title_label.winfo_reqwidth()
                    + self._btn_bar.winfo_reqwidth() + px(SPACE["lg"]))
        except Exception:
            return
        inline = need <= avail
        # ★ 已经在第一行、但**实际画出来**右边缘超出窗口 → 强制换行。
        #   为什么需要这道：`winfo_reqwidth()` 是按控件请求宽度算的，在 DPI 缩放
        #   不一致的环境里会"算着能放下、实际放不下" —— 打包版实测「知识库/设置」
        #   被裁到窗口外。真实几何比请求宽度可靠。
        if inline and getattr(self, "_header_inline", None) is True \
                and self._header_bar_overflows():
            self._wrap_above = self.root.winfo_width()
            inline = False
        elif inline and getattr(self, "_wrap_above", 0) \
                and self.root.winfo_width() <= self._wrap_above + px(40):
            # 还没明显变宽，别又试回第一行（否则 1.2s 那次复核会把它翻回来）
            inline = False
        # 布局数值落日志：这类"按钮被裁"的问题只有真实数字能定位
        # （用户反馈过一次、打包版实测又一次，都靠这几个数才找对原因）。
        # 只在数值变化时记一行 —— 拖窗口时不会刷屏。
        try:
            import logging as _logging
            sig = (self.root.winfo_width(), avail, need,
                   self._btn_bar.winfo_width(), inline)
            if sig != getattr(self, "_layout_sig", None):
                self._layout_sig = sig
                _logging.getLogger(__name__).info(
                    "头部布局: 窗口=%d 可用=%d 需要=%d 按钮条=%d 右边缘=%d "
                    "第一行=%s 缩放=%.2f",
                    self.root.winfo_width(), avail, need,
                    self._btn_bar.winfo_width(),
                    self._btn_bar.winfo_x() + self._btn_bar.winfo_width(),
                    inline, _ui_scale())
        except Exception:
            pass

        if inline == getattr(self, "_header_inline", None):
            return
        self._header_inline = inline
        if inline:
            self._btn_bar.grid_configure(row=0, column=1, columnspan=1, sticky="e",
                                         pady=(SPACE["sm"], SPACE["sm"]))
        else:
            self._btn_bar.grid_configure(row=1, column=0, columnspan=2, sticky="e",
                                         pady=(0, SPACE["sm"]))

    def _on_root_configure(self, event=None):
        """窗口被拖动改大小 → 防抖后重排头部 + 按比例分表格列。"""
        if event is not None and event.widget is not self.root:
            return
        if getattr(self, "_resize_job", None):
            try:
                self.root.after_cancel(self._resize_job)
            except Exception:
                pass
        self._resize_job = self.root.after(150, self._after_resize)

    def _after_resize(self):
        self._resize_job = None
        self._reflow_header()
        self._fit_tree_columns()
        self._fit_banner()

    def _fit_banner(self):
        """横幅文字按窗口宽度换行 —— 单行不换行时右边会被硬切（"…或点「设置」改配"）。"""
        try:
            w = self.root.winfo_width()
            if w > 1:
                self._banner.configure(wraplength=max(px(240), w - px(36)))
        except Exception:
            pass

    def _fit_tree_columns(self):
        """表格列宽按窗口宽度**按比例**分配（写死宽度在窄窗口下会把最后一列挤掉）。"""
        try:
            w = self._list_frame.winfo_width()
        except Exception:
            return
        if w <= 1:
            return
        for cid, ratio, minw in (("time", 0.13, px(64)), ("customer", 0.20, px(90)),
                                 ("message", 0.43, px(140)), ("action", 0.24, px(90))):
            try:
                self._tree.column(cid, width=max(minw, int(w * ratio)))
            except Exception:
                pass
        try:
            pw = self._pending_frame.winfo_width()
            if pw > 1:
                for cid, ratio, minw in (("ptime", 0.07, px(46)),
                                         ("pcustomer", 0.13, px(80)),
                                         ("pmsg", 0.24, px(130)),
                                         ("preply", 0.40, px(180)),
                                         ("pconf", 0.16, px(60))):
                    self._pending_tree.column(cid, width=max(minw, int(pw * ratio)))
        except Exception:
            pass

    def _create_header(self):
        """创建顶部标题栏（按 gui/theme.py 的设计系统：白底 + 发丝线分隔）。

        标题与按钮用 **grid** 摆：窄窗口时按钮整组换到第二行（见 _reflow_header），
        pack 做不到这件事。
        """
        header = tk.Frame(self.root, background=COLORS["canvas"])
        header.pack(fill=tk.X)
        self._header = header
        header.grid_columnconfigure(0, weight=1)      # 标题列可伸缩
        header.grid_columnconfigure(1, weight=0)      # 按钮列按需

        self._title_label = tk.Label(header, text="企业微信智能客服", font=font("h1", True),
                                     background=COLORS["canvas"],
                                     foreground=COLORS["ink"])
        self._title_label.grid(row=0, column=0, sticky="w",
                               padx=(SPACE["md"], 0), pady=(SPACE["sm"], SPACE["sm"]))

        # 按钮整组放在一个容器里：宽度不够时整组换到第二行（见 _reflow_header）
        btn_frame = tk.Frame(header, background=COLORS["canvas"])
        btn_frame.grid(row=0, column=1, sticky="e",
                       padx=(0, SPACE["md"]), pady=(SPACE["sm"], SPACE["sm"]))
        self._btn_bar = btn_frame
        self._header_inline = True

        self._pause_btn = ttk.Button(
            btn_frame, text="停止" if self._autostart else "开始",
            style="Primary.TButton", command=self._toggle_pause)
        self._pause_btn.pack(side=tk.LEFT, padx=(0, SPACE["xs"]))

        # 数据看板：店主最该常点的地方（"机器人替我做了什么 / 我该补什么资料"）
        self._dash_btn = ttk.Button(btn_frame, text="数据", style="Secondary.TButton",
                                    command=self._on_dashboard_clicked)
        self._dash_btn.pack(side=tk.LEFT, padx=SPACE["xs"])

        self._kb_btn = ttk.Button(btn_frame, text="知识库", style="Secondary.TButton",
                                  command=self._on_kb_clicked)
        self._kb_btn.pack(side=tk.LEFT, padx=SPACE["xs"])

        self._settings_btn = ttk.Button(btn_frame, text="设置", style="Ghost.TButton",
                                        command=self._on_settings_clicked)
        self._settings_btn.pack(side=tk.LEFT, padx=(SPACE["xs"], 0))

        sep = tk.Frame(header, height=1, background=COLORS["hairline"])
        sep.grid(row=2, column=0, columnspan=2, sticky="ew")

    def _on_dashboard_clicked(self):
        if self.on_dashboard:
            self.on_dashboard()

    def _on_kb_clicked(self):
        if self.on_kb:
            self.on_kb()

    def _on_settings_clicked(self):
        if self.on_settings:
            self.on_settings()

    def _create_banner(self):
        """状态横幅：把"为什么现在不干活"直接写在界面上。

        原来的坑：目标软件没打开时扫描会静默 return，界面还写着"运行中"，
        用户完全不知道问题在哪。
        """
        self._banner = tk.Label(self.root, text="", anchor="w", justify="left",
                                wraplength=px(600),
                                font=font("small"), foreground=COLORS["warning"],
                                background=COLORS["warning_bg"],
                                padx=SPACE["md"], pady=SPACE["sm"])
        self._banner.pack(fill=tk.X, padx=SPACE["md"], pady=(0, SPACE["xs"]))

    def set_banner(self, text: str, level: str = "warn"):
        """更新横幅。level: warn=黄 / error=红 / ok=绿（空文本＝隐藏）。"""
        colors = {
            "warn": (COLORS["warning_bg"], COLORS["warning"]),
            "error": (COLORS["danger_bg"], COLORS["danger"]),
            "ok": (COLORS["success_bg"], COLORS["success"]),
        }
        bg, fg = colors.get(level, colors["warn"])
        try:
            if not text:
                self._banner.pack_forget()
                return
            self._banner.config(text=text, background=bg, foreground=fg)
            self._banner.pack(fill=tk.X, padx=SPACE["md"], pady=(0, SPACE["xs"]))
        except Exception:
            pass

    def _create_status_bar(self):
        """创建状态栏"""
        status_frame = tk.Frame(self.root, background=COLORS["canvas_soft"])
        status_frame.pack(fill=tk.X, padx=SPACE["md"], pady=(SPACE["xs"], SPACE["xs"]))

        self._status_label = tk.Label(
            status_frame, text="状态：运行中" if self._autostart else "状态：未启动",
            font=font("small", True), background=COLORS["canvas_soft"],
            foreground=COLORS["ink_secondary"])
        self._status_label.pack(side=tk.LEFT)

        tk.Frame(status_frame, width=1, height=14,
                 background=COLORS["hairline"]).pack(side=tk.LEFT, fill=tk.Y,
                                                     padx=SPACE["md"])

        self._stats_label = tk.Label(
            status_frame, text="已回复 0　待人工 0", font=font("small"),
            background=COLORS["canvas_soft"], foreground=COLORS["ink_mute"])
        self._stats_label.pack(side=tk.LEFT)

    def _create_tabs(self):
        """创建标签页"""
        tab_frame = tk.Frame(self.root, background=COLORS["canvas_soft"])
        tab_frame.pack(fill=tk.BOTH, expand=True, padx=SPACE["md"], pady=SPACE["xs"])

        # 标签栏
        tab_bar = tk.Frame(tab_frame, background=COLORS["canvas_soft"])
        tab_bar.pack(fill=tk.X)

        self._tab_buttons = {}
        tabs = ["全部", "已回复", "待人工"]
        for tab in tabs:
            btn = ttk.Button(
                tab_bar, text=tab, style="Segment.TButton",
                command=lambda t=tab: self._switch_tab(t),
            )
            btn.pack(side=tk.LEFT, padx=(0, SPACE["xs"]))
            self._tab_buttons[tab] = btn

        self._current_tab = "全部"
        # 初始化时也要点亮一次选中态，否则一进来三个标签看着都"没选中"
        for name, b in self._tab_buttons.items():
            b.configure(style="SegmentOn.TButton" if name == "全部"
                        else "Segment.TButton")

        # ── message list treeview ──────────────────────────────────

        self._list_frame = ttk.Frame(tab_frame)
        self._list_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        columns = ("time", "customer", "message", "action")
        self._tree = ttk.Treeview(self._list_frame, columns=columns, show="headings")
        self._tree.heading("time", text="时间")
        self._tree.heading("customer", text="客户")
        self._tree.heading("message", text="消息")
        self._tree.heading("action", text="处理结果")
        self._tree.column("time", width=80)
        self._tree.column("customer", width=120)
        self._tree.column("message", width=300)
        self._tree.column("action", width=100)

        scrollbar = ttk.Scrollbar(self._list_frame, orient=tk.VERTICAL, command=self._tree.yview)
        self._tree.configure(yscrollcommand=scrollbar.set)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.root.after(120, self._fit_tree_columns)
        self._tree.tag_configure("replied", foreground="green")
        self._tree.tag_configure("escalated", foreground="red")

        # ── 待人工列表（点「待人工」标签时显示）──────────────────────
        # 置信度不足的消息会进这里，附带 AI 推荐回复，可【发送】或【编辑】

        self._pending_frame = ttk.Frame(tab_frame)

        p_columns = ("ptime", "pcustomer", "pmsg", "preply", "pconf", "paction")
        self._pending_tree = ttk.Treeview(self._pending_frame, columns=p_columns,
                                          show="headings")
        self._pending_tree.heading("ptime", text="时间")
        self._pending_tree.heading("pcustomer", text="客户")
        self._pending_tree.heading("pmsg", text="客户消息")
        self._pending_tree.heading("preply", text="AI 推荐回复")
        self._pending_tree.heading("pconf", text="置信度")
        self._pending_tree.heading("paction", text="")
        self._pending_tree.column("ptime", width=50)
        self._pending_tree.column("pcustomer", width=90)
        self._pending_tree.column("pmsg", width=170)
        self._pending_tree.column("preply", width=230)
        self._pending_tree.column("pconf", width=55)
        self._pending_tree.column("paction", width=0)  # hidden: 绑定用客户名

        p_scrollbar = ttk.Scrollbar(self._pending_frame, orient=tk.VERTICAL,
                                    command=self._pending_tree.yview)
        self._pending_tree.configure(yscrollcommand=p_scrollbar.set)

        self._pending_stats_label = ttk.Label(
            self._pending_frame, text="共 0 条待人工")

        pending_btn_frame = ttk.Frame(self._pending_frame)
        ttk.Button(pending_btn_frame, text="发送",
                   command=self._handle_pending_send).pack(side=tk.LEFT, padx=2)
        ttk.Button(pending_btn_frame, text="编辑",
                   command=self._handle_pending_edit).pack(side=tk.LEFT, padx=2)
        ttk.Button(pending_btn_frame, text="忽略",
                   command=self._handle_pending_ignore).pack(side=tk.LEFT, padx=2)

        self._pending_stats_label.pack(side=tk.BOTTOM, fill=tk.X, pady=3)
        pending_btn_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=3)
        self._pending_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        p_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self._pending_frame.pack_forget()  # 默认隐藏

    def _create_footer(self):
        """创建底部按钮"""
        footer = tk.Frame(self.root, background=COLORS["canvas"])
        footer.pack(fill=tk.X, padx=SPACE["md"], pady=(0, SPACE["sm"]))

        ttk.Button(footer, text="导出记录", style="Ghost.TButton",
                   command=self._export_csv).pack(side=tk.LEFT)
        ttk.Button(footer, text="清空", style="Ghost.TButton",
                   command=self._clear_records).pack(side=tk.LEFT, padx=(SPACE["xs"], 0))


    def _start_allowed(self):
        """问一下守卫能不能开始。守卫自己出错就当允许（别把它变成锁死程序的东西）。"""
        # getattr：单元测试会用 MainWindow.__new__ 造"半成品"实例，不能假设属性都在
        guard = getattr(self, "can_start", None)
        if guard is None:
            return True, ""
        try:
            ok, why = guard()
            return bool(ok), (why or "")
        except Exception:
            return True, ""

    def _refuse_start(self, why: str):
        """配置不完整 → 不让开始，并直接把人送到「设置」。"""
        self._pause_btn.config(text="开始")
        self._status_label.config(text="状态：未启动")
        one_line = " ".join((why or "").split())
        self.set_banner(f"⚠️ {one_line}", level="error")
        try:
            go = messagebox.askyesno("还不能开始", f"{why}\n\n现在打开「设置」？")
        except Exception:
            go = False
        if go and self.on_settings:
            try:
                self.on_settings()
            except Exception:
                pass

    def _toggle_pause(self):
        """切换运行/停止状态（默认是「未启动」）"""
        if self._paused:                      # 即将「开始」→ 先过守卫
            ok, why = self._start_allowed()
            if not ok:
                self._refuse_start(why)
                return
        self._paused = not self._paused
        if self._paused:
            self._pause_btn.config(text="开始")
            self._status_label.config(text="状态：未启动")
            self.set_banner("未启动。点「开始」运行，或点「设置」重新配置。", level="warn")
            if self.on_pause:
                self.on_pause()
        else:
            self._pause_btn.config(text="停止")
            self._status_label.config(text="状态：运行中")
            # 清掉"未启动"横幅；若目标软件不在，扫描线程下一轮会用红字顶回来
            self.set_banner("")
            if self.on_resume:
                self.on_resume()

    def toggle_pause(self):
        """公开的暂停切换方法（供全局热键调用）"""
        self._toggle_pause()

    def _switch_tab(self, tab: str):
        """切换标签页"""
        self._current_tab = tab
        # 选中的标签用"选中态"样式（靛蓝浅底 + 靛蓝字），一眼看出在看哪一页
        for name, btn in self._tab_buttons.items():
            btn.configure(style="SegmentOn.TButton" if name == tab
                          else "Segment.TButton")
        if tab == "待人工":
            self._list_frame.pack_forget()
            self._pending_frame.pack(fill=tk.BOTH, expand=True)
        else:
            self._pending_frame.pack_forget()
            self._list_frame.pack(fill=tk.BOTH, expand=True, pady=5)
            self._refresh_list()

    def _refresh_list(self):
        """刷新消息列表"""
        # 清空
        for item in self._tree.get_children():
            self._tree.delete(item)

        # 过滤
        filtered = self._records
        if self._current_tab == "已回复":
            filtered = [r for r in filtered if r.action == "replied"]

        # 添加
        for record in filtered:
            tag = record.action
            self._tree.insert(
                "", tk.END,
                values=(
                    record.timestamp,
                    record.customer,
                    record.message[:50] + ("..." if len(record.message) > 50 else ""),
                    self._get_action_text(record.action),
                ),
                tags=(tag,),
            )

    def _get_action_text(self, action: str) -> str:
        """把内部代号翻成人话（别把 escalate / no_reply 这种词漏给店主看）"""
        mapping = {
            "replied": "已自动回复",
            "auto_send": "已自动回复",
            "escalated": "已转人工",
            "escalate": "已转人工",
            "human_handle": "已转人工",
            "human_confirm": "待人工确认",
            "no_reply": "无需回复",
            "hold": "已回占位语",
            "greeting": "已发欢迎语",
        }
        return mapping.get(action, action)

    def _export_csv(self):
        """导出记录为 CSV"""
        if not self._records:
            messagebox.showinfo("导出", "没有记录可导出")
            return

        filename = f"records_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        filepath = os.path.join("data", filename)

        os.makedirs("data", exist_ok=True)

        with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["timestamp", "customer", "message", "action", "reply"])
            writer.writeheader()
            for record in self._records:
                writer.writerow(record.to_dict())

        messagebox.showinfo("导出", f"记录已导出到: {filepath}")

    def _clear_records(self):
        """清空记录"""
        if messagebox.askyesno("清空", "确定要清空所有记录吗？"):
            self._records.clear()
            self._stats = {
                "replied": 0,
                "escalated": 0,
            }
            self._refresh_list()
            self._update_stats()

    def _update_stats(self):
        """更新统计信息（本次会话；历史统计在「数据」看板里）"""
        self._stats_label.config(
            text=f"已回复 {self._stats['replied']}　待人工 {self._stats['escalated']}"
        )

    def _on_close(self):
        """关闭窗口"""
        import traceback
        import logging as _log
        if messagebox.askyesno("退出", "确定要退出吗？"):
            _stack = ''.join(traceback.format_stack())
            _log.getLogger(__name__).info(
                f"_on_close 被调用，调用栈:\n{_stack}")
            try:
                with open("logs/close_trace.log", "a", encoding="utf-8") as _f:
                    _f.write(f"\n=== _on_close called ===\n{_stack}\n")
                    _f.flush()
            except Exception:
                pass
            self._should_exit = True
            _log.getLogger(__name__).info("_on_close: 用户确认退出")
            self.root.destroy()
        else:
            _log.getLogger(__name__).info("_on_close: 用户取消退出")

    # ═══ 公开接口 ═══════════════════════════

    def add_record(self, customer: str, message: str, action: str,
                   reply: str = ""):
        """添加消息记录"""
        timestamp = datetime.now().strftime("%H:%M")
        record = MessageRecord(timestamp, customer, message, action, reply)
        self._records.append(record)

        # 更新统计
        if action in self._stats:
            self._stats[action] += 1

        # 刷新列表
        self._refresh_list()
        self._update_stats()

    def is_paused(self) -> bool:
        """是否暂停"""
        return self._paused

    def run(self):
        """运行主循环"""
        self.root.mainloop()

    def update(self):
        """更新界面（非阻塞）"""
        self.root.update_idletasks()
        self.root.update()

    # ── 待人工页 ───────────────────────────────────────────────────

    def set_pending_callbacks(self,
                              on_send: Callable = None,
                              on_edit: Callable = None,
                              on_ignore: Callable = None,
                              on_get_reply: Callable = None):
        """注册待人工页的动作回调。"""
        self._on_pending_send = on_send
        self._on_pending_edit = on_edit
        self._on_pending_ignore = on_ignore
        self._on_get_reply = on_get_reply

    def refresh_pending_queue(self, pending_items: list):
        """按 PendingItem 列表刷新待人工页，并在标签上显示条数。

        **一条消息一行**（不是一个人一行）：同一个人问了两件事就要处理两次。
        行定位用 ``item.key`` 而不是客户名 —— 否则同一个人的两行点哪行都一样。
        """
        for item in self._pending_tree.get_children():
            self._pending_tree.delete(item)

        # 同一个人有几条待处理 → 客户名后面标一下，免得以为重复了
        per_person = {}
        for item in pending_items:
            per_person[item.customer_name] = per_person.get(item.customer_name, 0) + 1

        for item in pending_items:
            ts = item.timestamp
            time_str = (datetime.fromtimestamp(ts).strftime("%H:%M")
                        if ts > 100000 else "")
            # 没草稿时要说清**为什么** —— 不然用户看到空白只会以为程序坏了。
            # （AI 答不上来时给的是内部信号"需要人工处理"，不会当草稿递过来）
            draft = (item.ai_reply or "").strip() or "（AI 没给草稿，点【编辑】自己写）"
            who = item.customer_name
            if per_person[item.customer_name] > 1:
                who = f"{who} [{per_person[item.customer_name]}条]"
            self._pending_tree.insert(
                "", tk.END,
                values=(
                    time_str,
                    who,
                    item.customer_message[:28]
                    + ("..." if len(item.customer_message) > 28 else ""),
                    draft[:44] + ("..." if len(draft) > 44 else ""),
                    f"{item.confidence:.0%}",
                    item.key,             # hidden: 动作时按它定位这一行
                ),
            )

        n = len(pending_items)
        people = len(per_person)
        extra = f"（{people} 个客户）" if people and people != n else ""
        self._pending_stats_label.config(
            text=f"共 {n} 条待人工{extra} | 选中后点【发送】直接发出，或点【编辑】改完再发")
        btn = self._tab_buttons.get("待人工")
        if btn is not None:
            btn.config(text=f"待人工 ({n})" if n else "待人工")

    def _selected_pending_key(self):
        """选中那一行的 key（``人::消息指纹``），界面动作全靠它。"""
        sel = self._pending_tree.selection()
        if not sel:
            messagebox.showinfo("待人工", "请先在列表里选中一条")
            return None
        return self._pending_tree.item(sel[0], "values")[5]

    def _handle_pending_send(self):
        key = self._selected_pending_key()
        if key and self._on_pending_send:
            self._on_pending_send(key)

    def _handle_pending_ignore(self):
        key = self._selected_pending_key()
        if key and self._on_pending_ignore:
            self._on_pending_ignore(key)

    def _handle_pending_edit(self):
        key = self._selected_pending_key()
        if not key:
            return
        current = ""
        if self._on_get_reply:
            current = self._on_get_reply(key) or ""
        item = self._pending_item_for(key) or {}
        customer = item.get("customer_name") or "客户"
        question = item.get("customer_message") or ""

        # 窗口本身在 gui/pending_edit.py 里 —— 抽出去才能测布局
        # （踩过：按钮最后 pack + 尺寸硬编码不缩放 → 高 DPI 下按钮被挤成 1px，
        #   店主反馈"编辑的框太小、下面两个按钮被吞了"）
        from gui.pending_edit import open_editor
        edited = open_editor(self.root, customer, question, current).wait()
        if edited and self._on_pending_edit:
            self._on_pending_edit(key, edited)

    def _pending_item_for(self, key: str) -> dict:
        """把 key 那一行的客户名/问题取回来（编辑窗要显示"客户问的是…"）。"""
        for row in self._pending_tree.get_children():
            vals = self._pending_tree.item(row, "values")
            if vals[5] == key:
                who = str(vals[1]).split(" [")[0]      # 去掉"[2条]"这种标注
                return {"customer_name": who,
                        "customer_message": str(vals[2]).rstrip(".")}
        return {}
