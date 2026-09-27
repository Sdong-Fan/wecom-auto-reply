# tests/test_guard.py
import pytest
from rag.guard import (
    check_retrieval, check_reply, check_reply_and_classify,
    classify_and_dispatch, RetrievalResult, GuardResult,
)


# ── Layer 1: retrieval checks (existing) ──────────────────────────────

def test_high_score_direct_reply():
    result = check_retrieval([0.85])
    assert result.decision == "generate"
    assert result.confidence == "high"


def test_medium_score_low_confidence():
    result = check_retrieval([0.55])
    assert result.decision == "generate"
    assert result.confidence == "low"


def test_low_score_escalate():
    result = check_retrieval([0.25])
    assert result.decision == "escalate"


def test_empty_results_escalate():
    result = check_retrieval([])
    assert result.decision == "escalate"


# ── Layer 2: legacy bool check (backward-compatible) ──────────────────

def test_uncertainty_keyword_detected():
    assert check_reply("我不确定这个是不是对的", []) is False


def test_uncertainty_keyword_maybe():
    assert check_reply("可能需要您联系客服处理", []) is False


def test_confident_reply_passes():
    assert check_reply("产品保修期为2年，从购买之日起计算。", ["产品保修期为2年"]) is True


def test_hallucination_numbers_detected():
    assert check_reply("产品价格为9999元", ["产品价格请咨询销售"]) is False


def test_grounded_numbers_pass():
    assert check_reply("产品保修期为2年", ["产品保修期为2年"]) is True


# ── Layer 2: three-state classification (new) ─────────────────────────

def test_guard_pass_clean_reply():
    result = check_reply_and_classify(
        "您好，课程价格是 2980 元，现在活动价 1980 元。",
        ["课程原价 2980，现价 1980"],
    )
    assert result.decision == "pass"


def test_guard_block_uncertainty():
    result = check_reply_and_classify(
        "这个我不太确定，可能大概 2000 左右吧",
        ["课程价格 2000"],
    )
    assert result.decision == "block"


def test_guard_block_hallucinated_number():
    result = check_reply_and_classify(
        "这个课程卖 9999 元", ["课程价格是 2000 元"],
    )
    assert result.decision == "block"


def test_guard_block_generic_pattern():
    result = check_reply_and_classify(
        "很高兴为您服务，请问有什么可以帮您？", ["课程介绍..."],
    )
    assert result.decision == "block"


def test_guard_low_risk_hedge():
    """Hedge phrase without uncertainty keywords → low_risk."""
    result = check_reply_and_classify(
        "课程不错，具体价格请以实际为准，您可以先报名", ["课程价格 2000"],
    )
    assert result.decision == "low_risk"


# ── Combined classify_and_dispatch ────────────────────────────────────

def test_classify_and_dispatch_retrieval_fail():
    result = classify_and_dispatch(
        [0.2], "您好，课程 2980", ["课程 2980"],
    )
    assert result.decision == "block"
    assert "retrieval_low_confidence" in result.reason


def test_classify_and_dispatch_full_pass():
    result = classify_and_dispatch(
        [0.85], "课程原价2980，活动价1980", ["课程原价2980，活动价1980"],
    )
    assert result.decision == "pass"


# ── Rule 2 数字校验：12/24 小时制等价 ─────────────────────────────────
#
# 实测：资料写「工作时间每天 9 点到 18 点，中午 12 点到 13 点半休息」，
# 模型答「下午 6 点下班，中午 12 点到 1 点半休息」—— 意思一模一样，
# 按字面比 18≠6、13≠1，会被判成"编造数字"→ 白转人工。

def test_time_12h_24h_equivalent_passes():
    result = check_reply_and_classify(
        "我们下午6点下班，中午12点到1点半休息",
        ["工作时间每天 9 点到 18 点，中午 12 点到 13 点半休息"],
    )
    assert result.decision != "block", result.reason


def test_time_half_hour_equivalent_passes():
    result = check_reply_and_classify(
        "晚上7点前都能来取",
        ["取货时间：9:00-19:00"],
    )
    assert result.decision != "block", result.reason


def test_price_number_still_strict():
    """价格照旧从严：6 元不能靠 18 元放过去。"""
    result = check_reply_and_classify("押金 6000 元", ["押金 3000 元"])
    assert result.decision == "block"
    assert "hallucinated_number" in result.reason


def test_number_is_both_time_and_price_not_relaxed():
    """同一个数字既在时间里又在价格里（"日租 6 元，晚上 6 点"）→ 不放宽。"""
    result = check_reply_and_classify(
        "日租 6 元，晚上 6 点下班",
        ["日租 8 元，18 点下班"],
    )
    assert result.decision == "block", \
        "价格处的 6 元没据，不能借时间表达式蒙过去"


def test_grounded_time_untouched():
    """资料和回复都写 18 点 → 本来就有据，不该被动到。"""
    result = check_reply_and_classify(
        "我们 18 点下班", ["营业时间 9:00-18:00"],
    )
    assert result.decision != "block", result.reason


# ── Config override ───────────────────────────────────────────────────

def test_config_overrides_thresholds():
    cfg = {"rag": {"high_confidence_threshold": 0.95, "low_confidence_threshold": 0.6}}
    result = check_retrieval([0.85], config=cfg)
    assert result.decision == "generate"
    assert result.confidence == "low"


# ── Retrieval score fallback ────────────────────────────────────────────

def test_low_retrieval_downgrades_pass_to_low_risk():
    """When top retrieval score is between 0.4 and 0.5, a clean reply
    passes check_reply_and_classify but should be downgraded to low_risk
    because the knowledge match is weak — the reply may be fabricated.

    Scores below 0.4 are already blocked by check_retrieval.
    This test targets the 0.4-0.5 gap where retrieval "passes" but is
    too weak to trust.
    """
    # Score 0.45: above check_retrieval threshold (0.4) but below our
    # new LOW_RETRIEVAL_THRESHOLD (0.5). Clean reply triggers no keywords.
    result = classify_and_dispatch(
        retrieval_scores=[0.45],
        reply="我们公司位于北京市朝阳区，欢迎来访咨询。",
        context_chunks=["产品介绍：智能客服解决方案..."],
    )
    assert result.decision != "pass", \
        f"Score 0.45 should NOT auto-send — knowledge match is too weak. Got: {result.decision}"
    assert result.decision == "low_risk", \
        f"Expected low_risk downgrade, got {result.decision}"
    assert "retrieval" in result.reason.lower(), \
        f"Reason should mention retrieval. Got: {result.reason}"


def test_high_retrieval_uses_normal_rules():
    """When top retrieval score is high, normal guard rules apply."""
    result = classify_and_dispatch(
        retrieval_scores=[0.85],
        reply="我们提供多种套餐，基础版、专业版和企业版可以供您挑选。",
        context_chunks=[
            "我们提供多种套餐，基础版每月XX元，专业版每月XX元，"
            "企业版按需定制。具体价格请咨询销售顾问。"
        ],
    )
    # With high retrieval, normal rules apply (this reply may trigger
    # existing keyword rules — we just verify it doesn't get the
    # low_retrieval downgrade reason)
    assert "low_retrieval" not in result.reason, \
        f"High retrieval should not trigger retrieval downgrade. Got: {result.reason}"
