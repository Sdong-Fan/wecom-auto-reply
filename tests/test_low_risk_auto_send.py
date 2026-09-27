"""TDD: low_risk 且分数够高时必须仍能直接发送。

历史：LLM 回"需要人工处理"时 guard 会降级为 low_risk。早期实现里
responder 一律走 human_confirm，导致分数再高也发不出去。
当时的修法是给 low_risk 单独加一个检查 top_score 的分支。

现在分流已改为二档（取消人工确认中间档）：
    分数 > 门槛 且 guard 不拦 → auto_send
    否则                     → 转人工（通知 + 让客户稍等）
条件从 `== "pass"` 放宽为 `!= "block"`，所以 low_risk 在高分时照样直发。
下面的断言保证这一点不被改回去。
"""

import re
from pathlib import Path


def _auto_send_condition() -> str:
    """取出决定 auto_send 的那个 if 条件。"""
    source = Path("rag/responder.py").read_text(encoding="utf-8")
    # 取**最后一次**出现：本地直答（系统时钟）那条分支也用 auto_send，
    # 它排在真正的分流闸门之前，用 index 会取错地方。
    idx = source.rindex('dispatch_level="auto_send"')
    head = source[max(0, idx - 500):idx]
    conds = re.findall(r'if \(([^)]*)\):', head)
    assert conds, "在 auto_send 之前找不到 if (...) 条件"
    return conds[-1]


def test_auto_send_gate_checks_top_score():
    """自动发送必须看检索分数，不能无条件发送。"""
    gate = _auto_send_condition()
    assert "top_score" in gate, \
        "auto_send 条件必须检查 top_score，否则低分会误发"


def test_low_risk_is_not_excluded_from_auto_send():
    """条件不能要求 guard == 'pass'，否则 low_risk 永远发不出去。"""
    gate = _auto_send_condition()
    assert '== "pass"' not in gate, \
        '条件写成 == "pass" 会把 low_risk 排除在自动发送之外'
    assert '!= "block"' in gate, \
        '应写成 != "block"，即除"明确拦下"之外都由分数决定'


def test_responder_no_longer_emits_human_confirm():
    """二档分流：responder 不再产生 human_confirm（人工确认中间档已取消）。"""
    source = Path("rag/responder.py").read_text(encoding="utf-8")
    assert 'dispatch_level="human_confirm"' not in source, \
        "responder 不应再返回 human_confirm，中间档已取消"


def test_escalation_carries_hold_text():
    """转人工时要带上给客户的礼貌占位语，且优先用 LLM 生成带上文内容的。"""
    source = Path("rag/responder.py").read_text(encoding="utf-8")
    n = source.count("hold_text=await self._make_hold_reply(text)")
    assert n >= 5, f"每条转人工路径都应带上 hold_text（实际 {n} 条）"
    assert "generate_hold_reply" in source, \
        "应优先用 LLM 生成带客户问题内容的占位语"
    assert "HOLD_REPLIES" in source, "缺少生成失败时的固定话术兜底"


def test_hold_reply_has_fallback_and_validation():
    """占位语必须有兜底与措辞校验，避免发出不合格内容。"""
    source = Path("rag/responder.py").read_text(encoding="utf-8")
    assert "def _hold_ok(" in source, "缺少占位语校验函数"
    for kw in ("_HOLD_BANNED", "需要人工处理"):
        assert kw in source, f"占位语校验应排除 {kw}"
