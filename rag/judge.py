# rag/judge.py
"""该不该答 判断层 —— 答复分流之前先回答"这条消息需不需要回"。

背景（源自 jev-chat "先判断、再写字"的思想，docs/学习笔记-Jev聊天副驾原理.md）：
客服机器人最大的低级错误不是"答错"，而是对"谢谢 / 好的 / 收到"这类收尾语
又回了一句"不客气，还有什么可以帮您"，变成刷屏。检索分数再高也不该回。

这里是一层**零成本、可解释、可测试**的规则判断（不做 LLM 调用）：
  * 收尾/确认/感谢语 → ``should_reply=False`` → 走 ``no_reply``，不回、不转人工、不弹窗；
  * 明显是问题 → ``should_reply=True``，继续走原有的检索 + guard 分流。

判断策略偏保守：只有**短消息 + 命中收尾词 + 没有疑问信号**才判"无需回复"，
拿不准的（含"吗/呢/？/怎么/什么/多少/有没有/多少钱…"）一律当作要回 ——
宁可多回一句，不要漏掉一个真问题。

LLM 版判断器（像 Jev 那样让模型答 7 道题）是更大的工程，需要一套标注集 +
校准脚手架（参考 jev 的 tools/jev/calibrate.py），本期不做，规则版先解决
最频繁的"给收尾语回消息"。
"""

import re
from typing import Tuple

# ── 收尾/确认/感谢语（全匹配才触发，见 should_reply 的判定顺序）──────────
CLOSING_WORDS = (
    "谢谢", "谢谢您", "谢谢老板", "谢谢亲", "多谢", "感谢",
    "好的", "好哒", "好滴", "好呢", "好的呢", "好的呀", "好哦",
    "嗯", "嗯嗯", "嗯嗯嗯",
    "ok", "OK", "Ok", "okok", "OKOK",
    "收到", "了解", "明白", "知道了", "晓得",
    "没事", "没事了", "不用了", "不需要了", "不用", "算了",
    "再见", "拜拜", "bye", "bye bye", "Bye", "白白",
    "行", "行吧", "可以", "可以呀", "可以哦", "好的吧",
)

# 明显疑问信号：出现任意一个就绝不判"无需回复"
QUESTION_CUES = (
    "吗", "呢", "？", "?", "怎么", "什么", "多少", "能否", "如何",
    "有没有", "有没有的", "多少钱", "价格", "怎么卖", "哪些", "哪个",
    "几钱", "能不能", "可以吗", "能吗", "行吗", "租", "借", "快递",
    "押金", "还", "能不能", "几号", "几点", "在哪里", "能不能",
)

_MAX_CLOSING_LEN = 12  # 超过这个长度基本是有内容的，不判收尾

_STRIP_RE = re.compile(r"[\s，,。.!！~～、：:]+")


def _closing_re() -> re.Pattern:
    # 收尾词后面允许跟一点语气标点，整串才算收尾
    alts = "|".join(re.escape(w) for w in sorted(CLOSING_WORDS, key=len, reverse=True))
    return re.compile(rf"^(?:{alts})[，,。.!！~～、\s]*$")


def should_reply(text: str, config: dict = None) -> Tuple[bool, str]:
    """判断这条客户消息是否需要回复。

    Returns:
        (True, "")      要回（正常走检索 + guard）
        (False, reason) 不要回（收尾语/确认），走 no_reply
    """
    raw = (text or "").strip()
    if not raw:
        return False, "空消息"

    stripped = _STRIP_RE.sub("", raw)
    # 太长基本有实质内容，不冒险判收尾
    if len(stripped) > _MAX_CLOSING_LEN:
        return True, ""

    closing = _closing_re()
    if closing.fullmatch(raw) or closing.fullmatch(stripped):
        return False, "收尾语/确认，无需回复"

    # 有疑问信号 → 一定是问题，必回
    if any(cue in stripped for cue in QUESTION_CUES):
        return True, ""

    return True, ""


def should_reply_batch(messages: list) -> Tuple[bool, str]:
    """多条合并消息：最后一条收尾语就整组不回（前一条可能是追问被跟着的谢谢覆盖）。"""
    if not messages:
        return False, "空消息"
    last = messages[-1]
    ok, reason = should_reply(last)
    if not ok:
        # 若前面还有实质问题（带疑问信号），仍要回 —— 只靠最后一条的"谢谢"就把追问吞掉太危险
        for m in messages[:-1]:
            if any(cue in m for cue in QUESTION_CUES):
                return True, ""
        return False, reason
    return True, ""
