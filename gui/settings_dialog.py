# gui/settings_dialog.py
"""设置面板：LLM 接口 + 选择软件 + 各模式附加字段 + 连通测试。

设计要点（按用户确认的方案）：
* 头部「设置」按钮**常驻**，任何时候都能点开重配，不受运行状态影响。
* LLM 接口：预设下拉 + 地址 + **模型名** + 密钥 + 「测试连接」。
  模型名必须可编辑 —— 各家模型名不一样，写死 deepseek-chat 换个地址就 400。
* 选择软件：三项（企业微信截图 / 企业微信 API / 微信 PC 截图）。
  微信 PC 还没标定 → 置灰并标注，不能选。
* 选 API 模式时才显示 corp_id / Secret / open_kfid，并给「测试企业微信连接」。
* 保存：密钥写 .env，软件选择写 config.json。
  **LLM 三项可以立即生效**（重载 .env + 重建 client）；
  **换软件需要重启**（channel/profile 在启动时读一次），所以给一个「立即重启」。

所有网络调用都放后台线程，绝不卡住 Tk 主循环。
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, Optional

from config import settings_store as store
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
        self.win.geometry("620x560")
        self.win.transient(parent)
        self.win.resizable(False, False)

        self._build()
        try:
            self.win.grab_set()
        except Exception:
            pass

    # ── 界面 ──────────────────────────────────────────────────────────

    def _build(self):
        pad = {"padx": 12, "pady": 4}

        # ══ LLM 接口 ══
        llm = ttk.LabelFrame(self.win, text="1. LLM 接口（用于生成回复）")
        llm.pack(fill=tk.X, **pad)

        row = ttk.Frame(llm)
        row.pack(fill=tk.X, padx=8, pady=4)
        ttk.Label(row, text="预设", width=10).pack(side=tk.LEFT)
        self._preset = ttk.Combobox(row, state="readonly",
                                    values=[p[0] for p in PRESETS], width=34)
        self._preset.pack(side=tk.LEFT)
        self._preset.bind("<<ComboboxSelected>>", self._on_preset)

        self._entries = {}
        for key, label, show in (
            ("llm_base_url", "接口地址", None),
            ("llm_model", "模型名", None),
            ("llm_api_key", "密钥", "•"),
        ):
            r = ttk.Frame(llm)
            r.pack(fill=tk.X, padx=8, pady=3)
            ttk.Label(r, text=label, width=10).pack(side=tk.LEFT)
            e = ttk.Entry(r, width=46, show=show)
            e.insert(0, self._cur.get(key, ""))
            e.pack(side=tk.LEFT, fill=tk.X, expand=True)
            self._entries[key] = e

        tip = ("提示：地址填到 /v1 为止，不要带 /chat/completions；"
               "模型名按服务商填（如 deepseek-chat / qwen-plus / moonshot-v1-8k）")
        ttk.Label(llm, text=tip, foreground="#666").pack(anchor="w", padx=10)

        trow = ttk.Frame(llm)
        trow.pack(fill=tk.X, padx=8, pady=6)
        ttk.Button(trow, text="测试连接", command=self._test_llm).pack(side=tk.LEFT)
        self._llm_status = ttk.Label(trow, text="", foreground="#666")
        self._llm_status.pack(side=tk.LEFT, padx=10)

        # ══ 选择软件 ══
        soft = ttk.LabelFrame(self.win, text="2. 选择要接的聊天软件")
        soft.pack(fill=tk.X, **pad)

        self._software = tk.StringVar(value=self._cur.get("software", "wecom_screenshot"))
        # 可选性按 profiles/<id>.json 是否存在动态决定：标定完微信 PC 就自动可点
        for sid, label, ok, note in store.software_options():
            r = ttk.Frame(soft)
            r.pack(fill=tk.X, padx=10, pady=2)
            rb = ttk.Radiobutton(r, text=label, value=sid, variable=self._software,
                                 command=self._on_software, state="normal" if ok else "disabled")
            rb.pack(side=tk.LEFT)
            if not ok:
                ttk.Label(r, text=f"（{note}）", foreground="#999").pack(side=tk.LEFT, padx=6)
        if not store.profile_available("wechat_pc"):
            # 已选中一个不可选项时给个兜底，免得面板打开就"选中了不能用的东西"
            if self._software.get() == "wechat_pc":
                self._software.set("wecom_screenshot")

        # ══ 企业微信 API 凭据（仅 API 模式显示）══
        self._api_frame = ttk.LabelFrame(self.win, text="3. 企业微信「微信客服」凭据")
        self._api_entries = {}
        for key, label, show in (
            ("wecom_corp_id", "企业 ID", None),
            ("wecom_kf_secret", "客服 Secret", "•"),
            ("wecom_open_kfid", "客服账号 ID", None),
        ):
            r = ttk.Frame(self._api_frame)
            r.pack(fill=tk.X, padx=8, pady=3)
            ttk.Label(r, text=label, width=12).pack(side=tk.LEFT)
            e = ttk.Entry(r, width=44, show=show)
            e.insert(0, self._cur.get(key, ""))
            e.pack(side=tk.LEFT, fill=tk.X, expand=True)
            self._api_entries[key] = e
        ttk.Label(self._api_frame,
                  text="位置：企业微信后台 → 应用管理 → 微信客服。"
                       "Secret 用【微信客服】那栏的，不是自建应用的。",
                  foreground="#666").pack(anchor="w", padx=10)
        arow = ttk.Frame(self._api_frame)
        arow.pack(fill=tk.X, padx=8, pady=6)
        ttk.Button(arow, text="测试企业微信连接", command=self._test_wecom).pack(side=tk.LEFT)
        self._api_status = ttk.Label(arow, text="", foreground="#666")
        self._api_status.pack(side=tk.LEFT, padx=10)

        # ══ 底部 ══
        bottom = ttk.Frame(self.win)
        bottom.pack(fill=tk.X, side=tk.BOTTOM, padx=12, pady=10)
        ttk.Button(bottom, text="取消", command=self.win.destroy).pack(side=tk.RIGHT, padx=6)
        ttk.Button(bottom, text="保存", command=self._save).pack(side=tk.RIGHT)

        self._restart_btn = ttk.Button(bottom, text="立即重启", command=self._restart,
                                       state="disabled")
        self._restart_btn.pack(side=tk.LEFT)
        self._saved_status = ttk.Label(bottom, text="", foreground="#1b5e20")
        self._saved_status.pack(side=tk.LEFT, padx=10)

        self._on_software()

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
        is_api = self._software.get() == "wecom_api"
        if is_api:
            self._api_frame.pack(fill=tk.X, padx=12, pady=4)
        else:
            self._api_frame.pack_forget()

    def _values(self) -> dict:
        v = {k: self._get(k) for k in self._entries}
        v.update({k: e.get().strip() for k, e in self._api_entries.items()})
        v["software"] = self._software.get()
        return v

    # ── 测试连接（后台线程，别卡界面）────────────────────────────────

    def _run_async(self, fn, label: ttk.Label, done_text="测试中…"):
        label.config(text=done_text, foreground="#666")

        def work():
            try:
                ok, detail = fn()
            except Exception as e:
                ok, detail = False, f"{type(e).__name__}: {e}"
            color = "#1b5e20" if ok else "#b3261e"

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
                                    foreground="#b3261e")
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
                text=f"已保存。换软件（→ {result['after']}）需要重启才生效，"
                     f"点「立即重启」或关掉窗口重新双击启动.bat。",
                foreground="#8a6d00")
            self._restart_btn.config(state="normal")
            logger.info(f"设置已保存：软件 {result['before']} → {result['after']}")
        else:
            self._saved_status.config(text=f"已保存。{applied}", foreground="#1b5e20")
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
