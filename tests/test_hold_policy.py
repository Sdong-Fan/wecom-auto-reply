# tests/test_hold_policy.py
"""占位语可以引用资料里的**通用政策**，但绝不能推演到客户这一单。

起因（2026-10-05 真实使用反馈）：客户问「我的订单什么时候发货」——
资料库里明明写着"24 小时发货"，机器人却只回了一句"稍等"，客户一个字有用信息都没听到。

诊断结论：**判定转人工是对的**（评测集 C13B「发货了吗」、C14「我昨天下的单发货了吗」
标注的就是 escalate —— 问的是他那一单，机器人看不到订单状态）。
真问题是**那句占位语把已发布的政策浪费了**，而且 `must_escalate` 在检索之前就返回，
占位语生成器手里**根本没有资料片段**。

所以这一版加了两件事：
1. 占位语生成器可以接收检索片段，允许引用片段里明说的政策
2. 占位语也要**过承诺护栏**（以前只过措辞护栏）—— 它是自动发给客户的

判定口径**没变**（仍是转人工、评测集零回归）。
"""
import asyncio
import csv
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


# ── 1. 评测回归池：这两个说法按同类标 escalate ─────────────────────────

def test_regression_pool_has_the_two_reported_cases():
    p = ROOT / "eval" / "messages_regression.csv"
    assert p.exists(), "真实使用反馈的回归用例池不存在"
    rows = list(csv.DictReader(p.open(encoding="utf-8-sig", newline="")))
    by_msg = {r["消息"]: r for r in rows}
    for msg in ("我的订单什么时候发货", "我昨天的订单什么时候发货"):
        assert msg in by_msg, f"回归池里缺 {msg!r}"
        assert by_msg[msg]["期望行为"] == "escalate"
        assert "24 小时" in by_msg[msg]["参考要点"], "要写清占位语应带上这条政策"


def test_regression_pool_does_not_disturb_the_600_set():
    """回归池单独一个文件 —— 不能塞进 3×200 里，否则 600 条的可比性就废了。"""
    assert (ROOT / "eval" / "messages_200.csv").exists()
    reg = (ROOT / "eval" / "messages_regression.csv").read_text(encoding="utf-8-sig")
    assert "我的订单什么时候发货" not in (
        ROOT / "eval" / "messages_200c.csv").read_text(encoding="utf-8-sig")
    assert "R01" in reg


def test_reported_cases_still_escalate():
    """行为与标注一致：这两句仍然必须转人工（问的是他那一单）。"""
    from rag.guard import must_escalate
    for msg in ("我的订单什么时候发货", "我昨天的订单什么时候发货",
                "发货了吗", "我昨天下的单发货了吗"):
        assert must_escalate(msg), f"{msg!r} 应该命中「订单查询」转人工"


def test_policy_questions_are_not_escalated():
    """反方向：问通用政策的不能被这条规则误伤（资料里有答案就该自动答）。"""
    from rag.guard import must_escalate
    for msg in ("多久发货", "你们多久发货", "几点前下单当天发货", "发什么快递，几天能到"):
        assert not must_escalate(msg), f"{msg!r} 是问政策，不该强制转人工"


# ── 2. 占位语带上政策 ─────────────────────────────────────────────────

def _stub_hold(monkeypatch, capture: dict, reply="我们一般是 24 小时内发出，您这单我帮您确认下～"):
    """把生成函数换掉，捕获它到底收到了什么。"""
    from rag import responder as rp

    async def fake_gen(question, chunks=None):
        capture["question"] = question
        capture["chunks"] = chunks
        return reply

    monkeypatch.setattr(rp, "generate_hold_reply", fake_gen)


def test_hold_receives_chunks_when_retrieval_is_confident(monkeypatch):
    from rag import responder as rp
    cap = {}
    _stub_hold(monkeypatch, cap)
    r = rp.Responder.__new__(rp.Responder)          # 只测这段逻辑，不建 Qdrant/Tk
    got = asyncio.run(r._make_hold_reply(
        "我的订单什么时候发货",
        chunks=["发货时效：下单后 24 小时内发出"], top_score=0.72))
    assert cap["chunks"] == ["发货时效：下单后 24 小时内发出"]
    assert "24 小时" in got


def test_hold_drops_chunks_when_retrieval_is_weak(monkeypatch):
    """检索分不够 = 资料里没这条政策 → 不许硬套，退回通用占位语。"""
    from rag import responder as rp
    cap = {}
    _stub_hold(monkeypatch, cap)
    r = rp.Responder.__new__(rp.Responder)
    asyncio.run(r._make_hold_reply(
        "客户问个资料里没有的事", chunks=["无关片段"], top_score=0.31))
    assert cap["chunks"] is None


def test_hold_never_gets_a_promise_through(monkeypatch):
    """★ 占位语是自动发给客户的：里面出现越权承诺（"您这单今天能到"）必须被拦下。"""
    from rag import responder as rp
    cap = {}
    _stub_hold(monkeypatch, cap, reply="您这单今天一定能到，放心～")
    r = rp.Responder.__new__(rp.Responder)
    got = asyncio.run(r._make_hold_reply("订单什么时候发货", chunks=["x"], top_score=0.9))
    assert got != "您这单今天一定能到，放心～"
    assert got in rp.HOLD_REPLIES, "被承诺护栏拦下后要落到固定话术"


def test_hold_still_rejects_uncertain_wording(monkeypatch):
    from rag import responder as rp
    cap = {}
    _stub_hold(monkeypatch, cap, reply="大概是 24 小时吧，可能明天发")
    r = rp.Responder.__new__(rp.Responder)
    got = asyncio.run(r._make_hold_reply("订单什么时候发货"))
    assert got in rp.HOLD_REPLIES


def test_retrieve_flag_pulls_policy_chunks(monkeypatch):
    """must_escalate 那条路在检索**之前**就返回了，所以它要自己补捞。"""
    from rag import responder as rp
    cap = {}
    _stub_hold(monkeypatch, cap)
    r = rp.Responder.__new__(rp.Responder)

    async def fake_policy(question):
        return ["发货时效：24 小时内发出"]

    monkeypatch.setattr(r, "_policy_chunks", fake_policy)
    asyncio.run(r._make_hold_reply("我的订单什么时候发货", retrieve=True))
    assert cap["chunks"] == ["发货时效：24 小时内发出"]


def test_policy_chunks_returns_empty_on_failure(monkeypatch):
    """取资料失败（embedding 挂了/向量库锁住）不能把占位语搞崩。"""
    from rag import responder as rp
    r = rp.Responder.__new__(rp.Responder)

    def boom(*a, **k):
        raise RuntimeError("向量库锁住了")

    monkeypatch.setattr(rp, "embed_query", boom)
    assert asyncio.run(r._policy_chunks("订单什么时候发货")) == []


# ── 3. 生成器把片段传给模型 + 提示词两处不能漂移 ──────────────────────

def test_generator_puts_chunks_in_the_user_message(monkeypatch):
    """片段走 user 消息（不靠 format 占位符）—— hold.md 是用户可编辑的，
    加占位符会让用户在正文里打花括号就崩。"""
    from rag import generator as gen

    seen = {}

    async def fake_chat(messages, **kw):
        seen["messages"] = messages
        return "我们一般是 24 小时内发出，您这单我帮您确认下～"

    monkeypatch.setattr(gen, "chat", fake_chat)
    asyncio.run(gen.generate_hold_reply("我的订单什么时候发货",
                                        chunks=["发货时效：24 小时内发出"]))
    user = seen["messages"][-1]["content"]
    assert "24 小时内发出" in user, "片段必须进 user 消息"
    assert "绝对不能说客户这一单" in user, "要带上'不许推演到这一单'的约束"
    assert "{" not in user, "不许引入 format 占位符"


def test_generator_without_chunks_stays_minimal(monkeypatch):
    from rag import generator as gen
    seen = {}

    async def fake_chat(messages, **kw):
        seen["messages"] = messages
        return "帮您确认一下发货时间，稍等～"

    monkeypatch.setattr(gen, "chat", fake_chat)
    asyncio.run(gen.generate_hold_reply("有 GoPro 吗"))
    assert seen["messages"][-1]["content"] == "有 GoPro 吗"


def test_hold_prompt_file_and_code_default_agree():
    """`prompts/hold.md`（磁盘，用户可改）会盖过代码里的默认值 ——
    两处改一处忘一处，行为会静默分叉。"""
    from rag.generator import HOLD_SYSTEM_PROMPT
    disk = (ROOT / "prompts" / "hold.md").read_text(encoding="utf-8").strip()
    assert disk == HOLD_SYSTEM_PROMPT.strip(), \
        "prompts/hold.md 与 generator.HOLD_SYSTEM_PROMPT 不一致，请同步"
    assert "通用政策" in disk and "不许把通用政策说成对这一单的保证" in disk
