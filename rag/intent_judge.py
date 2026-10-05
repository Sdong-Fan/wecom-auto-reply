# rag/intent_judge.py
"""让 **LLM 判"业务问题还是闲聊"** —— 只用于正则判不准的那一小段。

店主提的目标流程：
    ① 先检索资料库 → 有资料就按资料答
    ② 没有 → **LLM 判是业务还是闲聊**
    ③ 业务且资料库没有 → 转人工
    ④ 闲聊 → 直接生成回复

为什么不是每条消息都问 LLM：正则分类器对**明确的**句子判得又快又准
（有业务词 → 业务；"在吗/哈哈" → 闲聊），一次 LLM 调用要几百毫秒到 2 秒、
还花钱。真正需要 LLM 的只有**中间那段模糊地带**
（「你去过厦门吗」「这台机器拍出来好看吗」「你周末干嘛去了」这类：
没有业务词、也没有常见的闲聊词）。所以：

    正则判得准（classify_message_ex → confident=True）→ 直接用，不问 LLM
    正则判不准（confident=False）                    → 问 LLM；失败就退回正则的结论

**失败绝不阻塞**：没配 Key、接口超时、返回乱码 —— 一律退回正则结论，
机器人不会因为"判意图"这一步而卡住或拒答。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

VALID = ("business", "smalltalk")

INTENT_SYSTEM = """你在给一家相机租赁店的客服机器人做**意图分类**，\
判断客户的这句话属于【业务咨询】还是【闲聊寒暄】。

【业务咨询】= 在问这家店的事。包括但不限于：
价格、租金、押金、租期、续租、退租、库存/有没有货、机型、镜头、配件、
发票、合同、支付方式、分期、会员、优惠、减免、物流、快递、配送时间、
自提、门店位置、营业时间、售后、维修、赔偿、保险、退款、退押、投诉、
议价、发票细节、跨店取还、合作/加盟/招聘/拿货。

【闲聊寒暄】= 跟租器材无关的随口聊天，包括但不限于：
打招呼、道谢、聊天气、聊吃的、聊心情、问店员本人的经历/喜好/私事、
感慨、抱怨情绪、无关的推销（广告、代运营、拉群）、催人。

**判不准时一律回 business** —— 把业务问题当闲聊答了，客户会拿到不靠谱的回复；
把闲聊当业务，最多是多转一次人工。两个方向的代价不对称。

只输出一个词：business 或 smalltalk。不要解释、不要标点。"""


def parse_intent(raw: str) -> str:
    """把模型输出解析成 ``business`` / ``smalltalk``；解析不出来返回空串。"""
    t = (raw or "").strip().lower()
    if not t:
        return ""
    # 模型偶尔会多写一句话，取最先出现的那个词
    i_b = t.find("business")
    i_s = t.find("smalltalk")
    if i_b < 0 and i_s < 0:
        return ""
    if i_b < 0:
        return "smalltalk"
    if i_s < 0:
        return "business"
    return "business" if i_b < i_s else "smalltalk"


def intent_llm_settings(config: dict = None) -> tuple:
    """``(是否启用, 超时秒数)``。配置里没有这个键 = **不启用**（老配置行为不变）。"""
    section = ((config or {}).get("rag") or {}).get("intent_llm") or {}
    enabled = bool(section.get("enabled", False))
    try:
        timeout = float(section.get("timeout_seconds", 6))
    except (TypeError, ValueError):
        timeout = 6.0
    return enabled, timeout


async def judge_intent(text: str, model: str = None) -> str:
    """LLM 判意图 → ``"business"`` / ``"smalltalk"``；**判不了返回空串**。

    空串的含义是"这次没判出来，请调用方退回正则结论" —— 不抛异常、不阻塞。
    """
    q = (text or "").strip()
    if not q:
        return ""
    try:
        from rag.llm_client import chat
        out = await chat([{"role": "system", "content": INTENT_SYSTEM},
                          {"role": "user", "content": q}],
                         temperature=0.0, max_tokens=8, model=model)
    except Exception as e:
        logger.warning("意图判断失败（退回正则结论）: %s", e)
        return ""
    verdict = parse_intent(out)
    if not verdict:
        logger.warning("意图判断输出无法解析（退回正则结论）: %r", (out or "")[:40])
    return verdict
