# gateway/api_policy.py
"""API 模式下"收到一条客户消息之后怎么办"的**唯一**实现。

抽出来的原因：这段策略（该不该答 / 直发 / 转人工 + 占位语 + 推待人工）在 API 模式里
必须和截图模式完全一致，而它原来写在 ``main.py`` 的闭包里没法测。
现在 ``main.py._handle_api_message`` 和测试都调这里，改一处两边同步。

不碰网络、不碰 GUI：发送只往 ``send_queue`` 里放，刷新只调传进来的回调。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from rag import unanswered

logger = logging.getLogger(__name__)

ACTION_NO_REPLY = "no_reply"
ACTION_AUTO_SEND = "auto_send"
ACTION_ESCALATE = "escalate"
ACTION_FAILED = "failed"


@dataclass
class ApiDispatchOutcome:
    action: str
    reason: str = ""
    reply_text: str = ""
    hold_text: str = ""


async def dispatch_api_message(responder, external_userid: str, text: str,
                               send_queue, pending_queue,
                               refresh_pending=None,
                               display_name: str = "") -> ApiDispatchOutcome:
    """一条 API 客户消息 → RAG → 分流。

    与截图模式的三档分流完全一致：
      * ``no_reply``   —— 收尾语/感谢：不回、不转人工、不推待人工
      * ``auto_send``  —— guard 不拦且检索分高于门槛：直接把回复放进发送队列
      * ``escalate``   —— 先发一句礼貌占位语，再连 AI 草稿推进「待人工」

    ``external_userid`` 既是回复路由的收件人，也是会话上下文/待人工队列里的 key
    （回复必须用 id，昵称只用于显示）。
    """
    who = display_name or external_userid
    try:
        result = await responder.handle_customer_message(external_userid, [text])
    except Exception as e:
        logger.error(f"API 处理失败 [{who}]: {e}")
        return ApiDispatchOutcome(ACTION_FAILED, reason=str(e))

    if result.dispatch_level == "no_reply":
        logger.info(f"无需回复(API): {who} - {result.reason}")
        return ApiDispatchOutcome(ACTION_NO_REPLY, reason=result.reason)

    if result.success and result.dispatch_level == "auto_send":
        send_queue.put(("send", external_userid, result.reply_text))
        return ApiDispatchOutcome(ACTION_AUTO_SEND,
                                  reason=result.guard_reason or "auto_send",
                                  reply_text=result.reply_text)

    if result.escalated:
        # 转人工 = 资料库缺这条 → 记进「最常转人工的问题」排行
        unanswered.record(text, result.reason or result.guard_decision, who)
        if result.hold_text:
            send_queue.put(("send", external_userid, result.hold_text))
        pending_queue.push(external_userid, text, result.reply_text,
                           result.retrieval_score,
                           result.guard_decision or result.reason)
        if refresh_pending:
            refresh_pending()
        logger.info(f"转人工(API): {who} - {result.reason}")
        return ApiDispatchOutcome(ACTION_ESCALATE, reason=result.reason,
                                  reply_text=result.reply_text,
                                  hold_text=result.hold_text)

    # 技术性失败（不 escalated）：允许下一轮重试，不标记已见由调用方决定
    return ApiDispatchOutcome(ACTION_FAILED, reason=result.reason or "unknown")


# ── 非文本消息（图片 / 语音 / 文件…）────────────────────────────────

def pick_nontext_ack(kinds, acks) -> str:
    """按消息类型挑一句应答；没有对口类型就退到「其它」。"""
    for k in (kinds or []):
        if k in (acks or {}):
            return acks[k]
    return (acks or {}).get("其它", "")


async def dispatch_api_nontext(uid: str, items, send_queue, pending_queue,
                               acks=None, refresh_pending=None,
                               display_name: str = "") -> ApiDispatchOutcome:
    """客户发来的**不是文字**：回一句得体的话 + 转人工一条。

    原来这类消息整个丢掉 —— 客户发张器材照片问"这个有吗"，一个字都收不到。
    ``items`` 是这一批里同一客户的所有非文本消息（连发 3 张图只回一次、
    只进一条待人工，不能刷屏）。
    """
    kinds = []
    for it in (items or []):
        label = getattr(it, "kind_label", "未知类型")
        if label not in kinds:
            kinds.append(label)
    label = "、".join(kinds) or "未知内容"
    ack = pick_nontext_ack(kinds, acks)
    if ack:
        send_queue.put(("send", uid, ack))

    first_id = getattr(items[0], "msgid", "") if items else ""
    pending_queue.push(
        uid, f"（客户发来{label}，请到微信客服后台查看）", "", 0.0,
        f"非文本消息（{label}）",
        key_hint=f"nontext:{first_id}" if first_id else "")
    if refresh_pending:
        refresh_pending()
    logger.info(f"非文本消息（{display_name or uid}）: {label} ×{len(items or [])}"
                f" → 已应答并转人工")
    return ApiDispatchOutcome(ACTION_ESCALATE, reason=f"非文本（{label}）",
                              hold_text=ack)


# ── 进入会话的欢迎语 ────────────────────────────────────────────────

def should_welcome(uid: str, welcomed: dict, cooldown: float,
                   enabled: bool = True, code: str = "",
                   now: float = None) -> bool:
    """现在该不该给这个客户发欢迎语。

    三个"不该"：功能关着、事件没带 code、同一个人刚招呼过。
    最后一条很重要 —— 客户来回切会话会反复触发 enter_session，
    不拦就会一直弹"在的～想租什么"。
    """
    if not enabled or not code:
        return False
    if now is None:
        import time as _t
        now = _t.time()
    last = welcomed.get(uid)
    if last is None:
        return True                 # 从没欢迎过 → 直接发
    return (now - float(last)) >= cooldown
