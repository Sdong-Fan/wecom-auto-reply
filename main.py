#!/usr/bin/env python3
"""企业微信智能客服 — 协调器

职责：启动 → mainloop 驱动 → 后台线程扫描 → 调度 → 退出
所有参数由 config.json 驱动。
"""

import asyncio
import hashlib
import logging
import os
import queue
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

# ═══ 日志配置必须在所有 import 之前，否则 basicConfig 会被忽略 ═══
os.makedirs("logs", exist_ok=True)
LOG_PATH = os.path.abspath("logs/monitor.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
    force=True,  # 强制覆盖任何已有配置
)
log = logging.getLogger(__name__)

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("HF_HUB_OFFLINE", "1")  # skip HF network verification (~10s delay)
from dotenv import load_dotenv


def _app_dir() -> Path:
    """程序目录：打包后是 exe 所在目录，源码模式是项目根目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _can_start() -> tuple[bool, str]:
    """能不能点「开始」——返回 (是否允许, 不允许的原因)。

    ★ **没配模型接口就不让开始**（使用者实测反馈后定的口径）：
    以为"没填 Key 就等于完全没动作"，其实它会照常回客户 ——
    时间类问题直接答、越界请求按模板婉拒、**每个答不了的问题都会回一句
    "稍等，我帮您确认一下"**。要是没人盯「待人工」，客户就是被许了个不会来的回复。
    与其发一堆空头承诺，不如拦下来先让他配好。
    """
    try:
        from rag.llm_client import resolve_config
        key, _base, _model = resolve_config()
    except Exception:
        return True, ""          # 判不出来就别把人锁在门外
    if key:
        return True, ""
    return False, (
        "还没配模型接口，不能开始。\n\n"
        "没配的话，业务问题全都答不了；但客户仍会收到一句"
        "「稍等，我帮您确认一下」—— 没人处理「待人工」就等于替老板许了个空头承诺。\n\n"
        "请先在「设置」里填模型接口（任意 OpenAI 兼容服务，DeepSeek 等都可以）。")


# ★ **必须指定路径**，不能写 `load_dotenv()`：
#   无参时 python-dotenv 会从调用者所在目录**一路往上找** `.env`，
#   于是把程序解压到别人的项目目录里（或程序目录本身在某个有 .env 的目录下）时，
#   会静默读走那个**不相干的 .env** ——
#   实测：把分发包解压到一个带 .env 的目录里，界面密钥框直接显示了别人的密钥，
#   机器人还会拿那把密钥去调接口。只认自己目录下的 .env，没有就当没配。
_ENV_FILE = _app_dir() / ".env"
# override=True：.env 是本程序的配置文件，应当以它为准。
# 不加的话，如果环境里已经有一个空的 LLM_API_KEY（比如设置面板测试时写过），
# .env 里真正的密钥反而读不进来。
load_dotenv(_ENV_FILE, override=True)

import ctypes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

# 注意: torch 必须在 paddle 之前导入，否则 DLL 冲突
from rag.responder import Responder
from rag.human_fallback import PendingQueue
from rag import unanswered
from config.manager import ConfigManager
from wxbot.scanner import Scanner
from wxbot.detector import MessageDetector
from wxbot.scan_policy import parse_whitelist, row_eligibility
from wxbot.new_message_tracker import (NewMessageTracker, resolve_only_new_messages,
                                       row_fp, row_key)
from debug.visualizer import DebugVisualizer
from gui.main_window import MainWindow

# PaddleOCR 导入会修改 root logger 级别为 WARNING，需要恢复
logging.getLogger().setLevel(logging.INFO)


def main():
    """Entry point — tkinter mainloop drives the event loop."""
    try:
        _main_impl()
    except KeyboardInterrupt:
        log.info("停止 (Ctrl+C)")
    except Exception as e:
        log.critical(f"致命异常，进程退出: {e}\n{traceback.format_exc()}")
        raise


def _main_impl():
    # 初始化模块
    config = ConfigManager()
    cfg = config.config

    # DPI 感知必须在建任何窗口/抓图之前开，否则 150% 缩放下拿到的窗口坐标是
    # 虚拟化的（1346x1152 而不是 2019x1728），裁剪和点击全错。
    try:
        from wxbot.native_win import ensure_dpi_aware, check_layout
        ensure_dpi_aware()
    except Exception as e:
        log.warning(f"DPI 感知初始化失败: {e}")
        check_layout = None

    # ── 截图模式：套用软件 profile ──────────────────────────────
    # profile 决定"窗口怎么找、区域在哪、我方气泡什么颜色"，由
    # scripts/calibrate_chat_app.py 自动标定。config.json 里 profile 为空
    # 或文件不存在时，完全沿用 config.json 原来的值（与改造前一致）。
    profile_id = str(cfg.get("profile", "") or "")
    if profile_id:
        from wxbot.profile import apply_to_config, load_profile
        prof = load_profile(profile_id)
        if prof:
            cfg = apply_to_config(cfg, prof)
            log.info(f"已套用 profile: {prof.get('name', profile_id)}"
                     f"（窗口类={prof.get('window', {}).get('window_class')}，"
                     f"聊天区起点={prof.get('layout', {}).get('chat_x')}）")
        else:
            log.warning(f"profile '{profile_id}' 没找到，沿用 config.json 原有布局参数")

    scanner = Scanner(cfg)
    detector = MessageDetector(cfg)

    # 客户白名单：None = 不启用（企业微信用 @微信 后缀规则就够）；
    # 空集合 = 谁都不点（自定义软件的**安全默认**）。
    # 这里算一次，**红点路径与兜底路径共用**：两条路都会点开会话，
    # 少拦一条就等于没拦（点开会话＝清掉对方未读，不可逆）。
    whitelist = parse_whitelist(cfg.get("customers", {}))
    # 红点规则是按企业微信调的（扫列表最左 5%）；微信个人号里红头像会被误判成未读，
    # profile 可以把它关掉，只靠兜底扫描 + 白名单。
    use_red_dot = bool(cfg.get("detection", {}).get("red_dot", {}).get("enabled", True))
    # 「只处理点开始之后收到的新消息」：自定义软件（微信个人号）走这条，
    # 见 wxbot/new_message_tracker.py。企业微信不启用 —— 它有自己的红点+@微信后缀规则。
    only_new_messages = resolve_only_new_messages(cfg)
    new_tracker = NewMessageTracker(enabled=only_new_messages)
    if whitelist is not None:
        log.info(f"客户白名单已启用：{sorted(whitelist) if whitelist else '（空 —— 不会点开任何会话）'}")
    # 采集方式自检：config 里 col1_width_px/col2_width_px 是写死的像素边界，
    # 这里用像素锚点量一遍实际边界，对不上就打警告（只报不改）。
    if check_layout is not None and scanner._native:
        try:
            full = scanner._grab_window_cached()
            if full is not None:
                chat_left = (scanner._col1_px or 0) + (scanner._col2_px or 0)
                warn = check_layout(full, chat_left)
                if warn:
                    log.warning(warn)
                else:
                    log.info(f"采集: PrintWindow 不抢前台，窗口={full.size}，"
                             f"面板边界自检通过（col1+col2={chat_left}）")
        except Exception as e:
            log.debug(f"面板边界自检失败: {e}")

    # 知识库是本地文件模式（Qdrant local），同一时刻只能被一个进程打开。
    # 重复双击 启动.bat 时，第二个实例会在这里抛 RuntimeError —— 直接给出
    # 人话提示，而不是丢一段吓人的 CRITICAL 堆栈。
    try:
        responder = Responder(scanner, config=cfg)
    except RuntimeError as e:
        if "already accessed" in str(e):
            log.critical(
                "启动失败：知识库 data/qdrant 已被另一个实例占用。\n"
                "  → 程序很可能已经在运行了，先看看有没有已打开的"
                "「企业微信智能客服」窗口。\n"
                "  → 如果窗口已关闭但进程还在，用任务管理器结束所有 "
                "python.exe 后重试。")
            raise SystemExit(1)
        raise

    # 调试可视化
    debug_cfg = cfg.get("debug", {})
    visualizer = DebugVisualizer(
        output_dir=debug_cfg.get("output_dir", "debug"),
        max_history=debug_cfg.get("max_history", 10),
    )
    save_debug = debug_cfg.get("save_screenshots", True)

    # 主控窗口
    def _open_settings():
        """点「设置」打开配置面板：LLM 预设/模型名 + 软件选择 + 企业微信 API 凭据 + 连通测试。

        按钮常驻，配置完之后也能随时点开重配；LLM 三项保存后立即生效，
        换软件需要重启（面板里有「立即重启」）。
        """
        try:
            from gui.settings_dialog import open_settings
            # 用 window.root 而不是 root：root 变量在本文件后面才赋值，
            # 虽然闭包调用时已经存在，但少一层依赖更稳
            open_settings(window.root, cfg, config, on_saved=_after_settings_saved)
        except Exception as e:
            log.error(f"打开设置面板失败: {e}\n{traceback.format_exc()}")
            window.set_banner(f"设置面板打开失败：{e}", level="error")

    def _after_settings_saved():
        llm_summary = ""
        try:
            from rag.llm_client import describe_config
            llm_summary = f"当前 LLM：{describe_config()}"
        except Exception:
            pass
        window.set_banner(f"设置已保存。{llm_summary}", level="ok")

    def _open_kb():
        """点「知识库」打开提示词/资料库面板。"""
        try:
            from gui.kb_dialog import open_kb
            # 复用主程序已打开的 Qdrant 客户端（本地文件模式不能开第二个）
            open_kb(window.root, cfg, on_saved=_after_kb_saved,
                    qdrant=getattr(responder, "qdrant", None))
        except Exception as e:
            log.error(f"打开知识库面板失败: {e}\n{traceback.format_exc()}")
            window.set_banner(f"知识库面板打开失败：{e}", level="error")

    def _open_dashboard():
        """点「数据」打开运营看板（只读聚合本机日志，不发送、不修改数据）。"""
        try:
            from gui.dashboard import DashboardWindow
            from rag.retriever import active_collection
            window._dashboard = DashboardWindow(
                window.root,
                qdrant=getattr(responder, "qdrant", None),
                collection=active_collection(),
                cfg=cfg)
        except Exception as e:
            log.error(f"打开看板失败: {e}\n{traceback.format_exc()}")
            window.set_banner(f"看板打开失败：{e}", level="error")

    def _after_kb_saved(key):
        window.set_banner(f"「{key}」提示词已保存，立即生效。", level="ok")

    window = MainWindow(on_settings=_open_settings, on_kb=_open_kb,
                        on_dashboard=_open_dashboard, can_start=_can_start)

    # 待人工队列：置信度不足的消息连同 AI 草稿一起放这里，
    # 在 GUI「待人工」页可以选择直接发送或编辑后发送
    pending_queue = PendingQueue()

    # 线程安全发送队列：所有发送（直发 / 占位语 / 待人工发送）统一入队
    send_queue: queue.Queue[tuple[str, str, str]] = queue.Queue()

    # 改稿学习的勾选窗状态：同一时刻只开一个，后面的先排队
    _learn_state = {"busy": False, "queue": []}

    # ── 通道选择 ───────────────────────────────────────────────
    # screenshot  : 截图模式（默认）—— 抓企业微信桌面窗口，OCR 读消息，键盘发送
    # wecom_api   : API 模式 —— 企业微信「微信客服」官方接口，不需要桌面端、不截图
    # 两种模式共用同一套 RAG / guard / 待人工队列，只有"读消息"和"发消息"两头不同。
    channel_mode = str(cfg.get("channel", "screenshot")).lower()
    api_channel = None
    api_poll_interval = float(cfg.get("api", {}).get("wecom", {})
                              .get("poll_interval_seconds", 3))
    if channel_mode == "wecom_api":
        from gateway.wecom_kf import WeComKFClient
        api_channel = WeComKFClient.from_config(cfg)
        ok, detail = api_channel.selftest()
        if not ok:
            log.critical(f"API 模式启动失败：{detail}\n"
                         f"  → 先在 .env 里填好 WECOM_CORP_ID / WECOM_KF_SECRET / "
                         f"WECOM_OPEN_KFID，然后跑：\n"
                         f"     .venv\\Scripts\\python.exe scripts\\wecom_api_selftest.py\n"
                         f"  → 想先用截图模式就把 config.json 的 channel 改回 \"screenshot\"。")
            raise SystemExit(1)
        log.info(f"通道: 企业微信微信客服 API —— {detail}")
    else:
        log.info("通道: 截图模式（企业微信桌面端）")

    # 启动横幅：默认未启动，直接告诉用户下一步做什么（不是静默什么都不干）
    if channel_mode == "wecom_api":
        window.set_banner("未启动。当前：企业微信 · API 模式。"
                          "点「开始」运行，或点「设置」重新配置。", level="warn")
    else:
        window.set_banner("未启动。当前：企业微信 · 截图模式。"
                          "点「开始」运行，或点「设置」重新配置。", level="warn")

    # ★ 没配模型接口时的提示（守卫本体是模块级的 _can_start，见文件开头）
    #
    # ⚠️ 这里**不能写 `root.after(...)`**：`root` 是 `_main_impl` 的局部变量，
    #    要到后面 `root = tk.Tk()` 才赋值；在赋值前引用它（哪怕在闭包里）
    #    会直接 UnboundLocalError —— 实测打包版一启动就崩在第 311 行。
    #    前面那句 window.set_banner 是同步调用，所以这里也直接调用即可。
    def _check_llm_ready():
        ok, _why = _can_start()
        if ok:
            return
        log.warning("未配置模型接口（.env 里没有可用的 LLM_API_KEY）："
                    "「开始」会被拦下 —— 否则客户会收到占位语却没人处理。")
        window.set_banner(
            "⚠️ 还没配模型接口 —— 点「开始」会被拦下。"
            "请点「设置」填 API Key（任意 OpenAI 兼容服务）。", level="error")

    _check_llm_ready()

    def _refresh_pending_tab():
        """刷新 GUI 的待人工页。"""
        window.refresh_pending_queue(pending_queue.get_all())

    def _do_send(customer_name: str, reply_text: str) -> bool:
        """实际发送。通道感知：api 模式走企业微信微信客服官方接口
        （customer_name 就是 external_userid）；截图模式切前台 + 粘贴 + 回车。"""
        if api_channel is not None:
            try:
                api_channel.send_text(customer_name, reply_text)
                log.info(f"[发送·API] {customer_name}: {reply_text[:80]}")
                return True
            except Exception as e:
                log.error(f"[发送失败·API] {customer_name}: {e}")
                return False
        try:
            # ★ 发送前必须先把目标会话切到打开状态并**校验**。
            # 发送路径是"点输入框→粘贴→回车"，只作用于**当前打开的那个会话**：
            # 从待人工页点发送、或自动回复与扫描线程抢跑时，当前开着的可能是别人，
            # 那样 A 的回复就会发到 B 那里。宁可不发，也不能发错人。
            #
            # **失败要重试一次**：会话列表会因为新消息重新排序，第一次找错行很正常。
            # 实测踩过：没重试 → 一次没确认上就彻底放弃，客户那边一直没收到，
            # 看起来就是"很久才回复"（其实是根本没回）。
            opened = False
            for attempt in (1, 2):
                if scanner.open_conversation(customer_name, detector.read_row_name):
                    opened = True
                    break
                log.warning(f"[发送] 第 {attempt} 次没能确认 {customer_name!r} 的会话"
                            f"（列表可能正在重排），稍后重试")
                if attempt == 1:
                    time.sleep(1.2)          # 等列表稳定下来再找一遍
            if not opened:
                log.error(f"[发送中止] 两次都没能确认 {customer_name!r} 的会话"
                          f" —— 拒绝发送，避免发错人")
                msg = (f"发送已中止：连续两次都没能确认当前打开的是「{customer_name}」"
                       f"的会话。请手动切到该会话，或检查会话名是否被 OCR 读错。")
                root.after(0, lambda m=msg: window.set_banner(m, level="error"))
                return False
            sent = False
            for attempt in (1, 2):
                scanner._switch_to_wecom_and_back(
                    lambda: scanner.send_message_via_keyboard(reply_text))
                sent = _confirm_screenshot_sent()
                if sent:
                    break
                log.warning(f"[发送] 第 {attempt} 次发出后聊天区里没看到这条回复"
                            f"（粘贴或回车可能没生效），重试一次")
                time.sleep(0.6)
            if not sent:
                log.error(f"[发送失败] {customer_name}: 两次都没确认发出")
                msg = (f"给「{customer_name}」的回复**可能没发出去** —— "
                       f"粘贴或回车没生效。请手动看下会话。")
                root.after(0, lambda m=msg: window.set_banner(m, level="error"))
                return False
            log.info(f"[发送] {customer_name}: {reply_text[:80]}")
            return True
        except Exception as e:
            log.error(f"[发送失败] {customer_name}: {e}")
            return False

    def _confirm_screenshot_sent() -> bool:
        """截图模式：发完顺带看一眼聊天区，确认这条真的出去了。

        实现放在模块级 ``_verify_screenshot_sent`` 里 —— 闭包里没法测。
        """
        return _verify_screenshot_sent(scanner, detector, conf_bubble)

    def _process_send_queue_tick():
        """Process ONE send queue item per tick to avoid blocking GUI.

        ctypes clipboard + keyboard input takes ~300ms.
        Processing multiple items in a loop would freeze the UI.

        队列项可能是 3 元组 ``(mode, 客户, 文本)``，也可能是 4/5 元组 ——
        第 4 项是"这次是人工改过的"上下文（客户原话 + AI 草稿），
        只有它能触发学习（见 rag/learn.py）；
        第 5 项是**日志上下文**（客户原话 + 护栏判定 + decision_path + source），
        只用于落盘给运营看板统计，不参与业务判断。
        """
        try:
            row = send_queue.get_nowait()
            mode, customer_name, reply_text = row[0], row[1], row[2]
            learn_ctx = row[3] if len(row) > 3 else None
            log_ctx = row[4] if len(row) > 4 else None
            log.debug(f"发送队列处理: mode={mode} customer={customer_name}")

            # ★ 第一次跟这个客户说话 → 先招呼一句，正文放回队列下一轮发。
            # 分两轮是为了不让界面一次卡住两倍时间（发送是在 Tk 线程里跑的）。
            if greet_first_contact(customer_name, cfg, _do_send,
                                   log_send=responder.log_send_result):
                send_queue.put(row)
                root.after(500, _process_send_queue_tick)
                return

            send_ok = _do_send(customer_name, reply_text)
            # Log send result AFTER actual send (not optimistically)
            responder.log_send_result(customer_name, reply_text, send_ok,
                                      **(log_ctx or {}))
            if send_ok:
                root.after(0,
                    lambda: window.add_record(
                        customer_name, "", "replied", reply_text))
                # ★ 学习挂在"真的发出去"之后：没发出去的话不该被学走
                if learn_ctx:
                    if learn_ctx.get("direct_confirm"):
                        _record_direct_confirm(learn_ctx, reply_text)
                    else:
                        _learn_from_edit(learn_ctx, reply_text)
        except queue.Empty:
            pass
        root.after(500, _process_send_queue_tick)

    def _record_direct_confirm(ctx: dict, sent_text: str):
        """待人工页直接发送了 AI 草稿 → 把「客户问题 + 回答」收进「学到的」待确认。

        收进去的是**问答对**，不是语气样本 —— 点「采纳」就进资料库，
        下次同一个问题检索分就高了，不用再转人工。（学 AI 自己写的语气 = 越学越偏。）
        """
        try:
            from rag.learn import record_direct_send
            got = record_direct_send(ctx.get("question", ""), sent_text, cfg=cfg)
        except Exception as e:
            log.warning(f"记录直发确认失败（不影响发送）: {e}")
            return
        if got:
            root.after(0, lambda: window.set_banner(
                "这条问答已放进「学到的」待确认 —— 点「采纳进资料库」，"
                "下次同样的问题就能直接回答。", level="ok"))
            log.info(f"直发确认已收录: {ctx.get('question', '')[:30]}")
        else:
            log.info("直发确认没收录（学习关着，或内容太短）")

    def _learn_from_edit(ctx: dict, sent_text: str):
        """人工改完发出去 → **先分析出候选，弹窗让用户勾**，确认了才写。

        分析要调 LLM（好几秒），所以放后台线程；弹窗必须在 Tk 线程，用 root.after 回来。
        """
        # 同一时刻只开一个勾选窗；后面来的先排队，等这个处理完再弹
        learn_queue: list = _learn_state["queue"]
        if _learn_state["busy"]:
            log.info("勾选窗已开着，这次候选先排队")
            learn_queue.append((ctx, sent_text))
            return

        def work():
            try:
                from rag.learn import propose
                p = propose(ctx.get("question", ""), ctx.get("draft", ""),
                            sent_text, cfg=cfg)
            except Exception as e:
                log.warning(f"分析改稿失败（不影响发送）: {e}")
                return
            root.after(0, lambda: _offer_learn(p, ctx, sent_text))

        import threading
        threading.Thread(target=work, daemon=True).start()

    def _offer_learn(proposal: dict, ctx: dict, sent_text: str):
        """在 Tk 线程里弹出勾选窗。"""
        if not proposal.get("ok"):
            reason = proposal.get("skipped") or ""
            if reason and reason not in ("没改动，不学", "学习已关闭"):
                window.set_banner(f"这次改动没学到东西：{reason}", level="warn")
            log.info(f"不学习：{reason}")
            _learn_state["busy"] = False
            _drain_learn_queue()
            return
        _learn_state["busy"] = True

        def done(keys):
            _learn_state["busy"] = False
            if keys:
                _commit_learn(proposal, keys)
            else:
                window.set_banner("这次改动没有学习（你取消了）", level="warn")
                log.info("用户取消学习")
            _drain_learn_queue()

        try:
            from gui.learn_dialog import open_review
            open_review(window.root, proposal, done)
        except Exception as e:
            log.error(f"打不开勾选窗: {e}")
            _learn_state["busy"] = False
            _drain_learn_queue()

    def _commit_learn(proposal: dict, keys):
        """写入选中的候选。入库要跑嵌入，所以还是后台线程。"""
        from rag.learn import commit
        window.set_banner("正在学习…（写资料库要跑嵌入，稍等几秒）", level="warn")

        def work():
            res = commit(proposal, keys, cfg=cfg,
                         qdrant=getattr(responder, "qdrant", None))
            parts = []
            if res["rules"]:
                parts.append(f"{res['rules']} 条语气习惯")
            if res["tones"]:
                parts.append(f"{res['tones']} 条口吻样本")
            if res["facts"]:
                parts.append(f"{res['facts']} 条新说法（已写进资料库）")
            if res["fact_errors"]:
                parts.append(f"{len(res['fact_errors'])} 条入库失败（已放进待确认）")
            msg = ("已学到：" + "，".join(parts)) if parts else "这次没有写入任何东西"
            level = "warn" if res["fact_errors"] else "ok"
            root.after(0, lambda: window.set_banner(msg, level=level))
            log.info(f"学习完成: {res}")

        import threading
        threading.Thread(target=work, daemon=True).start()

    def _drain_learn_queue():
        q = _learn_state["queue"]
        if _learn_state["busy"] or not q:
            return
        ctx, sent = q.pop(0)
        _learn_from_edit(ctx, sent)

    # ── 待人工页的动作 ─────────────────────────────────────────────

    def _pending_item(item_key):
        """界面传过来的是**那一行的 key**（``人::消息指纹``）。

        兼容老的按人名的调用：key 找不到就当成客户名，取他最新的一条。
        """
        item = pending_queue.get_key(item_key)
        if item is None:
            item = pending_queue.get(item_key)
        return item

    def _handle_pending_send(item_key):
        """直接发送 AI 推荐回复。

        **两道拦截**（都踩过坑）：
        1. 草稿是"需要人工处理"这种内部信号 → 拒绝发送。它只是给程序看的标记，
           发给客户等于什么都没说（老板点过一次，客户真收到了这六个字）。
        2. 没有草稿 → 拒绝，并告诉他点「编辑」自己写。

        **只记采纳次数，不学语气**：学自己的草稿会自我强化、越跑越偏。
        """
        from rag.responder import is_human_marker

        item = _pending_item(item_key)
        if item is None:
            log.warning(f"待人工发送：找不到这一条 {item_key!r}（可能刚被处理过）")
            _refresh_pending_tab()
            return
        who = item.customer_name
        draft = (item.ai_reply or "")
        if not draft.strip() or is_human_marker(draft):
            window.set_banner(
                f"这条没给草稿：AI 认为资料里没有能回答「{who}」这个问题的内容。"
                f"点「编辑」自己写一句，或把它加进资料库。", level="warn")
            log.warning(f"待人工发送被拦（无有效草稿）: {who} draft={draft!r}")
            return

        pending_queue.remove_key(item.key)
        # 带上"这是直发确认"的上下文：发送**成功之后**再把这个问答对收进「学到的」
        # （发失败就不该算店主认可过）
        ctx = {"direct_confirm": True,
               "question": item.customer_message or "",
               "draft": item.ai_reply or ""}
        # 第 5 项：日志上下文（看板要区分"人工直发草稿"和"机器人自动答"）
        log_ctx = {"customer_message": item.customer_message or "",
                   "guard_decision": item.guard_decision or "",
                   "decision_path": "人工直发草稿",
                   "source": "human_confirm"}
        send_queue.put(("send", who, item.ai_reply, ctx, log_ctx))
        try:
            from rag import learn_store
            learn_store.bump_accept(item.customer_message, item.ai_reply)
        except Exception as e:
            log.warning(f"记录采纳次数失败: {e}")
        log.info(f"待人工发送: {who}: {item.ai_reply[:50]}")
        _refresh_pending_tab()

    def _handle_pending_edit(item_key, edited_text=None):
        """发送编辑后的回复。

        队列项带着"客户原话 + AI 草稿"，这样发送成功后才知道
        **人把草稿改成什么样了** —— 那是唯一值得学的东西（见 rag/learn.py）。
        """
        if edited_text is None:
            return
        item = _pending_item(item_key)
        if item is None:
            log.warning(f"待人工编辑发送：找不到这一条 {item_key!r}")
            _refresh_pending_tab()
            return
        pending_queue.remove_key(item.key)
        ctx = {"question": item.customer_message or "",
               "draft": item.ai_reply or ""}
        log_ctx = {"customer_message": item.customer_message or "",
                   "guard_decision": item.guard_decision or "",
                   "decision_path": "人工改稿后发出",
                   "source": "human_edit"}
        send_queue.put(("send", item.customer_name, edited_text, ctx, log_ctx))
        log.info(f"待人工编辑发送: {item.customer_name}: {edited_text[:50]}")
        _refresh_pending_tab()

    def _get_pending_reply(item_key):
        """取草稿全文，供编辑对话框预填。"""
        item = _pending_item(item_key)
        return item.ai_reply if item else ""

    def _handle_pending_ignore(item_key):
        item = _pending_item(item_key)
        if item is not None:
            pending_queue.remove_key(item.key)
        _refresh_pending_tab()

    window.set_pending_callbacks(
        on_send=_handle_pending_send,
        on_edit=_handle_pending_edit,
        on_ignore=_handle_pending_ignore,
        on_get_reply=_get_pending_reply,
    )

    # --- 读取配置 ---
    CHECK = cfg.get("check_interval_seconds", 3)

    # 裁切参数
    crop = cfg.get("crop", {})
    name_row = crop.get("name_row", {})
    row_top_offset = name_row.get("top_offset", -30)
    row_bot_offset = name_row.get("bottom_offset", 110)

    # OCR 置信度
    occ = cfg.get("ocr_confidence", {})
    conf_name = occ.get("name_detection", 0.05)
    conf_bubble = occ.get("bubble_text", 0.15)

    # 时序
    tmg = cfg.get("timing", {})
    t_after_click_row = tmg.get("after_click_row_delay", 1.2)
    t_after_scroll = tmg.get("after_scroll_delay", 0.5)

    # 兜底
    fallback = cfg.get("fallback", {})

    log.info(f"WeCom Auto Reply 启动 (日志: {LOG_PATH})")

    root = window.root

    def _cleanup():
        """Close Qdrant on exit."""
        try:
            responder.close()
            log.info("Qdrant 客户端已关闭")
        except Exception:
            pass
        log.info("清理完成")

    # ── background scan thread ─────────────────────────────────────

    _scan_lock = threading.Lock()
    _api_lock = threading.Lock()
    _seen_msgids: set = set()
    # 最近欢迎过的客户：进会话事件会重复来，同一个人短时间内只招呼一次
    _welcomed: dict = {}

    def scan_tick():
        """Schedule one poll/scan in a background thread, repeat on a timer."""
        target = _run_api_poll if api_channel is not None else _run_scan
        threading.Thread(target=target, daemon=True).start()
        root.after(CHECK * 1000, scan_tick)

    async def _handle_api_message(m):
        """一条 API 客户消息 → 同一套 RAG/分流。策略在 gateway.api_policy 里（可测）。"""
        from gateway.api_policy import dispatch_api_message
        uid = m.external_userid
        try:
            nick = await asyncio.to_thread(api_channel.display_name, uid)
        except Exception:
            nick = uid
        note = "（语音转写）" if getattr(m, "from_voice", False) else ""
        log.info(f">>> API 客户消息 [{nick}] {uid}{note}: {m.text[:60]!r}")
        await dispatch_api_message(
            responder, uid, m.text, send_queue, pending_queue,
            refresh_pending=lambda: root.after(0, _refresh_pending_tab),
            display_name=nick)

    async def _handle_api_nontext(uid: str, items: list):
        """客户发来**不是文字**的东西（图片/语音/文件/位置…）。

        原来整个丢掉 —— 客户发张器材照片问"这个有吗"，一个字都收不到。
        现在：回一句得体的话 + 转人工（店主去微信客服后台看图/听语音）。
        策略在 gateway.api_policy 里（可测），这边只负责取昵称和刷界面。
        """
        from gateway.api_policy import dispatch_api_nontext
        from rag import prompt_store as ps
        try:
            acks = ps.parse_nontext_acks()
        except Exception:
            acks = {}
        try:
            nick = await asyncio.to_thread(api_channel.display_name, uid)
        except Exception:
            nick = uid
        await dispatch_api_nontext(
            uid, items, send_queue, pending_queue, acks=acks,
            refresh_pending=lambda: root.after(0, _refresh_pending_tab),
            display_name=nick)

    async def _handle_enter_session(m):
        """客户进入会话 → 发欢迎语。

        这是 API 模式独有的机会（截图模式看不到"刚进来"这个动作）。
        事件里的 ``code`` 一次性且很快过期，所以拉到就发。
        同一个人短时间内重复进来只欢迎一次 —— 不然客户来回切一次就被招呼一次。
        """
        from gateway.api_policy import should_welcome
        w = cfg.get("welcome", {}) or {}
        enabled = bool(w.get("on_enter_session", True))
        cooldown = float(w.get("cooldown_seconds", 21600))
        uid = m.external_userid
        if not should_welcome(uid, _welcomed, cooldown, enabled=enabled,
                              code=m.event_code):
            if enabled and not m.event_code:
                log.debug("进入会话事件没带 code，跳过欢迎语")
            return
        try:
            from rag import prompt_store as ps
            text = ps.get("welcome").strip()
        except Exception:
            text = ""
        if not text:
            return
        try:
            await asyncio.to_thread(api_channel.send_welcome, m.event_code, text)
            _welcomed[uid] = time.time()
            # 记进"招呼过"的共用名单：第一次回复时就不会再招呼一遍
            try:
                from wxbot import first_contact
                first_contact.mark_greeted(uid)
            except Exception:
                pass
            log.info(f"已发欢迎语 → {uid}")
        except Exception as e:
            log.warning(f"发欢迎语失败（事件 code 可能已过期）: {e}")

    def _run_api_poll():
        """API 模式的取件循环。

        去重按 **msgid**（服务端给的唯一 ID）——比截图模式按文本指纹准：
        客户连发两句一样的话不会互相吞掉。
        """
        if not _api_lock.acquire(blocking=False):
            log.debug("上一轮 API 轮询未完成，跳过")
            return
        try:
            if window.is_paused():
                return
            config.reload()
            msgs = api_channel.sync_msg()
            nontext: dict = {}
            for m in msgs:
                if not m.msgid or m.msgid in _seen_msgids:
                    continue
                _seen_msgids.add(m.msgid)
                if m.is_event:
                    asyncio.run(_handle_enter_session(m))
                    continue
                if not m.has_text:
                    # 攒起来，等这一批拉完按客户合并处理（连发 3 张图只回一次）
                    nontext.setdefault(m.external_userid, []).append(m)
                    continue
                # 有可用文字就走正常问答（语音转写出来的也算）
                asyncio.run(_handle_api_message(m))
            for uid, items in nontext.items():
                asyncio.run(_handle_api_nontext(uid, items))
            if len(_seen_msgids) > 5000:
                _seen_msgids.clear()
        except Exception as e:
            log.error(f"API 轮询异常: {e}\n{traceback.format_exc()}")
        finally:
            _api_lock.release()

    def _set_banner(text: str, level: str = "warn"):
        """从后台线程更新界面横幅（Tk 只能在主线程碰，所以走 root.after）。"""
        try:
            root.after(0, lambda: window.set_banner(text, level))
        except Exception:
            pass

    _last_missing_warn = [0.0]

    def _notify_target_missing():
        """目标软件没找到时的提示：横幅常显，日志按分钟节流（免得刷爆）。"""
        names = "、".join(scanner._process_names) or "企业微信"
        _set_banner(f"未找到目标软件窗口（{names}）—— 请打开并登录它，程序才能读消息。"
                    f"当前通道：{channel_mode}", level="error")
        now = time.time()
        if now - _last_missing_warn[0] > 60:
            _last_missing_warn[0] = now
            log.warning(f"未找到目标软件窗口（{names}），本轮不扫描。"
                        f"请在设置里确认选的软件，或把它打开并登录。")

    def _run_scan():
        """Background thread: scan, OCR, RAG, dispatch."""
        if not _scan_lock.acquire(blocking=False):
            log.debug("上一轮扫描未完成，跳过")
            return
        try:
            if window.is_paused():
                return

            config.reload()

            if save_debug:
                visualizer.start_session()

            # ══ 1. 窗口 ══
            # 窗口必须存在且可见（否则无窗口可截）。
            # 但"聚焦失败"不应中断扫描：截图是按窗口矩形取屏幕像素，
            # 不需要窗口位于前台。只有发送（Ctrl+V + Enter）才要求前台，
            # 由 _switch_to_wecom_and_back 单独负责。
            if not scanner.is_running():
                # 原来是裸 return —— 界面还写着"运行中"、日志一个字都没有，
                # 用户完全不知道为什么不回消息。现在：日志按分钟节流 + 横幅红字提示。
                _notify_target_missing()
                return
            if not scanner.focus():
                log.debug("聚焦失败，继续扫描（窗口可见即可截图）")

            # ══ 2. 聊天列表截图 + 去重 ══
            c2_img = scanner.capture_chat_list()
            if c2_img is None:
                return
            if detector.col2_seen(c2_img):
                return

            # ══ 3. 红点检测 ══
            # 红点规则是按企业微信调的（扫会话列表最左侧 5%）。微信个人号里
            # 红头像/红图标会被误判成"有未读"，所以 profile 可以关掉这条路。
            reds = detector.detect_red_dots(c2_img) if use_red_dot else []
            if reds:
                log.info(f"红点: {len(reds)}个, Y={reds}")

            if save_debug:
                visualizer.save_chat_list(c2_img, reds)

            # ══ 4. 预过滤: 找到有@微信的客户红点 ══
            replied = False
            customer_red_y = None
            customer_name = "客户"

            if reds:
                cw2 = c2_img.size[0]
                for ry in reversed(reds):
                    # Skip recently processed y-positions to avoid
                    # expensive OCR re-scan every 3s after send failure
                    if not detector.is_clickable(ry, timeout=15):
                        log.info(f"红点y={ry} 冷却中，跳过")
                        continue
                    row_img = c2_img.crop(
                        (0, max(0, ry + row_top_offset), cw2,
                         ry + row_bot_offset))
                    name_crop, name_text = detector.read_row_name(row_img)
                    if name_crop is None:
                        log.info(f"红点y={ry} 未检测到名字区域")
                        continue
                    is_cust = detector.is_customer_name(name_crop, name_text)
                    log.info(f"红点y={ry} 对话名: [{name_text[:50]}] "
                             f"客户={is_cust}")

                    if save_debug:
                        visualizer.save_name_crop(name_crop, name_text)

                    # 白名单同样要拦这条路 —— 否则自定义软件里"红点头像"
                    # 会绕过白名单被点开（点开＝清掉对方未读，不可逆）。
                    cand_key = detector.clean_name_text(name_text) if name_text else ""
                    ok_to_open, why = row_eligibility(
                        is_cust, cand_key, whitelist=whitelist,
                        in_cooldown=bool(cand_key)
                        and detector.is_in_cooldown(cand_key))
                    if not ok_to_open:
                        if why == "not_in_whitelist":
                            log.info(f"红点y={ry} {cand_key!r} 不在客户白名单里，跳过（不点开）")
                        detector.mark_clicked(ry)
                        continue

                    customer_red_y = ry
                    customer_name = cand_key or "客户"
                    break

                if customer_red_y is None:
                    log.info("红点均非客户对话")
                else:
                    # ══ 5. 点击客户红点 ══
                    chat_fp = scanner.chat_fingerprint()
                    if not scanner.click_col2_row(customer_red_y):
                        log.warning("col2 点击失败，跳过")
                        return
                    detector.mark_clicked(customer_red_y)
                    # 自适应等待：渲染完就继续，最坏情况下等满 t_after_click_row
                    waited = scanner.wait_chat_update(chat_fp, t_after_click_row)
                    log.debug(f"点击后等待渲染 {waited:.2f}s")

                    # ★ 确认打开的就是这个客户再往下走。
                    # 列表会因为新消息**重新排序**，也可能点了没生效、聊天区没刷新；
                    # 这时读到的内容是**别人**的，回复就会发错人（真发生过）。
                    verdict = scanner.confirm_opened(
                        customer_name, detector.read_row_name,
                        retry_click=lambda: scanner.click_col2_row(customer_red_y))
                    if verdict == "mismatch":
                        log.warning(f"点开 y={customer_red_y} 后聊天区不是 "
                                    f"{customer_name!r}，跳过这一行（不读、不回）")
                        detector.mark_clicked(customer_red_y, timeout=30)
                        return

                    # ══ 6. 聊天区截图 ══
                    scanner.scroll_to_bottom()
                    time.sleep(t_after_scroll)

                    chat_img = scanner.capture_chat_area()
                    if chat_img is None:
                        return

                    if save_debug:
                        visualizer.save_chat_area(chat_img)

                    # ══ 7. 气泡扫描 ══
                    unreplied, has_blue, replied_gray = \
                        detector.extract_bubbles_detail(chat_img)
                    # ★ 「人工回过也算回过」：蓝泡（我们发的，或店主手动发的）
                    # **之上**的客户气泡＝已经有人回过了，把文字记下来。
                    # 否则界面一旦读到旧内容（列表重排/没刷新/重启），
                    # 这些老问题会被当成新消息再回一遍。
                    _mark_already_replied(detector, customer_name, replied_gray)
                    if not unreplied:
                        log.info("无气泡/已回复")
                    else:
                        # ══ 8. 逐条气泡 OCR + 过滤掉已经回过的 ══
                        # ★ 为什么要逐条：整段合并文本只要多读进一个新气泡就变了，
                        # "旧气泡 + 一个新气泡"会被当成全新消息，把老问题再回一遍
                        # （实测 客户B 那句 21:45 回过，21:59 又回了一次）。
                        bubble_texts = [
                            detector.extract_text(b, min_conf=conf_bubble).strip()
                            for b, _ in unreplied]
                        bubble_texts = [t for t in bubble_texts if t]
                        if not bubble_texts:
                            # ★ 气泡在、一个字都读不出来 → 图片/语音/文件。
                            # 以前这里写"OCR空"就 continue 了，客户等于石沉大海。
                            _screenshot_nontext(send_queue, pending_queue,
                                                customer_name, len(unreplied))
                            detector.mark_clicked(customer_red_y, timeout=30)
                            return
                        fresh = detector.unseen_texts(customer_name, bubble_texts)
                        if not fresh:
                            log.info(f"气泡都已回复过（{len(bubble_texts)} 条），跳过")
                            detector.mark_clicked(customer_red_y, timeout=30)
                            return
                        text = " ".join(fresh).strip()
                        if len(text) < 2:
                            # 读出来的太短（比如语音气泡的"3″"）→ 也当非文本
                            _screenshot_nontext(send_queue, pending_queue,
                                                customer_name, len(unreplied))
                            detector.mark_clicked(customer_red_y, timeout=30)
                            return
                        else:
                            log.info(f"OCR: {text[:120]}")

                            if save_debug:
                                visualizer.save_ocr_result(chat_img, text)
                                visualizer.update_meta(
                                    unreplied=[text], is_customer=True)

                            # ══ 9. 客户判定 ══
                            # If red dot path already confirmed customer,
                            # skip bubble is_customer check (which can fail
                            # when bubble nick OCR doesn't match pattern)
                            bn = unreplied[0][1]
                            if customer_name not in ("", "客户") or detector.is_customer(bn, has_blue, text):
                                log.info(">>> 客户消息")
                                if detector.has_stop_keyword(text):
                                    log.info("客户要求停止回复，跳过")
                                    detector.mark_message_seen(customer_name, text)
                                    if save_debug:
                                        visualizer.set_action("stop_reply")
                                elif detector.contains_own_reply(text):
                                    log.info("OCR包含自己回复，跳过(蓝泡检测失败)")
                                    detector.mark_message_seen(customer_name, text)
                                    if save_debug:
                                        visualizer.set_action("stop_reply")
                                elif detector.is_message_seen(customer_name, text,
                                                             long_term=False):
                                    log.info("消息已处理过(去重)")
                                    if save_debug:
                                        visualizer.set_action("dedup")
                                elif detector.is_in_cooldown(customer_name):
                                    log.info("冷却中")
                                    if save_debug:
                                        visualizer.set_action("cooldown")
                                else:
                                    # ══ 10. RAG 回复 ══
                                    result = asyncio.run(
                                        responder.handle_customer_message(
                                            customer_name, [text]))
                                    # 逐条气泡也记一遍已见（下次才能把旧气泡滤掉）
                                    detector.mark_messages_seen(customer_name,
                                                                bubble_texts)
                                    if result.dispatch_level == "no_reply":
                                        # 「该不该答」判断层：收尾语/感谢 → 不回、不转人工、不弹窗。
                                        # 只标记已见，避免机器人对"谢谢/好的"刷屏。
                                        detector.mark_message_seen(customer_name, text)
                                        log.info(
                                            f"无需回复: {customer_name} - {result.reason}")
                                        if save_debug:
                                            visualizer.set_action(
                                                "no_reply", result.reason)
                                        root.after(0,
                                            lambda: window.add_record(
                                                customer_name, text, "no_reply",
                                                result.reason))
                                    elif result.success and result.dispatch_level == "auto_send":
                                        # Unified send path: all sends go through
                                        # send_queue → _do_send → _switch_to_wecom_and_back
                                        # UI update happens in _process_send_queue_tick
                                        # after _do_send confirms success
                                        # ★ 第 5 项是"日志上下文"：客户原话 + 护栏判定，
                                        #   发给 log_send_result 落盘（看板要用，见 docs/看板-PRD.md）
                                        send_queue.put((
                                            "send", customer_name, result.reply_text,
                                            None,
                                            {"customer_message": text,
                                             "guard_decision": result.guard_decision,
                                             "guard_reason": result.guard_reason,
                                             "decision_path": result.decision_path,
                                             "source": "auto"}))
                                        detector.mark_replied(customer_name)
                                        detector.mark_message_seen(customer_name, text)
                                        detector.mark_text_sent(result.reply_text)
                                        detector.clear_seen()
                                        replied = True
                                        if save_debug:
                                            visualizer.set_action(
                                                "replied", result.reply_text)
                                    else:
                                        # 真正升级才标记已见；技术失败(如发送失败)允许重试
                                        if result.escalated:
                                            detector.mark_message_seen(customer_name, text)
                                            # ★ 转人工 = 资料库缺这条。记进排行，
                                            # 店主就能照单补资料库（而不是天天救火）。
                                            unanswered.record(
                                                text,
                                                result.reason
                                                or result.guard_decision,
                                                customer_name)
                                            # 转人工：自动回一句礼貌占位语，让客户稍等
                                            if result.hold_text:
                                                send_queue.put(
                                                    ("send", customer_name,
                                                     result.hold_text, None,
                                                     {"customer_message": text,
                                                      "guard_decision": result.guard_decision,
                                                      "guard_reason": result.guard_reason,
                                                      "decision_path": result.decision_path,
                                                      "source": "hold"}))
                                                detector.mark_replied(customer_name)
                                            # 连同 AI 草稿放进「待人工」，等人工发送/编辑
                                            pending_queue.push(
                                                customer_name, text,
                                                result.reply_text,
                                                result.retrieval_score,
                                                result.guard_decision
                                                or result.reason)
                                            root.after(0, _refresh_pending_tab)
                                            log.info(
                                                f"转人工: {customer_name} "
                                                f"- {result.reason}")
                                        if save_debug:
                                            visualizer.set_action(
                                                "escalated", result.reason)
                                        root.after(0,
                                            lambda: window.add_record(
                                                customer_name, text, "escalated",
                                                result.reason))
                            else:
                                log.info("非客户")
                                if save_debug:
                                    visualizer.update_meta(is_customer=False)

            # ══ 11. 兜底：无回复时扫前N条 ══
            # 冷却改成按客户记（在 _scan_fallback 内跳过刚回过话的那个客户），
            # 不再用全局 30 秒冷却把整轮兜底挡掉 —— 那样会漏掉同一时间窗里的 B。
            if not replied:
                found, fb_text, fb_name, fb_bubbles = asyncio.run(
                    _scan_fallback(scanner, detector, cfg,
                                   whitelist=whitelist,
                                   new_tracker=new_tracker,
                                   only_new_messages=only_new_messages))
                fb_cust = (fb_name or customer_name)
                # Use customer_name for dedup (main path's cleaned name),
                # not fb_name which may differ due to OCR at different Y.
                if found and not detector.is_message_seen(customer_name, fb_text,
                                                          long_term=False):
                    result = asyncio.run(
                        responder.handle_customer_message(
                            fb_cust, [fb_text]))
                    # 逐条气泡也记一遍：下次 OCR 只要多读进一个新气泡，整段文本就变了，
                    # 只有逐条记才能把"已经回过的旧气泡"滤掉（不然同一个问题会回两遍）
                    detector.mark_messages_seen(fb_cust, fb_bubbles)
                    if result.dispatch_level == "no_reply":
                        # 收尾语/感谢 → 不回、不转人工，只标记已见
                        detector.mark_message_seen(fb_cust, fb_text)
                        log.info(f"无需回复(兜底): {fb_cust} - {result.reason}")
                    elif result.success and result.dispatch_level == "auto_send":
                        # Unified send path
                        send_queue.put(("send", fb_cust, result.reply_text))
                        detector.mark_replied(fb_cust)
                        detector.mark_message_seen(fb_cust, fb_text)
                        detector.mark_text_sent(result.reply_text)
                        detector.clear_seen()
                    elif result.escalated:
                        detector.mark_message_seen(fb_cust, fb_text)
                        unanswered.record(fb_text,
                                          result.reason
                                          or result.guard_decision,
                                          fb_cust)
                        # 转人工：自动回一句礼貌占位语，让客户稍等
                        if result.hold_text:
                            send_queue.put(("send", fb_cust, result.hold_text))
                            detector.mark_replied(fb_cust)
                        # 连同 AI 草稿放进「待人工」
                        pending_queue.push(
                            fb_cust, fb_text, result.reply_text,
                            result.retrieval_score,
                            result.guard_decision or result.reason)
                        root.after(0, _refresh_pending_tab)
                        log.info(f"转人工(兜底): {fb_cust} - {result.reason}")

            if save_debug:
                visualizer.finish()

        except Exception as e:
            log.error(f"扫描异常: {e}\n{traceback.format_exc()}")
        finally:
            _scan_lock.release()

    # ── exit detection ─────────────────────────────────────────────

    def _check_exit():
        """Check for exit flag every 100ms on main thread."""
        if window._should_exit:
            _cleanup()
            root.destroy()
            return
        root.after(100, _check_exit)

    # ── start everything ───────────────────────────────────────────

    root.after(CHECK * 1000, scan_tick)
    root.after(500, _process_send_queue_tick)
    root.after(100, _check_exit)

    # 启动时先把磁盘上的待人工条目渲染出来（PendingQueue 会从
    # data/state/pending_queue.json 恢复，GUI 不会自动显示）
    _refresh_pending_tab()

    root.mainloop()
    log.info("已退出")


async def _scan_fallback(scanner: Scanner, detector: MessageDetector,
                         cfg: dict, whitelist=None, new_tracker=None,
                         only_new_messages: bool = False):
    """兜底扫描：扫 col2 前 N 个条目找客户对话。

    ``whitelist`` / ``new_tracker`` / ``only_new_messages`` 由调用方（``_main_impl``）
    传进来 —— 这个函数是**模块级**的，看不到 ``_main_impl`` 的局部变量。
    （踩过：直接在函数体里用那几个名字 → 每轮扫描都 NameError，表现成"点了开始没反应"。）
    """
    if new_tracker is None:
        new_tracker = NewMessageTracker(enabled=only_new_messages)
    c2 = scanner.get_col2_region()
    if not c2:
        return False, "", None

    c2_img = scanner.capture_chat_list()
    if c2_img is None:
        return False, "", None

    # --- 从 config 读取所有参数 ---
    fallback_cfg = cfg.get("fallback", {})
    # 起始 y 优先用**绝对像素**（profile 标定出来的），比例是给旧配置兜底的。
    # 比例会随窗口高度飘；实测微信上就是靠比例算 → 每行偏 4px、第 5 行偏 29px →
    # 读到的名字和实际行不对应（这条 bug 直接导致过"回复错会话"）。
    if fallback_cfg.get("start_y_px"):
        start_y = int(fallback_cfg["start_y_px"])
    else:
        start_y = int((c2[3] if len(c2) > 3 else 650)
                      * fallback_cfg.get("start_y_ratio", 0.09))
    row_h = fallback_cfg.get("row_height_px", 55)
    max_rows = fallback_cfg.get("max_scan_rows", 3)

    crop_cfg = cfg.get("crop", {}).get("name_row", {})
    row_top = crop_cfg.get("top_offset", -30)
    row_bot = crop_cfg.get("bottom_offset", 110)
    # 行裁剪的横向范围：给自定义软件留出"跳过左侧导航栏"的能力
    row_x0 = int(crop_cfg.get("x_from_ratio", 0.0) * c2_img.size[0])
    row_x1 = int(crop_cfg.get("x_to_ratio", 1.0) * c2_img.size[0])

    occ = cfg.get("ocr_confidence", {})
    conf_name = occ.get("name_detection", 0.05)
    conf_bubble = occ.get("bubble_text", 0.15)

    tmg = cfg.get("timing", {})
    t_click = tmg.get("fallback_after_click_delay", 1.0)
    t_scroll = tmg.get("fallback_after_scroll_delay", 0.3)

    dedup = cfg.get("dedup", {})
    cooldown = dedup.get("fallback_click_cooldown_seconds", 120)

    # 白名单与红点开关在 _main_impl 里算好，这里直接用（两条路径共用同一份）

    # ── 行 y 列表 ──────────────────────────────────────────────
    # 微信的会话行**高度不固定**：某行预览变两行时那行会变高，后面全被推下去。
    # 用固定 row_height 数行会累积错位，实测读到"客户F的名字 + 客户C的预览"，
    # 于是点开了错误的会话。所以优先按图像实际切分（wxbot/list_rows.py）。
    scan_cfg = cfg.get("scan", {})
    row_ys = []
    if scan_cfg.get("dynamic_rows"):
        from wxbot.list_rows import detect_list_rows
        detected = detect_list_rows(
            c2_img, max_rows=max_rows,
            x_from_ratio=scan_cfg.get("row_x_from_ratio", 0.28),
            x_to_ratio=scan_cfg.get("row_x_to_ratio", 0.92),
            skip_top_px=scan_cfg.get("list_top_skip_px", 0))
        row_ys = [a for a, _b in detected]
        if row_ys:
            log.debug(f"动态行定位：{len(row_ys)} 行，y={row_ys}")
        else:
            log.warning("动态行定位失败，退回固定行高（可能读到错位的会话）")
    if not row_ys:
        row_ys = [start_y + i * row_h for i in range(max_rows)
                  if start_y + i * row_h + row_bot <= c2_img.size[1]]

    # ── 「只处理点开始之后收到的新消息」（自定义软件用，见 wxbot/new_message_tracker）──
    # 首次调用先把当前会话列表整份记成基线，本轮不处理任何会话 ——
    # 免得一按开始就去翻你以前的聊天记录。
    def _iter_rows():
        for i, yy in enumerate(row_ys):
            if yy + row_bot > c2_img.size[1] or yy + row_top < 0:
                continue
            img = c2_img.crop((row_x0, yy + row_top, row_x1, yy + row_bot))
            if img.size[0] <= 0 or img.size[1] <= 0:
                continue
            # key 用**名字区**（不含消息预览）：新消息只会改预览，不会改名字，
            # 所以 key 稳定；行会重排，不能用 y 当 key（会串到别人身上）。
            name_area = img.crop((0, 0, max(1, int(img.size[0] * 0.72)),
                                  max(1, int(img.size[1] * 0.45))))
            yield i, yy, img, row_key(name_area), row_fp(
                img.crop((0, 0, max(1, int(img.size[0] * 0.72)), img.size[1])))

    if only_new_messages and not new_tracker.primed:
        n = new_tracker.prime((k, fp) for _i, _y, _img, k, fp in _iter_rows())
        log.info(f"会话基线建立完成（{n} 个会话）。之后只回复新收到的消息。")
        return False, "", None

    def _settle(idx, key, fp):
        """把一个会话的当前内容记为基线：这一行本轮处理完了，别再当新消息。"""
        if only_new_messages:
            new_tracker.accept(key, fp)

    first = None   # 本轮取到的待回复消息（每个扫描周期只回一条）
    for i, y in enumerate(row_ys):

        # 先算这一行「名字+消息预览」区域的指纹再判冷却：
        # 客户的新消息总把会话顶到行 0，只按时间冷却会把同一客户
        # 120 秒内的每一句追问都跳过。指纹变了说明有新消息，直接放行。
        # 右边界只取 72% —— 时间戳（"刚刚/1分钟前"）会随时间自己变，
        # 算进去会让冷却每分钟都被无意绕过。
        row_img = c2_img.crop((row_x0, max(0, y + row_top), row_x1, y + row_bot))
        if row_img.size[0] <= 0 or row_img.size[1] <= 0:
            continue
        name_area = row_img.crop((0, 0, max(1, int(row_img.size[0] * 0.72)),
                                  max(1, int(row_img.size[1] * 0.45))))
        this_key = row_key(name_area)
        this_fp = row_fp(row_img.crop((0, 0, max(1, int(row_img.size[0] * 0.72)),
                                       row_img.size[1])))

        # 「只处理新消息」：这个会话相对基线有没有变化？没有就直接跳过，
        # **连名字 OCR 都不用做**（省时间，也避免去碰没变化的会话）。
        if only_new_messages and not new_tracker.is_new(this_key, this_fp):
            continue

        if not detector.is_clickable(y, timeout=cooldown, fingerprint=this_fp):
            continue

        name_crop, name_text = detector.read_row_name(row_img)
        is_cust = detector.is_customer_name(name_crop, name_text)
        log.info(f"兜底行{i} y={y} 对话名: [{name_text[:50]}] 客户={is_cust}")

        # 逐行取舍（纯函数，规则见 wxbot/scan_policy.py）：
        # 点开会话＝清掉对方未读且不可逆，所以这一步要单独把关。
        cust_key = detector.clean_name_text(name_text) if name_text else ""
        ok_to_open, why = row_eligibility(
            is_cust, cust_key, whitelist=whitelist,
            in_cooldown=bool(cust_key) and detector.is_in_cooldown(cust_key))
        if not ok_to_open:
            if why == "not_in_whitelist":
                log.info(f"兜底行{i} {cust_key!r} 不在客户白名单里，跳过（不点开）")
            elif why == "customer_cooldown":
                log.info(f"兜底行{i} {cust_key} 回复冷却中，跳过")
            # 关键是这一步：把当前内容记为基线。给这个客户回过话之后，
            # 列表预览会变成**我们自己的回复**；不推进基线的话，下一轮
            # 又会把"我们刚发的回复"当成新消息，陷入反复点开。
            if only_new_messages:
                new_tracker.accept(this_key, this_fp)
            detector.mark_clicked(y, fingerprint=this_fp)
            continue

        # 是客户行 → 看聊天区。
        # 这里必须点开才能看到聊天内容，而点开会话会**清掉对方未读** ——
        # 所以一旦取到一个待回复客户就立即停止，剩下的行留到下一轮，
        # 不要为了"先看看有哪些"把用户所有会话都点一遍（实测踩过：一轮点了 6 个）。
        chat_fp = scanner.chat_fingerprint()
        if not scanner.click_col2_row(y):
            log.warning(f"兜底 col2 点击失败 y={y}")
            continue
        # 自适应等待：渲染完就继续，最坏情况下等满 t_click
        waited = scanner.wait_chat_update(chat_fp, t_click)
        log.debug(f"兜底点击后等待渲染 {waited:.2f}s")

        # ★ 关键：先确认打开的是**这一行的人**，再读聊天区。
        # 实测的翻车现场：点了 y=223（客户B 那行），聊天区还停在 客户A 没刷新，
        # 于是把 A 的「老板你多大了」算成 客户B 问的，占位语发到了 客户B 那里。
        verdict = scanner.confirm_opened(
            cust_key, detector.read_row_name,
            retry_click=lambda yy=y: scanner.click_col2_row(yy))
        if verdict == "mismatch":
            log.warning(f"兜底y={y} 点开后聊天区不是 {cust_key!r}，这一行跳过"
                        f"（不读内容、不回消息）")
            _settle(i, this_key, this_fp)
            detector.mark_clicked(y, fingerprint=this_fp, timeout=30)
            continue

        scanner.scroll_to_bottom()
        await asyncio.sleep(t_scroll)

        chat_img = scanner.capture_chat_area()
        if chat_img is None:
            continue

        unreplied, has_blue, replied_gray = \
            detector.extract_bubbles_detail(chat_img)
        _mark_already_replied(detector, cust_key, replied_gray)
        if not unreplied:
            log.info(f"兜底y={y} 无气泡/已回复")
            _settle(i, this_key, this_fp)
            detector.mark_clicked(y, fingerprint=this_fp)
            continue

        # ★ 逐条气泡过滤：**这条已经回过就别再回**。
        # 整段合并文本只要多读进一个新气泡就对不上，于是"旧气泡+新气泡"会被当成
        # 全新消息，把老问题再回一遍（实测 客户B 那句 21:45 回过，21:59 又回了一次）。
        bubble_texts = [detector.extract_text(b, min_conf=conf_bubble).strip()
                        for b, _ in unreplied]
        bubble_texts = [t for t in bubble_texts if t]
        if not bubble_texts:
            # 气泡在、一个字都读不出来 → 图片/语音/文件（以前写"OCR空"就跳过了）
            _screenshot_nontext(send_queue, pending_queue, cust_key,
                                len(unreplied))
            _settle(i, this_key, this_fp)
            detector.mark_clicked(y, fingerprint=this_fp, timeout=30)
            continue
        fresh = detector.unseen_texts(cust_key, bubble_texts)
        if not fresh:
            log.info(f"兜底y={y} 气泡都已回复过（{len(bubble_texts)} 条），跳过")
            _settle(i, this_key, this_fp)
            detector.mark_clicked(y, fingerprint=this_fp, timeout=30)
            continue
        combined = " ".join(fresh).strip()
        if len(combined) < 2:
            _screenshot_nontext(send_queue, pending_queue, cust_key,
                                len(unreplied))
            _settle(i, this_key, this_fp)
            detector.mark_clicked(y, fingerprint=this_fp, timeout=30)
            continue

        bn = unreplied[0][1]
        if not detector.is_customer(bn, has_blue, combined):
            _settle(i, this_key, this_fp)
            detector.mark_clicked(y, fingerprint=this_fp)
            continue

        log.info(f"兜底y={y} OCR: {combined[:100]}")

        # 消息级去重：处理过的直接冷却这行，本轮不用再等它
        if detector.is_message_seen(detector.clean_name_text(name_text), combined,
                                    long_term=False):
            log.info(f"兜底y={y} 消息已处理过，跳过")
            _settle(i, this_key, this_fp)
            detector.mark_clicked(y, fingerprint=this_fp)
            continue

        log.info(f">>> 兜底客户! y={y}")
        if first is None:
            # 一起把这条消息的**各个气泡文本**带回去：调用方处理完要逐条记为已见，
            # 否则下次 OCR 多读进一个新气泡，整段对不上，旧问题会被再回一遍。
            first = (combined, detector.clean_name_text(name_text), bubble_texts)
            # 取中的这条交给调用方，处理完会被标记为已见，因此可以立刻冷却这一行。
            # 同时把当前内容记为基线：这样**我们自己的回复**（会改列表预览）
            # 不会被当成一条新消息，避免反复点开同一个会话。
            _settle(i, this_key, this_fp)
            detector.mark_clicked(y, fingerprint=this_fp)
            # **立刻停止**：后面的行要留到下轮再看，不能为了"先看看有哪些"
            # 把用户所有会话都点一遍（点开会话＝清掉对方未读，不可逆）。
            break

    if first is not None:
        return True, first[0], first[1], first[2]
    return False, "", None, []


def _mark_already_replied(detector, customer_name: str, replied_bubbles,
                          limit: int = 5) -> int:
    """把"已经有人回过"的客户气泡记为已处理（**人工回过也算回过**）。

    蓝泡（我们自动发的，或店主手动在企微里发的）**之上**的客户气泡就是这种。
    记下来之后，以后界面再读到这些旧内容（列表重排、窗口没刷新、程序重启、
    OCR 多读进几条）也不会把它们当成新消息再回一遍。

    ``limit`` 只 OCR 最靠近蓝泡的几条 —— 越靠近的越可能是刚被回复的那句，
    再往上的老消息没必要花 OCR。
    """
    if not replied_bubbles:
        return 0
    texts = []
    for img in list(replied_bubbles)[:limit]:
        try:
            t = detector.extract_text(img).strip()
        except Exception:
            continue
        if t and len(t) >= 3 and not detector.is_message_seen(customer_name, t):
            texts.append(t)
    if texts:
        detector.mark_messages_seen(customer_name, texts, already_replied=True)
        log.info(f"已回复过的消息记为已见（{customer_name}）: {texts[:3]}")
    return len(texts)


# 本进程内"招呼已经试过"的客户：发失败了也不反复试，免得刷屏
_GREET_TRIED: set = set()


def greet_first_contact(customer_name: str, cfg: dict, send_fn,
                        log_send=None) -> bool:
    """第一次跟这个客户说话 → 先发欢迎语。返回 True 表示"这轮只发了招呼"。

    调用方拿到 True 要把正文**放回队列下一轮再发** —— 一次 tick 只发一条，
    否则界面要一次卡住两倍时间（发送是在 Tk 线程里跑的）。

    **两种模式共用同一份"招呼过"记录**（`wxbot/first_contact.py`），
    所以 API 模式发过 enter_session 欢迎语之后，这里不会再招呼第二次。

    API 模式其实有更好的信号（客户刚进会话就欢迎），截图模式看不到那个动作，
    只能退到"第一次要说话之前先招呼一句"。

    抽成模块级是为了能测 —— 原来写在 main() 闭包里，只能靠读源码断言。
    """
    w = (cfg or {}).get("welcome", {}) or {}
    if not w.get("on_first_contact", True):
        return False
    if not customer_name or customer_name in _GREET_TRIED:
        return False
    try:
        from wxbot import first_contact
        if first_contact.is_greeted(customer_name):
            return False
    except Exception as e:
        log.warning(f"查招呼记录失败（当没招呼过）: {e}")

    try:
        from rag import prompt_store as ps
        text = ps.get("welcome").strip()
    except Exception:
        text = ""
    if not text:
        return False

    _GREET_TRIED.add(customer_name)          # 试过就不再试，成败都算
    ok = False
    try:
        ok = bool(send_fn(customer_name, text))
    except Exception as e:
        log.warning(f"发欢迎语出错（不影响正文）: {e}")
    if ok:
        try:
            first_contact.mark_greeted(customer_name)
        except Exception as e:
            log.warning(f"记录招呼失败（下次可能再招呼一遍）: {e}")
        log.info(f"[欢迎语] 第一次跟「{customer_name}」说话，先招呼一句")
    else:
        log.warning(f"[欢迎语] 给「{customer_name}」的招呼没发出去，正文照发")
    if log_send:
        try:
            # source=greeting：欢迎语不是"回答客户问题"，看板要能把它单独拎出来
            log_send(customer_name, text, ok, source="greeting",
                     decision_path="欢迎语")
        except Exception:
            pass
    return True


def _verify_screenshot_sent(scanner, detector, min_conf: float = 0.5) -> bool:
    """截图模式：发完顺带看一眼聊天区，确认这条真的出去了。

    "点输入框→粘贴→回车"这条路上**没有任何回执** —— 剪贴板没设上、
    回车没生效、焦点跑了，代码都不知道，照样当成功。客户那边就是干等，
    而日志里写着"[发送]"、看不出异常。（API 模式有接口返回值，所以那边不用这个。）

    判据用**气泡颜色**而不是 OCR 文字：我们刚发的是蓝泡，正常情况下客户那条
    灰泡就变成"已回复"了。所以

    * 没有未回复灰泡 → 发出去了
    * 还有读得出字的未回复灰泡 → 大概率没发出去

    颜色是像素级的，不会因为 OCR 认错字而误判成"没发出去"（那会导致重复发）。
    读不到聊天区、或读不出字时一律**当成功**：宁可不重发，也不能给客户发两遍。
    """
    try:
        time.sleep(0.4)                         # 等气泡渲染出来
        img = scanner.capture_chat_area()
        if img is None:
            return True
        unreplied, _has_blue, _replied = detector.extract_bubbles_detail(img)
        if not unreplied:
            return True
        texts = [detector.extract_text(b, min_conf=min_conf).strip()
                 for b, _ in unreplied]
        if not [t for t in texts if t]:
            return True                         # 读不出内容，判不准 → 不重发
        return False
    except Exception as e:
        log.debug(f"发送确认异常（当成功处理）: {e}")
        return True


def _screenshot_nontext(send_queue, pending_queue, customer_name: str,
                        bubble_count: int = 1) -> str:
    """截图模式：**气泡在，但读不出字** → 当成图片/语音/文件处理。

    以前这种情况走到 ``OCR空`` 就 `continue` 了 —— 客户发张器材照片问"这个有吗"，
    一个字都收不到，日志还只写"OCR空"，店主根本看不出丢了一条消息。
    现在：回一句得体的话 + 转人工（待人工里写清"请打开会话查看"）。

    截图模式**分不清是图片还是语音**（都读不出字），所以用「其它」那句，
    措辞要能同时覆盖两种。待人工也按人合并成一条 —— 读不出内容，
    多条也没法区分。
    """
    from gateway.api_policy import pick_nontext_ack
    from rag import prompt_store as ps
    try:
        acks = ps.parse_nontext_acks()
    except Exception:
        acks = {}
    ack = pick_nontext_ack(["其它"], acks)
    if ack:
        send_queue.put(("send", customer_name, ack))
    pending_queue.push(
        customer_name, "（客户发来图片/语音/文件，请打开会话查看）", "", 0.0,
        "非文本消息（截图模式读不出文字）", key_hint="nontext")
    log.info(f"非文本消息（截图模式，{bubble_count} 个气泡读不出字）: "
             f"{customer_name} → 已应答并转人工")
    return ack


def _detach_process():
    """Re-launch this script as a detached process.

    When the user closes the terminal, the detached process survives.
    Uses DETACHED_PROCESS (not CREATE_NO_WINDOW) so tkinter GUI
    window can still be created.

    Strips --detach from argv to prevent infinite respawn loop.
    """
    import subprocess
    log.info("以脱离终端模式重新启动...")
    # Strip --detach from argv to prevent child from entering detach again
    child_argv = [a for a in sys.argv if a != "--detach"]
    try:
        subprocess.Popen(
            [sys.executable] + child_argv,
            creationflags=(
                subprocess.DETACHED_PROCESS
                if os.name == "nt" else 0
            ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        log.info("新进程已启动，当前进程退出")
    except Exception as e:
        log.error(f"脱离模式启动失败: {e}")
    sys.exit(0)


if __name__ == "__main__":
    if "--detach" in sys.argv:
        _detach_process()
    else:
        main()
