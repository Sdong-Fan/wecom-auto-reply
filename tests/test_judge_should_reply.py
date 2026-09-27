"""该不该答 判断层测试（rag/judge.py）。

目的：机器人不对"谢谢/好的/收到"这类收尾语刷屏；同时**不能**把真问题判成收尾语。
"""
import pytest

from rag.judge import should_reply, should_reply_batch


# ── 收尾语：不该回 ──────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "谢谢", "谢谢您", "谢谢老板", "多谢", "感谢",
    "好的", "好的呢", "好哒", "嗯嗯", "嗯",
    "收到", "了解", "明白", "知道了",
    "没事了", "不用了", "不需要了",
    "再见", "拜拜", "OK", "ok",
    "行", "行吧", "可以", "好的！", "谢谢~",
])
def test_closing_phrases_need_no_reply(text):
    ok, reason = should_reply(text)
    assert ok is False, f"{text!r} 应当判为无需回复"
    assert reason


# ── 真问题：必须回（不能因为短就误判）──────────────────────────────────

@pytest.mark.parametrize("text", [
    "有大疆吗", "有gopro吗", "多少钱", "怎么租", "可以开票吗",
    "押金多少", "有没有富士", "X-T5 有货吗？", "能便宜点吗", "怎么还",
    "预算100一天能租什么", "好的，那X-T5怎么租", "谢谢，请问押金多少",
    "收到，我还想问下快递", "可以，但我想知道租期",
])
def test_real_questions_always_need_reply(text):
    ok, _ = should_reply(text)
    assert ok is True, f"{text!r} 是真问题，必须回"


# ── 边界 ────────────────────────────────────────────────────────────────

def test_long_message_never_treated_as_closing():
    # 收尾词开头但内容很长 → 有实质内容
    ok, _ = should_reply("好的我明白了那我再确认一下设备的押金和租期安排")
    assert ok is True


def test_empty_message_needs_no_reply():
    assert should_reply("")[0] is False
    assert should_reply("   ")[0] is False


def test_batch_last_closing_but_earlier_question_still_replies():
    """客户连发"有大疆吗"+"谢谢"：不能因为最后一句是谢谢就把追问吞掉。"""
    ok, _ = should_reply_batch(["有大疆吗", "谢谢"])
    assert ok is True


def test_batch_pure_closing_needs_no_reply():
    ok, reason = should_reply_batch(["谢谢", "好的"])
    assert ok is False and reason


def test_batch_empty():
    assert should_reply_batch([])[0] is False


# ── 与 responder 的接线 ─────────────────────────────────────────────────

def test_responder_returns_no_reply_without_network():
    """收尾语必须在**任何** embedding/检索/LLM 之前就返回 no_reply。"""
    import asyncio
    from rag.responder import Responder

    class DummyScanner:
        pass

    r = Responder.__new__(Responder)          # 不跑 __init__（不连 Qdrant/不加载模型）
    r._config = None
    result = asyncio.run(r.generate_reply("客户A", ["谢谢"]))
    assert result.dispatch_level == "no_reply"
    assert result.escalated is False
    assert result.hold_text == ""
    assert result.reply_text == ""


def test_no_reply_is_not_treated_as_escalation_in_source():
    """三条路径都必须显式处理 no_reply（不回、不转人工、不推待人工）：
    红点路径、兜底路径在 main.py；API 路径在 gateway/api_policy.py。"""
    from pathlib import Path
    src = Path("main.py").read_text(encoding="utf-8")
    assert src.count('result.dispatch_level == "no_reply"') >= 2, \
        "红点路径与兜底路径都要处理 no_reply"
    assert src.count("无需回复") >= 2

    api = Path("gateway/api_policy.py").read_text(encoding="utf-8")
    assert 'result.dispatch_level == "no_reply"' in api
    assert "ACTION_NO_REPLY" in api
    # main.py 的 API 路径必须走这份共享策略，不能自己另写一遍
    assert "from gateway.api_policy import dispatch_api_message" in src
