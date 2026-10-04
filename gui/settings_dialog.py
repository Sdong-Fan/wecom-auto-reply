# gui/settings_dialog.py
"""设置面板：LLM 接口 + 接入通道（接哪个聊天软件）+ 各模式附加字段 + 连通测试。

设计要点：
* 头部「设置」按钮**常驻**，任何时候都能点开重配，不受运行状态影响。
* LLM 接口：预设下拉 + 地址 + **模型名** + 密钥 + 「测试连接」。
  模型名必须可编辑 —— 各家模型名不一样，写死 deepseek-chat 换个地址就 400。
* 接入通道：**表里只有内置推荐项，不是"只支持这三种"**。profiles/ 目录里
  任何 JSON 都会被自动发现并出现在这里（config.settings_store.discover_profiles）；
  接新软件不用改代码，跑一次 scripts/calibrate_chat_app.py 生成 profile 即可。
* 选 API 模式时才显示 corp_id / Secret / open_kfid，并给「测试企业微信连接」。
* 保存：密钥写 .env，通道选择写 config.json。
  **LLM 三项可以立即生效**（重载 .env + 重建 client）；
  **换通道需要重启**（channel/profile 在启动时读一次），所以给一个「立即重启」。

界面约束（2026-10-04 用户反馈后加的）：
* 内容区**可滚动** + 窗口**可缩放**：原来固定 620x560 且不可缩放，
  选中「企业微信 API 模式」后凭据栏在窗口外面，用户根本看不到、也拉不出来。
* 所有尺寸走 theme.px()，字号走 theme.font()（像素字号），跟随 DPI 缩放。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, Optional

from config import settings_store as store
from gui.theme import (COLORS, SPACE, apply_theme, dialog_geometry, font, hint,
                       px, scroll_area)
from rag.llm_client import PRESETS

logger = logging.getLogger(__name__)

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def open_settings(parent: tk.Misc, cfg: dict, config_manager=None,
                  on_saved: Optional[Callable] = None) -> "SettingsDialog":
    """打开设置面板（模态）并返回它。"""
    dlg = SettingsDialog(parent, cfg, config_manager, on_saved)
    return dlg


class SettingsDialog:
    def __init__(self, parent: tk.Misc, cfg: dict, config_manager=None,
                 on_saved: Optional[Callable] = None):
        self.cfg = cfg
        self.config_manager = config_manager
        self.on_saved = on_saved
        self._cur = store.read_current_settings()

        self.win = tk.Toplevel(parent)
        self.win.title("设置")
        self.win.configure(background=COLORS["canvas_soft"])
        apply_theme(self.win)                     # 与主界面同一套设计系统
        self.win.geometry(dialog_geometry(self.win, 660, 640))
        # ★ 必须可缩放：内容比窗口高时用户至少能拉大（配合下面的滚动区）
        self.win.resizable(True, True)
        self.win.minsize(px(560), px(360))
        self.win.transient(parent)

        self._build()
        try:
            self.win.grab_set()
        except Exception:
            pass

    # ── 界面 ──────────────────────────────────────────────────────────

    def _build(self):
        # 底部操作条先建（固定不滚动），内容区再填充
        bottom = tk.Frame(self.win, background=COLORS["canvas"])
        bottom.pack(fill=tk.X, side=tk.BOTTOM)
        tk.Frame(bottom, height=1, background=COLORS["hairline"]).pack(fill=tk.X)
        brow = tk.Frame(bottom, background=COLORS["canvas"])
        brow.pack(fill=tk.X, padx=SPACE["lg"], pady=SPACE["md"])
        ttk.Button(brow, text="保存", style="Primary.TButton",
                   command=self._save).pack(side=tk.RIGHT)
        ttk.Button(brow, text="取消", style="Ghost.TButton",
                   command=self.win.destroy).pack(side=tk.RIGHT, padx=(0, SPACE["sm"]))
        self._restart_btn = ttk.Button(brow, text="立即重启", style="Secondary.TButton",
                                       command=self._restart, state="disabled")
        self._restart_btn.pack(side=tk.LEFT)
        self._saved_status = tk.Label(brow, text="", font=font("small"),
                                      background=COLORS["canvas"],
                                      foreground=COLORS["success"], anchor="w")
        self._saved_status.pack(side=tk.LEFT, padx=SPACE["md"])

        # 内容区：可滚动（内容超出窗口时也能看到底部）
        area, body = scroll_area(self.win)
        area.pack(fill=tk.BOTH, expand=True)

        self._build_llm(body)
        self._build_channel(body)
        self._build_api(body)
        hint(body, "改完点「保存」。LLM 三项立即生效；换接入通道需要重启"
                   "（点左下「立即重启」）。").pack(anchor="w",
                                                   padx=SPACE["lg"], pady=SPACE["md"])

    def _section(self, parent, title: str) -> tk.Frame:
        """一节：白底卡片 + 标题（比 ttk.LabelFrame 更贴合设计系统）。"""
        outer = tk.Frame(parent, background=COLORS["hairline"])
        outer.pack(fill=tk.X, padx=SPACE["lg"], pady=(SPACE["md"], 0))
        box = tk.Frame(outer, background=COLORS["canvas"])
        box.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)
        tk.Label(box, text=title, font=font("h3", True), background=COLORS["canvas"],
                 foreground=COLORS["ink"], anchor="w").pack(
            anchor="w", padx=SPACE["lg"], pady=(SPACE["md"], SPACE["xs"]))
        inner = tk.Frame(box, background=COLORS["canvas"])
        inner.pack(fill=tk.X, padx=SPACE["lg"], pady=(0, SPACE["md"]))
        inner._outer = outer          # 整节显示/隐藏时用（API 凭据栏）
        return inner

    def _field(self, parent, label: str, key: str, *, show=None,
               values=None, readonly=False, width_label: int = 11):
        row = tk.Frame(parent, background=COLORS["canvas"])
        row.pack(fill=tk.X, pady=px(3))
        tk.Label(row, text=label, width=width_label, anchor="w", font=font("body"),
                 background=COLORS["canvas"],
                 foreground=COLORS["ink_secondary"]).pack(side=tk.LEFT)
        if values is not None:
            w = ttk.Combobox(row, values=values, state="readonly" if readonly else "normal",
                             width=32)
            w.set(self._cur.get(key, ""))
        else:
            w = ttk.Entry(row, show=show)
            w.insert(0, self._cur.get(key, ""))
        w.pack(side=tk.LEFT, fill=tk.X, expand=True)
        return w

    def _build_llm(self, body):
        box = self._section(body, "1. LLM 接口（生成回复用）")
        prow = tk.Frame(box, background=COLORS["canvas"])
        prow.pack(fill=tk.X, pady=px(3))
        tk.Label(prow, text="预设", width=11, anchor="w", font=font("body"),
                 background=COLORS["canvas"],
                 foreground=COLORS["ink_secondary"]).pack(side=tk.LEFT)
        # ★ 父容器必须是 prow。写成 ttk.Combobox(box) + pack(in_=prow) 时，
        #   Tk 把下拉框的几何算在 box 里、层叠顺序又低于后来 pack 的 prow，
        #   结果是**文字和下拉箭头被 prow 的底色盖掉**，只剩一个空框
        #   （实测：winfo_ismapped=1、get() 有值，但框内深色像素数 = 0）。
        self._preset = ttk.Combobox(prow, state="readonly",
                                    values=[p[0] for p in PRESETS], width=32)
        self._preset.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._preset.bind("<<ComboboxSelected>>", self._on_preset)
        # 预选：按当前接口地址匹配预设，匹配不上就选“自定义”
        cur_base = (self._cur.get("llm_base_url") or "").rstrip("/")
        picked = ""
        for pname, base, _model in PRESETS:
            if base and base.rstrip("/") == cur_base:
                picked = pname
                break
        self._preset.set(picked or (PRESETS[0][0] if PRESETS else ""))

        self._entries = {}
        for key, label, show in (("llm_base_url", "接口地址", None),
                                 ("llm_model", "模型名", None),
                                 ("llm_api_key", "密钥", "•")):
            self._entries[key] = self._field(box, label, key, show=show)

        hint(box, "地址填到 /v1 为止，不要带 /chat/completions；"
                  "模型名按服务商填（deepseek-chat / qwen-plus / moonshot-v1-8k…）").pack(
            anchor="w", pady=(px(4), 0))
        # 密钥到底从哪读的 —— 用户问过"我这密钥是哪来的"，直接把文件路径摆出来
        env_path = store.ENV_PATH
        hint(box, f"配置保存在：{env_path}"
                  + ("" if env_path.exists() else "（还没有这个文件，填完点「保存」会生成）")
             ).pack(anchor="w")
        trow = tk.Frame(box, background=COLORS["canvas"])
        trow.pack(fill=tk.X, pady=(SPACE["sm"], 0))
        ttk.Button(trow, text="测试连接", style="Secondary.TButton",
                   command=self._test_llm).pack(side=tk.LEFT)
        self._llm_status = tk.Label(trow, text="", font=font("small"),
                                    background=COLORS["canvas"],
                                    foreground=COLORS["ink_mute"])
        self._llm_status.pack(side=tk.LEFT, padx=SPACE["md"])

    def _build_channel(self, body):
        """接入通道：内置项 + **自动发现的 profile**（接新软件不用改代码）。"""
        box = self._section(body, "2. 接入通道（接哪个聊天软件）")
        self._channel_box = tk.Frame(box, background=COLORS["canvas"])
        self._channel_box.pack(fill=tk.X)

        bar = tk.Frame(box, background=COLORS["canvas"])
        bar.pack(fill=tk.X, pady=(SPACE["sm"], 0))
        ttk.Button(bar, text="怎么接新软件？", style="Ghost.TButton",
                   command=self._how_to_add).pack(side=tk.LEFT)
        ttk.Button(bar, text="刷新列表 ↻", style="Ghost.TButton",
                   command=self._reload_channels).pack(side=tk.LEFT, padx=SPACE["xs"])
        self._channel_note = hint(bar, "")
        self._channel_note.pack(side=tk.LEFT, padx=SPACE["md"])

        self._render_channels()

    def _render_channels(self):
        """按 profiles/ 现状重建单选项（标定完新软件点「刷新列表」就出现）。"""
        for child in self._channel_box.winfo_children():
            child.destroy()
        self._software = tk.StringVar(value=self._cur.get("software", "wecom_screenshot"))
        options = store.software_options()
        for sid, label, ok, note in options:
            r = tk.Frame(self._channel_box, background=COLORS["canvas"])
            r.pack(fill=tk.X, pady=px(2))
            rb = ttk.Radiobutton(r, text=label, value=sid, variable=self._software,
                                 command=self._on_software,
                                 state="normal" if ok else "disabled")
            rb.pack(side=tk.LEFT)
            if not ok:
                tk.Label(r, text=f"（{note}）", font=font("small"),
                         background=COLORS["canvas"],
                         foreground=COLORS["ink_faint"]).pack(side=tk.LEFT, padx=SPACE["sm"])
        # 选中的是不可用项 → 退回第一个可用项（免得面板打开就是"选中了不能用的"）
        avail = [sid for sid, _l, ok, _n in options if ok]
        if self._software.get() not in avail and avail:
            self._software.set(avail[0])
        custom = [o for o in options if o[0].startswith("profile:")]
        self._channel_note.configure(
            text=(f"已发现 {len(custom)} 个自定义软件" if custom
                  else "接新软件不用改代码：标定一次就会出现在这里"))
        self._on_software()

    def _reload_channels(self):
        """重新扫 profiles/ 目录（刚标定完不用重启程序）。"""
        self._cur["software"] = self._software.get() if hasattr(self, "_software") else ""
        self._render_channels()

    def _how_to_add(self):
        messagebox.showinfo(
            "怎么接别的聊天软件",
            "本程序用「标定文件」接不同软件，不需要改代码：\n\n"
            "1) 打开你要接的软件（钉钉 / 飞书 / 微信 / 企业微信…）\n"
            "2) 在本项目目录执行：\n"
            "     python scripts/calibrate_chat_app.py\n"
            "   按提示点两下（选窗口、框聊天区），它会生成\n"
            "     profiles/<名字>.json\n"
            "3) 回到这个面板点「刷新列表 ↻」，新软件就会出现并可选\n"
            "4) 保存 → 点「立即重启」生效\n\n"
            "原理：采集层（截图 / 红点 / 气泡 / OCR）读的都是 profile 里的坐标与\n"
            "窗口类名，决策层完全不用动。所以「接更多软件」只是多一个 profile 文件。",
            parent=self.win)

    def _build_api(self, body):
        """企业微信「微信客服」凭据（只有选 API 模式时显示）。"""
        self._api_frame = self._section(body, "3. 企业微信「微信客服」凭据")
        self._api_outer = self._api_frame._outer
        self._api_entries = {}
        for key, label, show in (("wecom_corp_id", "企业 ID", None),
                                 ("wecom_kf_secret", "客服 Secret", "•"),
                                 ("wecom_open_kfid", "客服账号 ID", None)):
            self._api_entries[key] = self._field(self._api_frame, label, key, show=show)
        hint(self._api_frame,
             "位置：企业微信后台 → 应用管理 → 微信客服。Secret 用【微信客服】那栏的，"
             "不是自建应用的。").pack(anchor="w", pady=(px(4), 0))
        arow = tk.Frame(self._api_frame, background=COLORS["canvas"])
        arow.pack(fill=tk.X, pady=(SPACE["sm"], 0))
        ttk.Button(arow, text="测试企业微信连接", style="Secondary.TButton",
                   command=self._test_wecom).pack(side=tk.LEFT)
        self._api_status = tk.Label(arow, text="", font=font("small"),
                                    background=COLORS["canvas"],
                                    foreground=COLORS["ink_mute"])
        self._api_status.pack(side=tk.LEFT, padx=SPACE["md"])
        self._on_software()           # 建完这一节再同步显示状态

    # ── 交互 ──────────────────────────────────────────────────────────

    def _on_preset(self, _evt=None):
        name = self._preset.get()
        for pname, base, model in PRESETS:
            if pname == name:
                if base:   # "自定义" 的 base 为空，不动用户已填的内容
                    self._set("llm_base_url", base)
                    self._set("llm_model", model)
                return

    def _set(self, key, value):
        e = self._entries[key]
        e.delete(0, tk.END)
        e.insert(0, value)

    def _get(self, key):
        return self._entries[key].get().strip()

    def _on_software(self):
        """选了 API 模式才显示凭据栏（内容区可滚动，不会看不到）。"""
        if not hasattr(self, "_api_outer"):
            return                    # 通道那节先建，此时凭据节还没建
        if self._software.get() == "wecom_api":
            self._api_outer.pack(fill=tk.X, padx=SPACE["lg"], pady=(SPACE["md"], 0))
        else:
            self._api_outer.pack_forget()

    def _values(self) -> dict:
        v = {k: self._get(k) for k in self._entries}
        v.update({k: e.get().strip() for k, e in self._api_entries.items()})
        v["software"] = self._software.get()
        return v

    # ── 测试连接（后台线程，别卡界面）────────────────────────────────

    def _run_async(self, fn, label: tk.Label, done_text="测试中…"):
        """后台跑一个测试函数，把 (ok, 说明) 填到 label 上。

        ★ `fn` 可能是**协程函数**（`llm_client.ping` 就是 async），直接 `fn()`
        拿到的是 coroutine 对象，`ok, detail = fn()` 会抛
        `TypeError: cannot unpack non-iterable coroutine object` ——
        而且协程从未被 await，等于测试根本没跑。所以这里统一识别 awaitable。
        """
        label.config(text=done_text, foreground=COLORS["ink_mute"])

        def work():
            try:
                result = fn()
                if inspect.isawaitable(result):
                    result = asyncio.run(result)
                ok, detail = result
            except Exception as e:
                ok, detail = False, f"{type(e).__name__}: {e}"
            color = COLORS["success"] if ok else COLORS["danger"]

            def apply():
                try:
                    label.config(text=("✓ " if ok else "✗ ") + detail, foreground=color)
                except Exception:
                    pass
            try:
                label.after(0, apply)
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _test_llm(self):
        vals = self._values()
        # 测试用的是输入框里的值，不是磁盘上的 —— 用户可能还没保存
        os.environ["LLM_API_KEY"] = vals["llm_api_key"]
        os.environ["LLM_BASE_URL"] = vals["llm_base_url"]
        os.environ["LLM_MODEL"] = vals["llm_model"]
        from rag import llm_client
        llm_client.reset_client()
        self._run_async(llm_client.ping, self._llm_status)

    def _test_wecom(self):
        vals = self._values()
        if not (vals["wecom_corp_id"] and vals["wecom_kf_secret"]):
            self._api_status.config(text="✗ 先填企业 ID 和客服 Secret",
                                    foreground=COLORS["danger"])
            return

        def run():
            from gateway.wecom_kf import WeComKFClient
            cli = WeComKFClient(corp_id=vals["wecom_corp_id"],
                                secret=vals["wecom_kf_secret"],
                                open_kfid=vals["wecom_open_kfid"],
                                state_path="data/state/wecom_kf.json")
            return cli.selftest()

        self._run_async(run, self._api_status)

    # ── 保存 ──────────────────────────────────────────────────────────

    def _save(self):
        vals = self._values()
        if not vals["llm_api_key"]:
            if not messagebox.askyesno("还没填密钥",
                                       "LLM 接口密钥是空的，程序将无法生成回复。\n"
                                       "仍要保存吗？"):
                return
        try:
            result = store.save_settings(vals)
        except Exception as e:
            messagebox.showerror("保存失败", f"{type(e).__name__}: {e}")
            return

        # LLM 三项立即生效：重载 .env + 重建 client（不用重启）
        try:
            from dotenv import load_dotenv
            load_dotenv(store.ENV_PATH, override=True)
            from rag import llm_client
            llm_client.reset_client()
            applied = "LLM 设置已立即生效。"
        except Exception as e:
            applied = f"（LLM 设置需重启才生效：{e}）"

        if result["software_changed"]:
            self._saved_status.config(
                text=f"已保存。换通道（→ {result['after']}）需要重启才生效，"
                     f"点「立即重启」或关掉窗口重新双击启动.bat。",
                foreground=COLORS["warning"])
            self._restart_btn.config(state="normal")
            logger.info(f"设置已保存：通道 {result['before']} → {result['after']}")
        else:
            self._saved_status.config(text=f"已保存。{applied}",
                                      foreground=COLORS["success"])
        if self.on_saved:
            try:
                self.on_saved()
            except Exception:
                pass

    def _restart(self):
        """关掉当前程序并重新启动它。

        先起一个独立的 cmd，它等 3 秒（让本进程把 Qdrant 文件锁放掉）再跑 启动.bat。
        """
        if not messagebox.askyesno("重启", "现在重启程序？界面会关闭后重新打开。"):
            return
        bat = os.path.join(HERE, "启动.bat")
        try:
            if os.path.exists(bat):
                subprocess.Popen(
                    f'ping -n 4 127.0.0.1 >nul & start "" "{bat}"',
                    shell=True, cwd=HERE,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                subprocess.Popen([sys.executable, os.path.join(HERE, "main.py")],
                                 cwd=HERE,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            logger.info("用户点了「立即重启」，正在退出当前进程")
        except Exception as e:
            messagebox.showerror("重启失败", f"{e}\n请手动关掉窗口重新双击「启动.bat」")
            return
        try:
            self.win.destroy()
        except Exception:
            pass
        os._exit(0)
