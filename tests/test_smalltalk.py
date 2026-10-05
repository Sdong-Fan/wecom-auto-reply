"""非业务消息走"店员闲聊"分支，而不是每句都回"帮您问下…稍等"。

现场：客户说"在吗"，检索不到 → 转人工 → 回"帮您问下在吗，稍等。" —— 荒谬。
"""
import asyncio
import datetime
from unittest.mock import MagicMock

import pytest

from rag.smalltalk import TONE_SAMPLES, build_tone_block, classify_message


# ── 分类：保守优先 ────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "在吗", "在么", "在不在", "你好", "您好", "哈喽", "早上好", "晚上好",
    "哈哈", "嘿嘿", "辛苦了", "忙不忙", "累不累", "吃了吗",
    "今天天气不错", "外面下雨了", "周末了", "好无聊啊", "你是真人吗",
])
def test_smalltalk(text):
    assert classify_message(text) == "smalltalk", text


@pytest.mark.parametrize("text", [
    "富士X-T5 多少钱一天", "押金多少", "有大疆吗", "可以开票吗", "怎么租",
    "你们几点下班", "营业时间到几点", "你们招保安吗", "店在哪", "有现货吗",
    "租期怎么算", "能便宜点吗", "身份证要押吗", "快递还是自提",
    # 拿不准的长句 → 业务（宁可答得拘谨，不要答错）
    "我朋友说你们这边机器挺全的你觉得我该选哪个",
])
def test_business(text):
    assert classify_message(text) == "business", text


@pytest.mark.parametrize("text", ["谢谢", "好的", "收到", "没事了", "再见"])
def test_closing(text):
    assert classify_message(text) == "closing", text


def test_business_cues_win_over_smalltalk_cues():
    """"你们几点下班"含"下班"（营业），是**业务**，不能被闲聊截走。"""
    assert classify_message("你们几点下班") == "business"
    assert classify_message("在吗，想问下押金多少") == "business"


def test_empty_is_business():
    assert classify_message("") == "business"
    assert classify_message(None) == "business"


# ── 口吻样本 ──────────────────────────────────────────────────────────

def test_tone_samples_have_emoji_but_not_piles():
    joined = " ".join(TONE_SAMPLES)
    assert "😄" in joined or "😂" in joined or "👌" in joined, "要有人味就得有 emoji"
    for s in TONE_SAMPLES:
        assert len(s) <= 40, f"口吻样本要短: {s!r}"
        # 一条里最多一个 emoji
        emo = sum(1 for ch in s if ord(ch) > 0x1F000)
        assert emo <= 1, f"一条最多一个 emoji: {s!r}"


def test_tone_block_formats_as_list():
    blk = build_tone_block()
    assert blk.count("\n") >= 5
    assert blk.startswith("- ")


# ── 闲聊回复的底线 ────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    "日租 95 元，押金 4000",            # 提价格
    "这个有现货的，随时来拿",            # 提库存
    "我们的政策是租期 3 天起",          # 提政策
    "我是客服，有什么可以帮您",          # 客服腔
    "作为AI我无法回答",                 # 暴露身份
    "", "x" * 200,                      # 空 / 太长
    # ★ 2026-09-29 加：200 条测试集跑出来的 12 条"危险直发"里，绝大多数是
    #   闲聊通道把**拖延/承诺**的话直接发给了客户 ——
    #     「支持分期吗」→「这个我得问下店里哈，晚点回你～」
    #     「我订单到哪了」→「稍等我查下哈～」
    #     「就它了」→「好嘞 那这台给你留着哈」（凭空承诺留货）
    #   客户收到"晚点回你"却没有人工工单；这些必须判不合格 → 退回原流程 → 转人工。
    "稍等我问下店里～",
    "这个我得问下店里哈，晚点回你～",
    "好嘞 那这台给你留着哈",
    "在的，Air 3 有货",
    "哈哈这句我看不懂呀 😂",
])
def test_smalltalk_ok_rejects(bad):
    from rag.responder import _smalltalk_ok
    assert _smalltalk_ok(bad) is False, bad


@pytest.mark.parametrize("good", [
    "在的在的，你说 😄",
    "哈哈这个问题问得好",
    "嗯嗯 就是这么回事",
    "对了，你是想拍什么呀？我帮你看看有什么合适的",
])
def test_smalltalk_ok_accepts(good):
    from rag.responder import _smalltalk_ok
    assert _smalltalk_ok(good) is True, good


# ── 链路：闲聊必须在 escalation 与 LLM 生成之前 ────────────────────────

def test_smalltalk_branch_is_in_low_score_zone():
    """闲聊分支必须落在"检索分不够"那条路上，否则客户说"在吗"永远轮不到它。

    ★ 2026-09-27 收紧：原来只在 `retrieval.decision == "escalate"`（分数 < 0.4）
    分支里判闲聊。但实测"你好/在吗/想你的夜"的检索分是 0.40~0.46 —— 落在
    "生成"档，生成完因为够不上 0.65 又被转人工，客户收到"帮您问下在吗，稍等"。
    所以现在**在检索判定之前**先判一次：分数够不上自动发 + 判定为闲聊 → 走闲聊通道。
    """
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "rag/responder.py").read_text(encoding="utf-8")
    esc = src.index('if retrieval.decision == "escalate":')
    gen = src.index("# ── LLM Generation")
    early = src.index('if kind == "smalltalk" and top_score <= self._high_threshold')
    assert early < esc < gen, "闲聊兜底要排在 escalate 判定之前，LLM 生成之前"
    assert src.index("classify_message_ex(text)") < esc, "分类一次就够，别重复调"
    assert "_smalltalk_ok(small)" in src, "闲聊回复也要过底线校验"


def test_generator_prompt_has_tone_samples_and_emoji():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "rag/generator.py").read_text(encoding="utf-8")
    assert "{tone_samples}" in src
    assert "tone_samples=build_tone_block()" in src
    assert "最多一个" in src, "要限制 emoji 数量，别堆"
