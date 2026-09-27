"""Generate customer reply using retrieved knowledge and DeepSeek API.

Supports multi-turn conversation context via sliding window injection.
Includes prompt injection defense (filters system-instruction patterns).
"""

import logging
import asyncio
import random
import re
from typing import Optional

from rag.llm_client import chat

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一个专业的销售客服助手。你的回答必须严格遵守以下规则：

1. **只使用提供的知识片段来回答客户问题。** 如果知识片段中没有相关信息，或者客户问的话题知识片段完全没有涉及，你必须回复"需要人工处理"。不得编造、推测或补充任何知识片段中没有的信息。
2. 知识不足时的唯一正确回复是"需要人工处理"这6个字。
   - 禁止回复"我没有相关信息"、"我不太清楚"、"建议您咨询"、"或许可以"等变通说法
   - 禁止回复"请问您需要什么帮助"、"您好请问有什么可以帮您"、"请问您主要想解决什么问题"等泛泛问候
   - 禁止回复"很高兴为您服务"、"我是XX客服"等自我介绍
   - 价格/优惠/折扣等敏感信息同样回复"需要人工处理"，不得编造不同说辞
   - 知识不足时唯一正确回复就是"需要人工处理"
3. 当知识片段覆盖了客户问题时，回复要简洁、专业、友好，像真人销售在跟客户对话。
4. 不要透露你是AI或机器人。
5. 不要使用"根据知识库"、"根据提供的信息"等暴露AI身份的表述。
6. **不要重复自己。** 如果对话记录里你已经回答过客户这个问题，必须换一种说法：
   换句式、换个开头、调整详略（这次可以更短，也可以更具体）。
   绝对不要逐字复用你上一轮的回复 —— 真人不会两次说一模一样的话。
7. 语气自然口语化，像在微信上跟客户聊天，不要像念说明书。
   可以带 emoji，但**最多一个**，别堆。
   下面是店主平时说话的样子，照着这个口吻写（只学措辞，事实仍以知识片段为准）：
{tone_samples}
   不要每句话都用"我们"开头，不要用"尊敬的客户"这类书面语。
8. 知识片段里如果有 [当前时间]，那是真实的系统时间，可以放心引用；
   客户问“现在几点/今天几号”就按它回答。
9. **客户重复问同一个问题时，绝不能复读。** 看他问第几遍了：第一遍给完整说法，
   第二遍更短更随意（“刚说过哈，还是X”），第三遍可以带点人味（“还是老样子～”）。
   宁可短，也不要一模一样。
10. **用词必须确定。** 禁止使用"大概"、"可能"、"也许"、"应该是"、"估计"、
   "差不多"这类表示不确定的副词 —— 即使只是随口一问（比如"你大概哪天过来"）
   也不要用。价格、时间、数量一律说确定的数。

{conversation_history}

可用的知识片段：
{context}

请根据以上信息回复客户的问题。记住：知识不足时只能回复"需要人工处理"。。"""

# Patterns to filter from customer messages (prompt injection defense)
_INJECTION_PATTERNS = [
    r'(?i)你是一个\S*',
    r'(?i)请忽略\S*',
    r'(?i)忽略上面的\S*',
    r'(?i)新的指令\S*',
    r'(?i)ignore\s+(all\s+)?(previous|above)\s+instructions',
    r'(?i)system\s*:\s*',
]


def _sanitize_message(text: str) -> str:
    """Remove prompt-injection patterns from customer messages."""
    for pattern in _INJECTION_PATTERNS:
        text = re.sub(pattern, '[filtered]', text)
    return text.strip()


def _format_history(history: list[dict]) -> str:
    """Format conversation history for prompt injection.

    Args:
        history: List of {"role": "customer"|"assistant", "text": "..."} dicts.

    Returns:
        Formatted string, or empty string if no history.
    """
    if not history:
        return ""
    lines = ["最近的对话记录："]
    for turn in history[-3:]:  # sliding window: last 3 turns only
        role = "客户" if turn.get("role") == "customer" else "客服"
        text = turn.get("text", "")
        # Sanitize customer messages to prevent prompt injection via history
        if turn.get("role") == "customer":
            text = _sanitize_message(text)
        lines.append(f"{role}: {text}")
    return "\n".join(lines)


def build_prompt(
    question: str,
    context_chunks: list[str],
    history: Optional[list[dict]] = None,
    style: Optional[str] = None,
) -> list[dict]:
    """Build LLM chat messages with optional conversation history.

    Args:
        question: Current (sanitized) customer message.
        context_chunks: Retrieved knowledge chunks.
        history: Prior conversation turns (from ContextStore.get_recent).
        style: Optional per-reply style hint (wording only, not facts).

    Returns:
        List of message dicts for LLM chat completion.
    """
    safe_question = _sanitize_message(question)
    context = "\n\n---\n\n".join(context_chunks)
    conv_history = _format_history(history or [])
    from rag.prompt_store import get as get_prompt
    from rag.smalltalk import build_tone_block
    from rag.learn import append_to_system
    system = get_prompt("system").format(
        tone_samples=build_tone_block(),
        conversation_history=conv_history,
        context=context,
    )
    # 学到的口吻/改稿习惯追加在尾部：不动用户编辑的提示词文件，也看得见学了什么
    system = append_to_system(system)
    if style:
        system += (f"\n\n本次答复的风格要求（只影响措辞，不得改变任何事实）：{style}")
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": safe_question},
    ]


# 每次生成随机挑一条风格提示 —— 让同一问题的回答在句式上不雷同。
# 实测：只调 temperature 完全无效（0.3/0.8/0.95 下第二次回答都逐字照抄
# 对话历史里自己的上一句话）；加"不要重复自己"规则后每次都换说法，
# 再叠加风格提示后明显更像真人在聊天。
STYLE_NUDGES = [
    "口语一点，简短直接",
    "热情一些，多说一句贴心话",
    "专业简洁，重点先行",
    "像老朋友一样随意一点",
]


# ── 采样温度 ────────────────────────────────────────────────────────
# 线上用偏高的值（更像真人在聊天）；**跑评测时要固定成 0** ——
# 否则同一套题两次跑出不同结果，尺子就不准了（实测同一句话三次三种结果）。
TEMPERATURE = {"reply": 0.8, "hold": 0.9, "smalltalk": 0.95}

# 风格提示平时随机挑（同一问题换着说法）；评测时固定，让结果可复现。
# ★ 光固定温度不够：风格提示也是随机的，它会影响模型怎么措辞、
#   甚至影响它"要不要转人工"的判断 —— 实测同一题两次结果不同就是这个原因。
STYLE_OVERRIDE = None


def set_temperature(reply: float = None, hold: float = None,
                    smalltalk: float = None) -> None:
    """临时改采样温度（评测用；不传的保持原值）。"""
    for key, val in (("reply", reply), ("hold", hold),
                     ("smalltalk", smalltalk)):
        if val is not None:
            TEMPERATURE[key] = float(val)


def set_style(style: str = None) -> None:
    """固定风格提示（评测用）；传 None 恢复随机。"""
    global STYLE_OVERRIDE
    STYLE_OVERRIDE = style


async def generate_reply(
    question: str,
    context_chunks: list[str],
    history: Optional[list[dict]] = None,
) -> str:
    """Generate a reply using DeepSeek with knowledge context and history."""
    messages = build_prompt(question, context_chunks, history=history,
                            style=STYLE_OVERRIDE or random.choice(STYLE_NUDGES))
    reply = await chat(messages, temperature=TEMPERATURE["reply"],
                       max_tokens=512)
    return reply.strip()


# ── 转人工时发给客户的"稍等"占位语 ─────────────────────────────────

HOLD_SYSTEM_PROMPT = """客户问了一句话，你需要先礼貌地告诉客户"我去确认一下，稍等"。

规则：
1. **必须提到客户问的具体内容**，把客户问的东西换个说法说出来。
   例如客户问"适合滑雪的机器"，就回"帮您问下适合滑雪的机器"；
   客户问"有 GoPro 吗"，就回"帮您确认一下有没有 GoPro"。
2. 只表达"我去确认、请稍等"，**不要给出任何答案、价格、参数、库存结论**。
3. 一句话，不超过 30 个字，语气自然口语化，像微信聊天。
4. 禁止使用"大概""可能""也许""应该是""请咨询"这类不确定的词。
5. 不要用"您好，请问"这类套话开场，也不要自称 AI 或客服机器人。

只输出这一句话，不要加引号、不要解释。"""


async def generate_hold_reply(question: str) -> str:
    """生成一句带客户问题内容的礼貌占位语（如"帮您问下适合滑雪的机器"）。

    由调用方兜底：失败时退回 rag.responder.HOLD_REPLIES 里的固定话术。
    """
    from rag.prompt_store import get as get_prompt
    messages = [
        {"role": "system", "content": get_prompt("hold")},
        {"role": "user", "content": _sanitize_message(question)},
    ]
    reply = await chat(messages, temperature=TEMPERATURE["hold"],
                       max_tokens=80)
    return reply.strip()


async def generate_smalltalk_reply(text: str, history: list = None,
                                   timeout: float = 20.0) -> str:
    """非业务消息的闲聊回复 —— 店员视角，短、口语、可带一个 emoji。

    与 generate_reply 的区别：**不喂知识片段**（闲聊不需要也不该引事实），
    提示词里明确禁止提价格/库存/政策。
    """
    from rag.prompt_store import get as get_prompt, parse_tone_samples
    from rag.smalltalk import build_tone_block
    from rag.learn import append_to_system
    sys_prompt = get_prompt("smalltalk").format(
        tone_samples=build_tone_block(parse_tone_samples()))
    sys_prompt = append_to_system(sys_prompt)
    msgs = [{"role": "system", "content": sys_prompt}]
    hist = _format_history(history or [])
    if hist:
        msgs.append({"role": "system", "content": hist})
    msgs.append({"role": "user", "content": _sanitize_message(text)})
    reply = await asyncio.wait_for(
        chat(msgs, temperature=TEMPERATURE["smalltalk"], max_tokens=160),
        timeout=timeout)
    return (reply or "").strip().strip('"').strip("'")
