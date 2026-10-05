# rag/responder.py
"""Responder module — RAG reply generation + three-tier dispatch.

Responsibilities:
- RAG retrieval + LLM generation
- Three-tier quality gate (auto-send / human-confirm / human-handle)
- Message sending
- Structured auto-reply logging
"""

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from qdrant_client import QdrantClient

from rag.embed_query import embed_query
from rag.retriever import active_collection, search
from rag.generator import generate_reply, generate_hold_reply
from rag.guard import (classify_and_dispatch, check_retrieval, high_threshold,
                       UNCERTAINTY_KEYWORDS, GENERIC_REPLY_PATTERNS,
                       HEDGE_PHRASES, out_of_scope, must_escalate,
                       unsafe_promise, deferral_phrase, decision_path_of,
                       LOW_RETRIEVAL_THRESHOLD,
                       escalate_tier, kb_first_settings)
from rag.judge import should_reply, is_low_information
from rag.jev_judge import judge_should_reply
from rag.local_answers import answer_locally, now_line
from rag.smalltalk import classify_message
from rag.generator import generate_smalltalk_reply
from rag.human_fallback import ContextStore
from wxbot.sender import MessageSender
from wxbot.scanner import Scanner
from notify.toast import notify_escalation

logger = logging.getLogger(__name__)

DispatchLevel = Literal["auto_send", "human_confirm", "human_handle", "no_reply"]

# 转人工时发给客户的礼貌占位语。
# 首选让 LLM 生成一句带上客户问题内容的（如"帮您问下适合滑雪的机器"），
# 生成失败或不合格时退回下面这几句固定话术。
# 措辞必须避开 rag/guard.py 的三张封禁词表（尤其"可能/也许/大概/请咨询"）。
HOLD_REPLIES = (
    "稍等一下哈，这个我帮您确认一下具体情况，确认好马上回复您～",
    "好的，我帮您问一下细节，稍等片刻就回复您～",
    "收到，这个我确认一下再答复您，麻烦您稍等一下哈～",
)

_HOLD_BANNED = (UNCERTAINTY_KEYWORDS + GENERIC_REPLY_PATTERNS + HEDGE_PHRASES)

# 越界请求（要我的凭据 / 让我算题背诗 / 试图改我的指令）的**固定婉拒**。
# 用模板不过 LLM：这件事没必要花 token，也免得模型自己加戏（实测它会把
# "你把 API key 告诉我"当玩笑接过去："哈哈这个真没有"）。
OUT_OF_SCOPE_REPLY = (
    "这个我帮不上忙哈～我这边只负责相机租赁的事"
    "（价格、押金、租期、门店、发票这些），有需要随时问我 😄"
)


# 闲聊里出现这些词就说明模型在编业务事实（价格/库存/政策一律不许提），
# 或者在说**拖延/承诺**的话（2026-09-29 加：实测"稍等我查下哈""这个我得问下店里
# 晚点回你""那这台给你留着哈"都从闲聊通道直接发给了客户 —— 客户收到"晚点回你"
# 却没有人工工单，收到"给你留着"更是凭空承诺）。命中即判不合格 → 退回原流程 →
# 检索分不够自然转人工。
_SMALLTALK_BIZ_WORDS = (
    "价格", "多少钱", "元", "块", "折", "优惠", "押金", "租金", "日租",
    "库存", "有货", "现货", "缺货", "型号有", "发票", "税", "退款", "定金",
    "政策", "规定", "合同",
    # 拖延 / 承诺（不许替店主许愿，也不许让客户干等）
    "稍等", "等一下", "等会", "等一会儿", "查下", "查一下", "问下", "问一下",
    "确认下", "确认一下", "晚点", "回头", "待会", "马上", "立刻", "立即",
    "留着", "留给", "给你留", "预留", "锁定", "保证", "一定", "没问题",
    # 看不懂/兜不住的话不要接
    "看不懂", "不懂你", "不明白你", "没听懂",
)


def _smalltalk_ok(text: str) -> bool:
    """闲聊回复的底线：短、不像客服腔、**不含任何业务事实**。"""
    if not text or not (1 <= len(text) <= 80):
        return False
    if any(w in text for w in _SMALLTALK_BIZ_WORDS):
        return False
    for bad in ("我是客服", "有什么可以帮您", "很高兴为您服务", "需要人工处理",
                "作为AI", "作为人工智能"):
        if bad in text:
            return False
    return True


def _hold_ok(text: str) -> bool:
    """校验生成的占位语是否可用。"""
    if not text or not (2 <= len(text) <= 60):
        return False
    # 占位语不能变成"需要人工处理"这种非占位语
    if "需要人工处理" in text or "需要帮您确认" in text:
        return False
    return not any(k in text for k in _HOLD_BANNED)


# LLM 答不出来时按提示词回的这句 —— 它是**给程序看的内部信号**，不是给客户的回复。
# 曾经真的发给过客户（老板在待人工页点了「发送」），所以这里既做过滤也做标记。
HUMAN_MARKER = "需要人工处理"
HUMAN_MARKER_ALT = "需要帮您确认"


def is_human_marker(text: str) -> bool:
    """这句话是不是"我答不了"的内部信号（不能当回复发出去）。"""
    t = (text or "").strip().strip("。.！!~ 　")
    if not t:
        return False
    return t == HUMAN_MARKER or HUMAN_MARKER in t or HUMAN_MARKER_ALT in t


@dataclass
class ReplyResult:
    """Reply result with dispatch metadata."""
    success: bool
    reply_text: str = ""
    hold_text: str = ""            # 转人工时发给客户的占位语（空=不发）
    reason: str = ""
    escalated: bool = False
    dispatch_level: DispatchLevel = "human_handle"
    guard_decision: str = ""       # "pass" | "block" | "low_risk"
    guard_reason: str = ""
    retrieval_score: float = 0.0
    decision_path: str = ""        # 具体是哪条路/哪道护栏（看板统计用，见 decision_path_of）


# 决策路径归一化（decision_path_of）已挪到 rag/guard.py：
# 它没有重依赖，离线回放评测结果、生成看板示例数据都要用，
# 不该因为放在这里而被拖进 torch / pyautogui。已在文件顶部 import 回来。



class Responder:
    """RAG reply generator with a two-tier dispatch gate."""

    def __init__(self, scanner: Scanner, qdrant_path: str = "data/qdrant",
                 config: dict = None):
        self.scanner = scanner
        self.sender = MessageSender()
        self.qdrant = QdrantClient(path=qdrant_path)
        self._top_k = 5
        self._log_path = Path("logs/auto_replies.jsonl")
        self._context = ContextStore()
        # 传了 config 就按 config.json 的 rag.high_confidence_threshold 走，
        # 否则用 guard 里的默认值（0.65）
        self._config = config
        self._high_threshold = high_threshold(config)
        # 是否允许自动发送。自定义软件（比如个人微信）默认要关掉：
        # 那里的会话是朋友/群/营销号，发错话的代价远大于企业微信客服。
        # 关掉之后走「只进待人工」：连"稍等"占位语都不发，什么都不自动发出去。
        self._allow_auto_send = True
        if config:
            self._allow_auto_send = bool(config.get("rag", {}).get("allow_auto_send", True))

    def close(self):
        """Release Qdrant client so the file lock is freed."""
        try:
            self.qdrant.close()
        except Exception:
            pass

    async def _policy_chunks(self, question: str) -> list:
        """为占位语捞一次资料片段（**best-effort，失败就返回空**）。

        为什么需要：`must_escalate`（订单查询/议价/投诉）在**检索之前**就返回了，
        所以那条路上的占位语手里没有任何资料 —— 客户收到的只有"稍等"。
        实测反馈：「我的订单什么时候发货」被判转人工是对的（评测集 C13B 同类），
        但资料库里明明写着"24 小时发货"，客户却一个字都没听到。
        """
        try:
            qv = await asyncio.to_thread(embed_query, question)
            pts = await asyncio.to_thread(
                search, qv, self.qdrant, active_collection(), 3)
            if not pts:
                return []
            if pts[0].score < LOW_RETRIEVAL_THRESHOLD:
                return []                      # 资料里没这条，别硬套
            return [((p.payload or {}).get("text") or "")[:400] for p in pts
                    if (p.payload or {}).get("text")]
        except Exception as e:
            logger.debug("占位语取资料失败（用通用占位语）: %s", e)
            return []

    async def _make_hold_reply(self, question: str, chunks: list = None,
                               top_score: float = 0.0,
                               retrieve: bool = False) -> str:
        """转人工时发给客户的占位语，带上客户问题的内容。

        让 LLM 说一句"帮您问下适合滑雪的机器"这类话，比固定话术更像真人。

        ★ **资料里有的通用政策，可以借占位语说一句**（2026-10-05 实测反馈后加的）：
        客户问「我的订单什么时候发货」，资料里写着"24 小时发货" —— 判定转人工是对的
        （问的是他那一单，机器人看不到），但让客户只听到"稍等"就把已发布的政策浪费了。
        所以把检索到的片段喂进去，允许它**引用片段里明说的政策**，
        同时由提示词 + 承诺护栏**双重禁止**它推演到这一单（"您这单今天能到"）。

        `retrieve=True`：调用方手里没有片段时（`must_escalate` 在检索前就返回了），
        自己补捞一次（本地检索，不花 token，失败就退回通用占位语）。
        有片段但检索分不够（`top_score < LOW_RETRIEVAL_THRESHOLD`）时一律不给 ——
        资料里没这条政策，就别硬套。
        """
        if retrieve and not chunks:
            chunks = await self._policy_chunks(question)
        elif chunks and top_score < LOW_RETRIEVAL_THRESHOLD:
            chunks = None
        try:
            text = await asyncio.wait_for(
                generate_hold_reply(question, chunks=chunks), timeout=8)
        except Exception as e:
            logger.warning(f"占位语生成失败，用固定话术: {e}")
            return random.choice(HOLD_REPLIES)
        # 占位语是**自动发出去**的：既要过原来的措辞护栏，也要过承诺护栏
        if _hold_ok(text) and not unsafe_promise(text):
            return text
        logger.warning(f"占位语不合格，用固定话术: {text[:40]!r}")
        return random.choice(HOLD_REPLIES)

    def _log_reply(self, customer_name: str, message: str,
                   result: ReplyResult, send_ok: bool | None = None):
        """Write structured auto-reply log entry.

        Args:
            send_ok: None = send not attempted (human_confirm/escalated),
                     True = auto-send succeeded, False = auto-send failed.
        """
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "timestamp": datetime.now().isoformat(),
                "customer_name": customer_name,
                "customer_message": message[:500],
                "ai_reply": result.reply_text[:500] if result.reply_text else "",
                "confidence": result.retrieval_score,
                "dispatch_level": result.dispatch_level,
                "guard_decision": result.guard_decision,
                "guard_reason": result.guard_reason,
                "decision_path": result.decision_path or decision_path_of(
                    result.guard_decision, result.guard_reason,
                    result.dispatch_level, result.reason),
                "generated": result.success,
                "escalated": result.escalated,
            }
            if send_ok is not None:
                entry["sent"] = send_ok
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"Failed to write reply log: {e}")

    def log_send_result(self, customer_name: str, reply_text: str,
                        send_ok: bool, customer_message: str = "",
                        guard_decision: str = "", guard_reason: str = "",
                        decision_path: str = "", source: str = "auto"):
        """Log auto-send outcome AFTER the actual send completes.

        Called from main.py's _process_send_queue_tick after _do_send
        returns. This ensures the JSONL accurately reflects whether the
        message was actually delivered to WeChat.

        ★ 2026-10-03 补齐字段（运营看板的前置改造）：
          原来这里把 customer_message / guard_decision / guard_reason 都写成空字符串，
          于是"自动发出去的那些消息客户问了什么"从来没有落盘 —— 看板的
          「客户问题分布」和「护栏拦下了什么」都缺一半数据。
          现在由调用方（发送队列）透传：客户原话 + 护栏判定 + decision_path，
          并用 source 区分这条回复是谁发的（auto=机器人直发 / human_edit=人工改过
          / human_confirm=人工直接发草稿 / greeting=第一次接触的欢迎语）——
          否则人工改稿发出的也会被算成"机器人自动解决"。
        """
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "timestamp": datetime.now().isoformat(),
                "customer_name": customer_name,
                "customer_message": (customer_message or "")[:500],
                "ai_reply": reply_text[:500] if reply_text else "",
                "confidence": 0.0,
                "dispatch_level": "auto_send",
                "guard_decision": guard_decision,
                "guard_reason": guard_reason,
                "decision_path": decision_path or decision_path_of(
                    guard_decision, guard_reason, "auto_send"),
                "source": source,
                "generated": True,
                "sent": send_ok,
                "escalated": False,
            }
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"Failed to write send log: {e}")

    async def _smalltalk_result(self, text: str, customer_name: str,
                                top_score: float):
        """非业务闲聊：按店员口吻接一句，**不转人工**。接不了返回 None。

        「接不了」= 生成失败 / 措辞不合格（含业务数字、客服腔）—— 这时退回原流程。
        """
        try:
            small = await asyncio.wait_for(
                generate_smalltalk_reply(
                    text, history=await asyncio.to_thread(
                        self._context.get_recent, customer_name, 3)),
                timeout=20)
        except Exception as e:
            logger.warning(f"闲聊生成失败，退回原流程: {e}")
            return None
        # 闲聊也必须过底线：不许出现业务数字/价格（编造事实一律拦掉）
        if not (small and _smalltalk_ok(small)):
            logger.info(f"闲聊回复不合格，退回原流程: {small[:40]!r}")
            return None
        if self._allow_auto_send:
            logger.info(f"闲聊回复（非业务，直发）: {text[:20]!r} → {small[:40]!r}")
            return ReplyResult(
                success=True, reply_text=small,
                dispatch_level="auto_send",
                guard_decision="smalltalk",
                guard_reason="非业务闲聊（店员视角）",
                retrieval_score=top_score,
            )
        # 只进待人工的模式（微信个人号）：不发占位语，草稿就是闲聊内容
        logger.info(f"闲聊回复（非业务，进待人工）: {text[:20]!r} → {small[:40]!r}")
        return ReplyResult(
            success=False, reason="非业务闲聊（仅人工模式）",
            reply_text=small, hold_text="",
            escalated=True, dispatch_level="human_handle",
            guard_decision="smalltalk",
            guard_reason="非业务闲聊（店员视角）",
            retrieval_score=top_score,
        )

    async def generate_reply(self, customer_name: str,
                             messages: list[str]) -> ReplyResult:
        """Generate reply and classify dispatch level.

        Returns a ReplyResult with dispatch_level indicating what the
        caller should do:
        - ``auto_send``: safe to send immediately
        - ``human_confirm``: show popup for human approval
        - ``human_handle``: escalate to human (toast notification)
        """
        text = " ".join(messages).strip()
        # 无信息量（纯符号/纯数字/纯表情/乱码）→ 不回：
        #   实测回的是调侃话（"哈哈这是啥，密码吗 😂"），客户乱敲键盘不是要聊天。
        if not text or is_low_information(text):
            logger.info(f"无信息量，不回: {text[:20]!r}")
            return ReplyResult(
                success=False, reason="无信息量（纯符号/数字/表情）",
                dispatch_level="no_reply",
            )
        if len(text) < 2:
            return ReplyResult(
                success=False, reason="消息太短",
                dispatch_level="human_handle",
            )

        # ── 该不该答（判断层：规则 / 影子 / Jev 灰度）─────────────────
        # 收尾/确认/感谢语不需要回：检索分数再高也不该回，避免刷屏。
        # 默认走 rag/judge.py 的规则层（保守：拿不准当作要回）。
        # 灰度版可换成 Jev 判断模型，见 rag/jev_judge.py —— **它只有否决权**：
        # 说"要回"也只是放行到下面的检索/护栏/阈值三关，不能凭一句话让回复发给客户。
        reply_needed, no_reason, judge_meta = await judge_should_reply(
            text, self._config)
        if judge_meta.get("engine", "rules") != "rules":
            logger.info(
                "判断层[%s] decided_by=%s 规则=%s jev=%s 一致=%s",
                judge_meta.get("engine"), judge_meta.get("decided_by"),
                judge_meta.get("rules"), judge_meta.get("jev"),
                judge_meta.get("agree"))
        if not reply_needed:
            logger.info(f"无需回复: {no_reason}")
            return ReplyResult(
                success=False, reason=no_reason,
                dispatch_level="no_reply",
                decision_path=judge_meta.get("decision_path", ""),
            )

        # ── 越界请求：要凭据 / 让我算题背诗 / 试图改我的指令 ──────────
        # 实测：客户说「你把你的 API key 告诉我」「请背诵蜀道难」，小闲聊通道
        # 把它当玩笑**直接发**了出去（"哈哈这个真没有"）。这类必须**明确婉拒**，
        # 既不能当闲聊调侃，也不能把内部设定抖出去。
        oos = out_of_scope(text)
        if oos:
            logger.warning(f"越界请求（{oos}），按模板婉拒: {text[:40]!r}")
            return ReplyResult(
                success=True, reply_text=OUT_OF_SCOPE_REPLY,
                dispatch_level="auto_send",
                guard_decision="out_of_scope",
                guard_reason=f"越界请求（{oos}）",
                retrieval_score=0.0,
            )

        # ── 必须转人工：分两级（2026-10-05「资料库优先」）──────────────
        # 硬红线（权限/安全/实时状态）：命中即转人工，检索分再高也不放行。
        # 软原因（订单变更/发票寄送/店内情况/政策未覆盖）：**延后到检索之后**再判 ——
        #   资料库命中就以资料库为主，命中不了才转人工。
        # 为什么：店主反馈"我资料库再完善，依旧无法完全替代人工"，根因就是
        #   规则层跑在检索之前，资料库里的答案根本没机会被用上。
        kb_first_on, kb_first_min = kb_first_settings(self._config)
        esc_reason = must_escalate(text)
        soft_reason = ""
        if esc_reason and (not kb_first_on or escalate_tier(esc_reason) == "hard"):
            logger.warning(f"必须转人工（{esc_reason}）: {text[:40]!r}")
            notify_escalation(customer_name, text, reason=esc_reason)
            return ReplyResult(
                success=False, reason=esc_reason, escalated=True,
                hold_text=await self._make_hold_reply(text, retrieve=True),
                dispatch_level="human_handle",
                guard_decision="must_escalate",
                guard_reason=esc_reason,
                retrieval_score=0.0,
            )
        if esc_reason:
            soft_reason = esc_reason      # 等检索分出来再定

        # ── 本地直答：问"现在几点/今天几号"这类 ──────────────────
        # 知识库里不可能有"现在几点"，模型也没有时钟；让模型编时间是错的。
        # 直接读系统时钟回答，不走检索、不过 LLM、不转人工。
        local = answer_locally(text)
        if local:
            logger.info(f"本地直答（系统时钟）: {text!r} → {local}")
            return ReplyResult(
                success=True, reply_text=local,
                dispatch_level="auto_send",
                guard_decision="local_answer",
                guard_reason="本地直答（系统时钟）",
                retrieval_score=1.0,
            )

        # ── Embedding ──────────────────────────────────────────────

        try:
            qv = embed_query(text)
        except Exception as e:
            logger.error(f"Embedding失败: {e}")
            notify_escalation(customer_name, text, reason="Embedding错误")
            return ReplyResult(
                success=False, reason="Embedding错误", escalated=True,
                hold_text=await self._make_hold_reply(text, retrieve=True),
                dispatch_level="human_handle",
            )

        # ── Retrieval ──────────────────────────────────────────────

        try:
            # 检索**当前资料库档案**（每次现读）—— 界面切了档案立刻生效
            results = search(qv, self.qdrant,
                             collection_name=active_collection(), top_k=self._top_k)
        except Exception as e:
            logger.error(f"Qdrant检索异常: {e}")
            notify_escalation(customer_name, text, reason="检索服务异常")
            return ReplyResult(
                success=False, reason="检索服务异常", escalated=True,
                hold_text=await self._make_hold_reply(text, retrieve=True),
                dispatch_level="human_handle",
            )

        scores = [r.score for r in results]
        chunks = [(r.payload or {}).get("text", "") for r in results]
        # 把当前时间当一条"知识片段"塞进去：模型看得到它，guard 的
        # "数字必须有出处"检查也能把它当出处（否则回复里写个时间就判成编造）。
        chunks = [now_line()] + chunks
        top_score = scores[0] if scores else 0.0

        # ── 软原因落定（「资料库优先」）─────────────────────────────
        # 到这里才知道资料库答不答得出来：
        #   够分 → 以资料库为主，继续往下走生成（规则让位给资料库）；
        #   不够分 → 按规则转人工（资料库都不知道，只能人工兜底）。
        if soft_reason:
            if top_score >= kb_first_min:
                logger.info("资料库优先：资料库最高分 %.3f ≥ %.2f，按资料库回答"
                            "（覆盖转人工原因=%s）: %r"
                            % (top_score, kb_first_min, soft_reason, text[:40]))
            else:
                logger.warning("必须转人工（%s；资料库最高分 %.3f < %.2f）: %r"
                               % (soft_reason, top_score, kb_first_min, text[:40]))
                notify_escalation(customer_name, text, reason=soft_reason)
                return ReplyResult(
                    success=False, reason=soft_reason, escalated=True,
                    hold_text=await self._make_hold_reply(
                        text, chunks=chunks, top_score=top_score),
                    dispatch_level="human_handle",
                    guard_decision="must_escalate",
                    guard_reason=soft_reason,
                    retrieval_score=top_score,
                )

        # ── Pre-generation retrieval check ─────────────────────────

        retrieval = check_retrieval(scores, config=self._config)
        kind = classify_message(text)

        # ★ 招呼/闲聊不该被"检索分不够"拖去转人工。
        #   现场：客户说「你好」「在吗」「想你的夜」，检索分 0.40~0.46（够不上
        #   自动发的 0.65），于是走了"生成→分数不够→转人工"，
        #   客户收到的是「帮您问下在吗，稍等」—— 荒谬且没人味。
        #   条件里加 `top_score <= 门槛`：分数高说明知识库真有答案，
        #   那就别用闲聊把它抢走。
        if kind == "smalltalk" and top_score <= self._high_threshold:
            st = await self._smalltalk_result(text, customer_name, top_score)
            if st is not None:
                return st

        if retrieval.decision == "escalate":
            # ── 低分区再分一次：是"业务没覆盖"还是"压根不是业务"？──────────
            if kind == "smalltalk":
                st = await self._smalltalk_result(text, customer_name, top_score)
                if st is not None:
                    return st
            # 业务问题但 KB 没覆盖 → 还是转人工

            reason_str = f"检索低({top_score:.2f})" if scores else "无结果"
            notify_escalation(customer_name, text, reason=reason_str)
            return ReplyResult(
                success=False, reason=reason_str, escalated=True,
                hold_text=await self._make_hold_reply(text, chunks=chunks, top_score=top_score),
                dispatch_level="human_handle",
                retrieval_score=top_score,
            )

        # ── LLM Generation (with conversation history) ─────────────

        history = await asyncio.to_thread(
            self._context.get_recent, customer_name, 3)
        try:
            reply = await asyncio.wait_for(
                generate_reply(text, chunks, history=history), timeout=30)
        except asyncio.TimeoutError:
            logger.error("LLM 超时")
            notify_escalation(customer_name, text, reason="LLM超时")
            return ReplyResult(
                success=False, reason="LLM超时", escalated=True,
                hold_text=await self._make_hold_reply(text, chunks=chunks, top_score=top_score),
                dispatch_level="human_handle",
                retrieval_score=top_score,
            )
        except Exception as e:
            logger.error(f"LLM失败: {e}")
            notify_escalation(customer_name, text, reason="LLM错误")
            return ReplyResult(
                success=False, reason="LLM错误", escalated=True,
                hold_text=await self._make_hold_reply(text, chunks=chunks, top_score=top_score),
                dispatch_level="human_handle",
                retrieval_score=top_score,
            )

        # ── Three-tier quality gate ────────────────────────────────

        # Detect LLM's "needs human" signal — pass to guard, don't hard-block
        llm_requests_human = is_human_marker(reply)

        # ── 承诺护栏：回复里不许替店主许愿 ──────────────────────────
        # 200 条测试集里最危险的 3 条都在这：检索分很高、数字也都有出处，
        # 所以数字校验与型号校验都拦不住 ——
        #   「大疆Air3还有吗」→「在的，Air 3 有货」（资料库没有库存数据）
        #   「两小时后能自提吗」→「可以的，两小时后就能自提」
        #   「就它了」→「好嘞 那这台给你留着哈」
        # 共同点：替店主做了他才有权做的承诺（有货/留货/时效/保证）。
        # 草稿留给人工（人工能改），但绝不自动发出去。
        promise = unsafe_promise(reply)
        if promise:
            logger.warning(f"回复含越权承诺（{promise}），转人工: {reply[:60]!r}")
            notify_escalation(customer_name, text,
                              reason=f"越权承诺（{promise}）", draft_reply=reply)
            return ReplyResult(
                success=False, reason=f"越权承诺（{promise}）", escalated=True,
                reply_text=reply,
                hold_text=await self._make_hold_reply(text, chunks=chunks, top_score=top_score),
                dispatch_level="human_handle",
                guard_decision="unsafe_promise",
                guard_reason=f"越权承诺（{promise}）",
                retrieval_score=top_score,
            )

        # ── 拖延话术护栏：主通道也不许自己写"我问问店里再回你" ──────────
        # 2026-10-03 第二批现场：「能签三方合同吗」→「这块得跟店里确认下，晚点回你哈」
        # 被**自动发了出去**。客户在等，人工却不知道（没有工单）。
        # 这类话该由转人工的占位语去说，同时生成待人工条目。
        defer = deferral_phrase(reply)
        if defer:
            logger.warning(f"回复是拖延话术（{defer}），改为转人工: {reply[:60]!r}")
            notify_escalation(customer_name, text,
                              reason="回复是拖延话术（应为转人工）", draft_reply=reply)
            return ReplyResult(
                success=False, reason="回复是拖延话术（应为转人工）", escalated=True,
                reply_text=reply,
                hold_text=await self._make_hold_reply(text, chunks=chunks, top_score=top_score),
                dispatch_level="human_handle",
                guard_decision="deferral_phrase",
                guard_reason=f"拖延话术（{defer}）",
                retrieval_score=top_score,
            )

        # ── 三档质量门控走这里的 classify_and_dispatch ──────────────────
        guard_result = classify_and_dispatch(
            scores, reply, chunks,
            config=self._config,
            llm_requests_human=llm_requests_human,
        )

        # ★ "需要人工处理"是**内部信号**，绝不能当回复发出去。
        # 实测踩过：老板在待人工页点了「发送」，客户收到了这六个字。
        # 所以这里就不把它当草稿 —— 待人工页会显示"没草稿，请自己写"。
        draft = "" if llm_requests_human else reply

        # ── 二档分流（已取消「人工确认」中间档）─────────────────────
        #   检索分数 > 门槛 且 guard 不拦 → 直接发给客户
        #   否则                          → 弹窗通知人工 + 自动发一句礼貌占位语
        if (self._allow_auto_send and not llm_requests_human
                and guard_result.decision != "block"
                and top_score > self._high_threshold):
            return ReplyResult(
                success=True, reply_text=reply,
                dispatch_level="auto_send",
                guard_decision=guard_result.decision,
                guard_reason=guard_result.reason,
                retrieval_score=top_score,
            )

        # ★ 2026-10-05：**业务路走不通时，闲聊再兜一次**。
        #   现场：「你周末干嘛去了」「这台机器拍出来好看吗」分类是 smalltalk，
        #   但它们在资料库里撞上了相近条目（"周末上班吗"）→ 分数 > 0.50 →
        #   上面那道闸把闲聊让给了业务路 → 业务路生成出"帮您问下周末的营业安排"
        #   （答不对）→ 转人工。客户只是随口聊一句，却收到「稍等」。
        #
        #   为什么**不能直接拆那道闸**：实测拆闸会让 **48 条**"期望自动答、分数也够"
        #   的题从"按资料库答"退化成"闲聊答"（「支持哪些支付方式」0.849、
        #   「新疆能发吗」0.897、「A7R5一天多少」0.840…）。闸门是那条
        #   "分数高说明资料库真有答案，别用闲聊抢走"的规则，得留着。
        #   所以只在**业务路已经失败**（没走到自动发）之后兜这一次。
        if kind == "smalltalk":
            st = await self._smalltalk_result(text, customer_name, top_score)
            if st is not None:
                return st

        # 关掉自动发送时（自定义软件），连占位语都不发：只把草稿推进待人工，
        # 由人决定发不发。发错话到个人微信的代价不可逆，宁可什么都不发。
        if not self._allow_auto_send:
            reason_str = (guard_result.reason
                          if guard_result.decision == "block"
                          else f"仅人工模式（置信度 {top_score:.2f}）")
            notify_escalation(customer_name, text, reason=reason_str,
                              draft_reply=draft)
            logger.info(f"仅人工模式，不自动发送: {reason_str}")
            return ReplyResult(
                success=False, reason=reason_str, escalated=True,
                reply_text=draft,
                hold_text="",          # ← 关键：不发占位语
                dispatch_level="human_handle",
                guard_decision=guard_result.decision,
                guard_reason=guard_result.reason,
                retrieval_score=top_score,
            )

        reason_str = (guard_result.reason
                      if guard_result.decision == "block"
                      else f"置信度不足({top_score:.2f})")
        notify_escalation(customer_name, text, reason=reason_str,
                          draft_reply=draft)
        hold = await self._make_hold_reply(text, chunks=chunks, top_score=top_score)
        logger.info(f"转人工: {reason_str} → 已回复客户: {hold}")
        return ReplyResult(
            success=False, reason=reason_str, escalated=True,
            reply_text=draft,
            hold_text=hold,
            dispatch_level="human_handle",
            guard_decision=guard_result.decision,
            guard_reason=guard_result.reason,
            retrieval_score=top_score,
        )

    def send_reply(self, text: str) -> bool:
        """Send reply via WeChat input box."""
        try:
            self.scanner.click_input_box()
            time.sleep(0.3)
            self.sender.send(text)
            return True
        except Exception as e:
            logger.error(f"发送失败: {e}")
            return False

    async def handle_customer_message(self, customer_name: str,
                                      messages: list[str]) -> ReplyResult:
        """Handle customer message: generate reply + log.

        Does NOT send — sending is the caller's responsibility via
        the unified send_queue → _do_send path (which handles
        window switching and keyboard input).
        """
        text = " ".join(messages).strip()
        # Record customer message
        await asyncio.to_thread(
            self._context.append, customer_name, "customer", text)

        result = await self.generate_reply(customer_name, messages)
        # 决策路径统一在这里补齐（各分支只管 guard_decision/reason，不用每条都写）
        if not result.decision_path:
            result.decision_path = decision_path_of(
                result.guard_decision, result.guard_reason,
                result.dispatch_level, result.reason)

        if result.success:
            # For auto_send, logging is deferred until the send actually
            # completes (caller calls log_send_result). For other dispatch
            # levels, log immediately since no send will be attempted.
            if result.dispatch_level != "auto_send":
                self._log_reply(customer_name, text, result)
            # Record assistant reply
            await asyncio.to_thread(
                self._context.append, customer_name, "assistant", result.reply_text)
        elif result.dispatch_level == "no_reply":
            # 审计：记录"判断为无需回复"，不产生任何 assistant 回复
            self._log_reply(customer_name, text, result)

        return result
