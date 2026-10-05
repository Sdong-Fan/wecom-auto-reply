# tests/test_intent_judge.py
"""「资料库检索不到时，用 LLM 判业务还是闲聊」—— 仲裁者，不是替代者。

店主提的目标流程：
    ① 先检索资料库 → 有资料就按资料答
    ② 没有 → LLM 判是业务还是闲聊
    ③ 业务且资料库没有 → 转人工
    ④ 闲聊 → 直接生成回复

为什么 LLM 只在**正则判不准**时才问：正则对明确的句子判得又快又准，
一次 LLM 调用要几百毫秒到 2 秒还花钱。实测 600 条评测集里真正落到
这一段（正则判不准 + 检索分 < 0.50）的只有 **4 条 = 1%**：
    正则判得准 375 / 检索答得了 40 / 硬红线 88 / 越界婉拒 59 / 低信息量 34。

为什么是仲裁不是替代：实测 LLM 会把「你们老板是男的女的」判成 smalltalk
（它就是"问店员本人"的口吻），而那是店主定的"转人工"，靠正则的业务信号接住。
"""
import pytest

from rag.intent_judge import intent_llm_settings, parse_intent
from rag.smalltalk import classify_message, classify_message_ex


# ── 1. 置信度：正则什么时候该说"我判不准" ────────────────────────────

@pytest.mark.parametrize("text", [
    "在吗", "你好", "哈哈", "今天天气不错", "你们辛苦了", "哈哈",
    "索尼A7M4一天多少钱", "能不能便宜点", "你们店在哪", "帮我推荐个三脚架",
    "就它了", "好的", "谢谢",     # 短句 / 收尾语 —— 正则也判得准
])
def test_confident_cases_do_not_ask_llm(text):
    assert classify_message_ex(text)[1] is True, text


@pytest.mark.parametrize("text", [
    "支持哪些支付方式", "最贵的是哪台", "拍短视频预算200一天",
])
def test_ambiguous_cases_ask_llm(text):
    """既没有业务词、也没有闲聊词、还超过 4 个字 → 交给 LLM 判。"""
    kind, confident = classify_message_ex(text)
    assert confident is False, text
    assert kind == "smalltalk", "正则的兜底结论仍是 smalltalk（店主定的默认值）"


def test_classify_message_is_the_regex_only_version():
    assert classify_message("支持哪些支付方式") == classify_message_ex("支持哪些支付方式")[0]


# ── 2. 解析 LLM 输出 ────────────────────────────────────────────────

@pytest.mark.parametrize("raw,want", [
    ("business", "business"),
    ("smalltalk", "smalltalk"),
    ("Business.", "business"),
    ("SMALLTALK", "smalltalk"),
    ("我判断是 business", "business"),
    ("smalltalk（闲聊）", "smalltalk"),
    ("", ""),
    ("???", ""),
    ("无法判断", ""),
])
def test_parse_intent(raw, want):
    assert parse_intent(raw) == want


# ── 3. 配置 ─────────────────────────────────────────────────────────

def test_intent_llm_disabled_without_config():
    """没配这个键 = 不启用 —— 老配置与既有测试行为完全不变。"""
    assert intent_llm_settings(None)[0] is False
    assert intent_llm_settings({})[0] is False
    assert intent_llm_settings({"rag": {}})[0] is False


def test_intent_llm_reads_config():
    cfg = {"rag": {"intent_llm": {"enabled": True, "timeout_seconds": 3}}}
    assert intent_llm_settings(cfg) == (True, 3.0)


def test_intent_llm_bad_timeout_falls_back():
    cfg = {"rag": {"intent_llm": {"enabled": True, "timeout_seconds": "abc"}}}
    assert intent_llm_settings(cfg) == (True, 6.0)


# ── 4. 失败绝不阻塞 ─────────────────────────────────────────────────

def test_judge_intent_returns_empty_on_llm_failure(monkeypatch):
    """"没配 Key/超时/接口报错" → 返回空串，调用方退回正则结论。"""
    import asyncio

    from rag import intent_judge as ij

    async def boom(*a, **k):
        raise RuntimeError("401 没配 Key")

    import rag.llm_client as lc
    monkeypatch.setattr(lc, "chat", boom)
    assert asyncio.run(ij.judge_intent("随便一句话")) == ""


def test_judge_intent_empty_text_not_called():
    import asyncio
    from rag.intent_judge import judge_intent
    assert asyncio.run(judge_intent("")) == ""
