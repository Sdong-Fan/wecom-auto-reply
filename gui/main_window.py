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
                 on_settings: Callable = None, on_kb: Callable = None):
        self.on_pause = on_pause
        self.on_resume = on_resume
        # 「设置」常驻在头部，任何状态（未启动/运行中/已配置过）都能点开重配
        self.on_settings = on_settings
        # 「知识库」：编辑提示词 / 管理资料库（常驻，任何时候可点）
        self.on_kb = on_kb
        self._records: List[MessageRecord] = []
        # 默认**未启动**：打开程序不会自动开始扫描/回复，先让用户确认配置再点「开始」。
        # 已有的暂停机制本来就同时挡住两条通道（截图扫描与 API 轮询），所以直接复用。
        # 想开机就跑（或脚本化启动）：设环境变量 WECOM_AUTOSTART=1。
        import os as _os
        self._autostart = _os.getenv("WECOM_AUTOSTART", "0").strip() == "1"
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
        # 右上角定位
        sw = self.root.winfo_screenwidth()
        self.root.geometry(f"700x280+{sw - 720}+10")
        self.root.attributes('-topmost', True)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

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

    def _create_header(self):
        """创建顶部标题栏"""
        header = ttk.Frame(self.root)
        header.pack(fill=tk.X, padx=10, pady=5)

        title = ttk.Label(header, text="企业微信智能客服", font=("微软雅黑", 14, "bold"))
        title.pack(side=tk.LEFT)

        # 按钮
        btn_frame = ttk.Frame(header)
        btn_frame.pack(side=tk.RIGHT)

        self._pause_btn = ttk.Button(btn_frame,
                                     text="停止" if self._autostart else "开始",
                                     command=self._toggle_pause)
        self._pause_btn.pack(side=tk.LEFT, padx=5)

        # 设置按钮：常驻，任何时候都能重新配置（换软件 / 换 API key / 改模型）
        self._kb_btn = ttk.Button(btn_frame, text="知识库", command=self._on_kb_clicked)
        self._kb_btn.pack(side=tk.LEFT, padx=5)

        self._settings_btn = ttk.Button(btn_frame, text="设置", command=self._on_settings_clicked)
        self._settings_btn.pack(side=tk.LEFT, padx=5)

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
        self._banner = ttk.Label(self.root, text="", anchor="w",
                                 font=("微软雅黑", 10), foreground="#8a6d00",
                                 background="#fff8e1", padding=(8, 5))
        self._banner.pack(fill=tk.X, padx=10, pady=(0, 2))

    def set_banner(self, text: str, level: str = "warn"):
        """更新横幅。level: warn=黄 / error=红 / ok=绿（空文本＝隐藏）。"""
        colors = {
            "warn": ("#fff8e1", "#8a6d00"),
            "error": ("#fdecea", "#b3261e"),
            "ok": ("#e8f5e9", "#1b5e20"),
        }
        bg, fg = colors.get(level, colors["warn"])
        try:
            if not text:
                self._banner.pack_forget()
                return
            self._banner.config(text=text, background=bg, foreground=fg)
            self._banner.pack(fill=tk.X, padx=10, pady=(0, 2))
        except Exception:
            pass

    def _create_status_bar(self):
        """创建状态栏"""
        status_frame = ttk.Frame(self.root)
        status_frame.pack(fill=tk.X, padx=10, pady=5)

        self._status_label = ttk.Label(
            status_frame, text="状态: 运行中" if self._autostart else "状态: 未启动")
        self._status_label.pack(side=tk.LEFT)

        ttk.Separator(status_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)

        self._stats_label = ttk.Label(
            status_frame,
            text="已回复: 0 | 待人工: 0",
        )
        self._stats_label.pack(side=tk.LEFT)

    def _create_tabs(self):
        """创建标签页"""
        tab_frame = ttk.Frame(self.root)
        tab_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # 标签栏
        tab_bar = ttk.Frame(tab_frame)
        tab_bar.pack(fill=tk.X)

        self._tab_buttons = {}
        tabs = ["全部", "已回复", "待人工"]
        for tab in tabs:
            btn = ttk.Button(
                tab_bar, text=tab,
                command=lambda t=tab: self._switch_tab(t),
            )
            btn.pack(side=tk.LEFT, padx=2)
            self._tab_buttons[tab] = btn

        self._current_tab = "全部"

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
        footer = ttk.Frame(self.root)
        footer.pack(fill=tk.X, padx=10, pady=5)

        ttk.Button(footer, text="导出记录", command=self._export_csv).pack(side=tk.LEFT, padx=5)
        ttk.Button(footer, text="清空", command=self._clear_records).pack(side=tk.LEFT, padx=5)


    def _toggle_pause(self):
        """切换运行/停止状态（默认是「未启动」）"""
        self._paused = not self._paused
        if self._paused:
            self._pause_btn.config(text="开始")
            self._status_label.config(text="状态: 未启动")
            self.set_banner("未启动。点「开始」运行，或点「设置」重新配置。", level="warn")
            if self.on_pause:
                self.on_pause()
        else:
            self._pause_btn.config(text="停止")
            self._status_label.config(text="状态: 运行中")
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
        """获取动作文本"""
        mapping = {
            "replied": "已回复客户",
            "escalated": "已转人工",
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
        """更新统计信息"""
        self._stats_label.config(
            text=f"已回复: {self._stats['replied']} | "
                 f"待人工: {self._stats['escalated']}"
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

        dialog = tk.Toplevel(self.root)
        dialog.title(f"编辑回复 — {customer}")
        dialog.geometry("480x280")
        dialog.transient(self.root)
        dialog.grab_set()

        ttk.Label(dialog, text=f"客户: {customer}").pack(
            padx=10, pady=(10, 5), anchor="w")
        if question:
            ttk.Label(dialog, text=f"客户问的是: {question[:60]}",
                      foreground="#666").pack(padx=10, anchor="w")
        if current.strip():
            ttk.Label(dialog, text="AI 推荐回复（可修改后发送）:").pack(
                padx=10, anchor="w")
        else:
            # 空框会让用户以为是坏了 —— 说明白"这题资料里没有"
            ttk.Label(dialog,
                      text="AI 没给草稿（这个问题在资料库里找不到依据）。\n"
                           "自己写一句发出去，或者把它加进资料库（知识库 → 资料库 → 新增一条）。",
                      foreground="#8a6d00", justify="left").pack(
                padx=10, anchor="w")

        text_box = tk.Text(dialog, height=6, width=54, wrap=tk.WORD)
        text_box.pack(padx=10, pady=5, fill=tk.BOTH, expand=True)
        text_box.insert("1.0", current)
        text_box.focus_set()

        result = {"text": None}

        def on_confirm():
            result["text"] = text_box.get("1.0", tk.END).strip()
            dialog.destroy()

        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="发送", command=on_confirm).pack(
            side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="取消", command=dialog.destroy).pack(
            side=tk.LEFT, padx=5)

        self.root.wait_window(dialog)
        if result["text"] and self._on_pending_edit:
            self._on_pending_edit(key, result["text"])

    def _pending_item_for(self, key: str) -> dict:
        """把 key 那一行的客户名/问题取回来（编辑窗要显示"客户问的是…"）。"""
        for row in self._pending_tree.get_children():
            vals = self._pending_tree.item(row, "values")
            if vals[5] == key:
                who = str(vals[1]).split(" [")[0]      # 去掉"[2条]"这种标注
                return {"customer_name": who,
                        "customer_message": str(vals[2]).rstrip(".")}
        return {}
