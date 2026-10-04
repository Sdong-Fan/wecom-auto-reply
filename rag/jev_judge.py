# rag/jev_judge.py
"""「该不该答」这个判断点上的**灰度引擎**：规则层 / 影子模式 / Jev 灰度。

## 为什么只换这一个点

`rag/judge.py` 现在还靠词表 + 正则判断"这条客户消息要不要回"。三批 600 条测试里
剩下的 30 条失败**全是"该答没答"（保守转人工）**，说明这个判断点是整条链路最弱的一环。
但它同时是**判错代价最高**的一环：判"该答"其实不该答 = 对客户说错话。

所以灰度版的设计原则是 **[Jev 只有否决权，没有放行权]**：

    Jev 说"不必回" → 不回（它拦下来了）
    Jev 说"要回"   → **仍然**要过检索 / 护栏 / 阈值这三关才能发出去

它永远不能凭一句话让回复发给客户。这样即使判断模型判错，最坏结果也只是
"少答一条、转人工"，不会多发给客户。

## 三态引擎（`config.json` → `judge.engine`）

| engine | 行为 | 用途 |
|---|---|---|
| `rules`（默认） | 完全走 `rag/judge.py`，不联网 | 线上现状，行为不变 |
| `shadow` | **规则决定行为**；同时问一次 Jev，把两边结论落 `logs/jev_shadow.jsonl` | 取证：攒对比数据，零风险 |
| `jev` | **Jev 决定**（只有否决权）；失败/超时/没 key → 自动退回规则层 | 灰度：真在自己号上跑 |

## 成本与延迟

判断约 1 秒、约 1000 输入 token。**每条都问会把你 1.5 秒的中位响应拉到 2.5 秒**，
所以默认 `ask_when = rules_say_reply`：**只在规则层说要回时才问**（反正规则说不用回的
本来就不花钱、也不生成）。这就是分级调用。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from rag.judge import should_reply as rules_should_reply

logger = logging.getLogger(__name__)

SHADOW_LOG = Path("logs/jev_shadow.jsonl")

# ── 题目集（客服语域）────────────────────────────────────────────────
# ★ 三条硬规则（照 Jev 的官方要求，别改）：
#   1. instructions / criteria **一律英文**，state 里的中文原文保持中文
#      —— 判断模型的主训练语言是英文
#   2. 一次请求带全部题目（加题不加价）
#   3. 题目之间不许互相矛盾：每道题只问一个维度，别在两个选项里重复同一个维度
#
# `is_actionable_question` 是**唯一参与决策**的题；另外两道是顺手带上的证据题
# （同一请求里不加钱），影子模式靠它们产出更有说服力的对比表。
QUESTIONS: dict = {
    "is_actionable_question": {
        "type": "noul",
        "instructions": (
            "The last message is from a customer of a camera-rental shop. "
            "Is it a message that the shop's assistant should reply to? "
            "Choose true when it asks something the shop could answer "
            "(price, deposit, stock, rental rules, opening hours, pickup, invoice) "
            "or when it clearly expects a response. "
            "Choose false when it needs no reply at all: a bare acknowledgement "
            "('ok', 'thanks', 'got it'), a closing remark, pure noise "
            "(emoji only, random symbols, a single character), or a message that is "
            "obviously not addressed to this shop. "
            "Judge the last message only; earlier messages are context."
        ),
        "criteria": {
            "true": (
                "A real question or request needing an answer: asking a price, "
                "deposit, availability, rental terms, business hours, pickup, "
                "invoice, or describing a problem and expecting help. "
                "Also true when it starts with a closing word but is really a question, "
                "e.g. 'OK, so how much to rent the X-T5?'"
            ),
            "false": (
                "Nothing to answer: pure acknowledgement ('ok', 'thanks', 'got it', "
                "'received'), a farewell or wrap-up, an emoji-only or symbol-only or "
                "single-character message, a duplicate of something already answered, "
                "or a message clearly meant for someone else."
            ),
        },
    },
    "message_intent": {
        "type": "choice",
        "instructions": (
            "What does the customer actually want in the last message? "
            "Pick one. Judge the ask, not the wording. "
            "If they ask about price or a discount, choose ask_price or ask_discount "
            "even if they phrase it as small talk."
        ),
        "criteria": {
            "ask_price": "Asking how much something costs, or what the deposit is.",
            "ask_stock": "Asking whether a specific item, model, or accessory is available.",
            "ask_rules": ("Asking about rental terms: duration, extension, damage and "
                          "compensation, late return, invoice, pickup or delivery."),
            "ask_hours_location": "Asking opening hours, address, or how to get there.",
            "chitchat": "Small talk, greetings, thanks, or friendly chatter with no real ask.",
            "harassment": ("Hostile, abusive, threatening, or clearly spam / scam content."),
            "unclear": "Cannot tell what they want from this message.",
        },
    },
    "needs_human_authority": {
        "type": "noul",
        "instructions": (
            "Does the last message ask for something that only the shop owner can decide, "
            "rather than something answerable from the shop's published information? "
            "Choose true for price negotiation, a discount or waiver the assistant cannot grant, "
            "a complaint or dispute, a refund, a contract or invoice request, "
            "or any exception to the normal terms."
        ),
        "criteria": {
            "true": ("Negotiating a price, asking for a discount or deposit waiver, "
                     "complaining or threatening a bad review, asking for a refund, "
                     "a contract or special invoice, or any exception to stated terms."),
            "false": ("Answerable from the shop's own published information without "
                      "anyone making a decision: standard price, standard deposit, "
                      "stock or availability, standard rental terms, opening hours, "
                      "ordinary pickup or delivery."),
        },
    },
    # ★ 这一道是**第二个候选落点**（比"该不该答"更对症，见 docs/判断层-Jev替换方案.md）：
    #   现在"资料到底覆盖没覆盖"是靠**检索相似度分数 > 0.50** 这个代理判断的，
    #   而 600 条实测里"该答没答"的失败集中在这里。带上检索片段让模型直接判覆盖，
    #   才是把代理换成显式判断。
    "kb_covers_question": {
        "type": "noul",
        "instructions": (
            "The shop assistant retrieved the excerpts below (field 'knowledge_excerpts') "
            "from the shop's own knowledge base. Do those excerpts contain enough information "
            "to answer the customer's last message accurately? "
            "Choose true only when an excerpt states the actual fact being asked for. "
            "Choose false when the excerpts merely mention the topic without the asked-for fact, "
            "describe a different model, a different rule, or an outdated value, "
            "or when answering would require live or unpublished information."
        ),
        "criteria": {
            "true": ("An excerpt states the exact fact asked for: the price of that model, "
                     "the deposit amount or rule, the rental terms, the opening hours, "
                     "the pickup or delivery rule, or the invoice rule."),
            "false": ("No excerpt states the fact: the topic is mentioned but the specific "
                      "number or rule is missing, a different model or rule is described, "
                      "the value looks outdated, or the question depends on real-time or "
                      "unpublished information such as current stock or a one-off discount."),
        },
    },
}

# 决策只看这一道题
DECISION_QUESTION = "is_actionable_question"
# 第二个候选落点用这道题（判"检索到的片段有没有答上这个问题"）
COVERAGE_QUESTION = "kb_covers_question"
TRUE_THRESHOLD = 0.5           # noul（命题为真的程度）≥ 它 → 要回 / 覆盖


# ── 配置 ──────────────────────────────────────────────────────────────

def engine(cfg: dict = None) -> str:
    """当前引擎：rules（默认）/ shadow / jev。"""
    j = (cfg or {}).get("judge", {}) or {}
    mode = str(j.get("engine", "rules")).strip().lower()
    return mode if mode in ("rules", "shadow", "jev") else "rules"


def _ask_when(cfg: dict = None) -> str:
    j = (cfg or {}).get("judge", {}) or {}
    return str(j.get("ask_when", "rules_say_reply")).strip().lower()


def _send_history(cfg: dict = None) -> bool:
    j = (cfg or {}).get("judge", {}) or {}
    return bool(j.get("send_history", False))


# ── 请求体 ────────────────────────────────────────────────────────────

def build_state(text: str, cfg: dict = None, chunks: list = None) -> dict:
    """客户消息 → Jev 的 state。

    ★ 隐私默认值：**只发这一句客户消息**，不发历史。这和主流程"只把客户这一句话
    + 命中片段发给店主自己配的接口"是同一个口径。要带历史必须显式打开
    `judge.send_history`。

    `chunks` 只在判"资料覆盖没覆盖"（`kb_covers_question`）时才传 —— 那是**必须**给的：
    不让模型看到检索到的片段，它只能靠世界知识猜，判的就不是"我的库覆盖没有"。

    字段形状照 Jev 的 schema（`from` 只认 her/me）：客户 = her，店主/机器人 = me。
    ⚠️ 带片段的字段名（`background.knowledge_excerpts`）是**唯一没法从公开文档确认**的地方；
    真机报 422 时只改这一处（`scripts/jev_shadow.py` 会把原始响应打出来）。
    """
    msgs = [{"from": "her", "text": str(text)}]
    chat = {
        "relationship": ("A customer (her) messaging a camera-rental shop's "
                         "customer-service assistant (me) on WeCom."),
        "messages": msgs,
        "latest_from": "her",
        "is_group": False,
    }
    state = {"chat": chat}
    if chunks:
        state["background"] = {
            "knowledge_excerpts": [str(c)[:1200] for c in chunks[:5]],
        }
    return state


# ── 判断 ──────────────────────────────────────────────────────────────

def _noul(answer: dict) -> Optional[float]:
    """从答案里取 noul（命题为真的程度）。形状不对就当没有。"""
    if not isinstance(answer, dict):
        return None
    v = answer.get("noul")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def decide_from_answers(answers: dict, threshold: float = TRUE_THRESHOLD):
    """答案 → (是否要回, 原因, 证据)。纯函数，离线脚本和在线判断共用一份解析。"""
    if not answers:
        return None, "没有答案", {}
    a = answers.get(DECISION_QUESTION) or {}
    noul = _noul(a)
    if noul is None:
        return None, "答案形状不对（拿不到 noul）", {"raw": a}
    need = noul >= threshold
    ev = {"noul": noul, "threshold": threshold,
          "intent": (answers.get("message_intent") or {}).get("choice", ""),
          "needs_human": _noul(answers.get("needs_human_authority"))}
    reason = ("Jev 判为需要回（%.2f）" if need else "Jev 判为不必回（%.2f）") % noul
    return need, reason, ev


def coverage_from_answers(answers: dict, threshold: float = TRUE_THRESHOLD):
    """答案 → (资料是否覆盖, 原因, 证据)。给"资料覆盖判断"那一维用。

    这道题**必须**在 state 里带上检索到的片段（`build_state(..., chunks=...)`），
    否则模型判的是"这类问题一般能不能答"，不是"我的库覆盖没有"。
    """
    a = (answers or {}).get(COVERAGE_QUESTION) or {}
    noul = _noul(a)
    if noul is None:
        return None, "答案形状不对（拿不到 noul）", {"raw": a}
    covered = noul >= threshold
    reason = ("Jev 判为资料覆盖（%.2f）" if covered
              else "Jev 判为资料未覆盖（%.2f）") % noul
    return covered, reason, {"noul": noul, "threshold": threshold}


def _log_shadow(row: dict) -> None:
    """影子证据落盘。**只写 logs/（gitignore）**，失败也不影响主流程。"""
    try:
        SHADOW_LOG.parent.mkdir(parents=True, exist_ok=True)
        with SHADOW_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.debug("写影子日志失败（忽略）: %s", e)


async def judge_should_reply(text: str, cfg: dict = None
                             ) -> Tuple[bool, str, dict]:
    """「该不该答」的统一入口 —— 三种引擎共用一个签名。

    Returns:
        (是否要回, 原因, meta)
        meta: ``{"engine", "decided_by", "rules", "jev", "agree", "decision_path"}``

    任何情况下都**不抛异常**：判断模型挂了就退回规则层（这是硬要求，
    判断层不能成为机器人接待的单点故障）。
    """
    rules_need, rules_reason = rules_should_reply(text, cfg)
    meta = {"engine": engine(cfg), "decided_by": "rules",
            "rules": rules_need, "jev": None, "agree": None,
            "decision_path": ""}

    mode = meta["engine"]
    if mode == "rules":
        return rules_need, rules_reason, meta

    # 分级调用：规则说"不用回"时默认不问（省钱也省那 1 秒）
    if _ask_when(cfg) == "rules_say_reply" and not rules_need:
        return rules_need, rules_reason, meta

    from rag import jev_client

    if not jev_client.has_key():
        meta["decision_path"] = "未配 JEV_API_KEY，走规则层"
        logger.info("灰度/影子模式开着但没配 %s，本次走规则层", jev_client.ENV_KEY)
        return rules_need, rules_reason, meta

    try:
        data = await jev_client.ask_async(build_state(text, cfg), QUESTIONS, cfg)
    except Exception as e:                      # JevError 及一切意外
        # ★ 判断模型失败 → 退回规则层。这是设计的一部分，不是"降级异常"。
        meta["decision_path"] = "Jev 失败，退回规则层"
        logger.warning("Jev 判断失败，退回规则层: %s", e)
        return rules_need, rules_reason, meta

    answers = data.get("answers") or {}
    jev_need, jev_reason, ev = decide_from_answers(answers)
    meta["jev"] = ev
    meta["usage"] = data.get("usage") or {}

    if jev_need is None:                        # 形状不对 → 同样退回规则层
        meta["decision_path"] = "Jev 答案不可用，退回规则层"
        logger.warning("Jev 答案不可用（%s），退回规则层", jev_reason)
        return rules_need, rules_reason, meta

    meta["agree"] = (jev_need == rules_need)

    if mode == "shadow":
        # 影子：**行为不变**，只落证据
        _log_shadow({
            "ts": datetime.now().isoformat(),
            "text": text,
            "rules": rules_need, "rules_reason": rules_reason,
            "jev": jev_need, "jev_reason": jev_reason,
            "agree": meta["agree"], **ev,
            "usage": meta.get("usage", {}),
        })
        meta["decision_path"] = "影子：与规则%s" % ("一致" if meta["agree"] else "不一致")
        return rules_need, rules_reason, meta

    # ── 灰度：Jev 决定，但它只有否决权 ──────────────────────────────
    if jev_need:
        # 说"要回"也只是放行到检索/护栏/阈值那三关，不是允许发送
        meta["decided_by"] = "jev"
        meta["decision_path"] = "Jev 判为需要回（仍过检索与护栏）"
        return True, "", meta

    meta["decided_by"] = "jev"
    meta["decision_path"] = "Jev 否决：不必回"
    _log_shadow({
        "ts": datetime.now().isoformat(),
        "text": text, "rules": rules_need, "rules_reason": rules_reason,
        "jev": jev_need, "jev_reason": jev_reason, "agree": meta["agree"],
        "mode": "jev", **ev, "usage": meta.get("usage", {}),
    })
    return False, jev_reason, meta


def judge_one_sync(text: str, cfg: dict = None) -> dict:
    """离线/同步版（给 `scripts/jev_shadow.py` 用）：返回两边结论 + 证据。

    与在线版共用 `build_state` / `decide_from_answers`，避免"线上跑一套、
    离线测另一套"的经典坑。
    """
    import asyncio

    from rag import jev_client

    rules_need, rules_reason = rules_should_reply(text, cfg)
    row = {"text": text, "rules": rules_need, "rules_reason": rules_reason,
           "jev": None, "jev_reason": "", "error": ""}
    try:
        data = jev_client.ask(build_state(text, cfg), QUESTIONS, cfg)
    except Exception as e:
        row["error"] = f"{type(e).__name__}: {e}"
        return row
    need, reason, ev = decide_from_answers(data.get("answers") or {})
    row.update({"jev": need, "jev_reason": reason, "agree": need == rules_need,
                "usage": data.get("usage") or {}, **ev})
    return row
