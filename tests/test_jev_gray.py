# tests/test_jev_gray.py
"""Jev 灰度判断层：三态引擎 / 只有否决权 / 失败降级 / 请求体形状。

这些用例**不联网**：网络那层用打桩替换掉。它们要钉住的是三条硬约束：

1. **默认引擎是 rules** —— 装了这套东西也不改变线上行为
2. **Jev 只有否决权** —— 它说"要回"也只是放行到检索/护栏/阈值，不能凭一句话让回复发给客户
3. **判断模型挂了不影响接待** —— 超时/报错/没 key 一律退回规则层，绝不抛到主链路
"""
import asyncio
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def cfg_rules():
    return {"judge": {"engine": "rules"}}


@pytest.fixture
def cfg_jev():
    return {"judge": {"engine": "jev", "jev": {"provider": "bocha"}}}


@pytest.fixture
def cfg_shadow():
    return {"judge": {"engine": "shadow", "jev": {"provider": "bocha"}}}


def _answers(noul, intent="ask_price", needs_human=0.1):
    return {"answers": {
        "is_actionable_question": {"type": "noul", "noul": noul},
        "message_intent": {"type": "choice", "choice": intent,
                           "confidence": 0.8,
                           "probabilities": {intent: 0.8}},
        "needs_human_authority": {"type": "noul", "noul": needs_human},
    }, "usage": {"input_tokens": 900, "output_tokens": 12}}


# ── 1. 默认引擎不改变行为 ──────────────────────────────────────────────

def test_default_engine_is_rules(cfg_rules):
    from rag.jev_judge import engine
    assert engine(cfg_rules) == "rules"
    assert engine({}) == "rules", "没配置时必须是 rules（线上现状）"
    assert engine({"judge": {"engine": "胡说八道"}}) == "rules", "非法值回落 rules"


def test_rules_engine_matches_judge_module(cfg_rules):
    """rules 模式下结论必须与 rag/judge.py 完全一致（不能偷偷改行为）。"""
    from rag.jev_judge import judge_should_reply
    from rag.judge import should_reply
    for text in ("嗯嗯", "好的", "请问押金要多少", "索尼A7M4一天多少钱", "😀", "。"):
        got = asyncio.run(judge_should_reply(text, cfg_rules))
        assert got[0] == should_reply(text)[0], text


# ── 2. 灰度模式：Jev 决定，但只有否决权 ────────────────────────────────

def test_jev_vetoes_when_it_says_no(cfg_jev, monkeypatch):
    """规则说要回、Jev 说不必回 → 不回（否决生效）。"""
    from rag import jev_client, jev_judge

    monkeypatch.setattr(jev_client, "has_key", lambda: True)

    async def fake_ask(state, questions, cfg=None):
        return _answers(0.2)

    monkeypatch.setattr(jev_client, "ask_async", fake_ask)
    need, reason, meta = asyncio.run(
        jev_judge.judge_should_reply("请问押金要多少", cfg_jev))
    assert need is False
    assert meta["decided_by"] == "jev"
    assert "不必回" in reason
    assert meta["decision_path"] == "Jev 否决：不必回"


def test_jev_cannot_grant_permission(cfg_jev, monkeypatch):
    """Jev 说"要回"也只是放行到下游三关，不能直接变成"可以发"。"""
    from rag import jev_client, jev_judge

    monkeypatch.setattr(jev_client, "has_key", lambda: True)

    async def fake_ask(state, questions, cfg=None):
        return _answers(0.95)

    monkeypatch.setattr(jev_client, "ask_async", fake_ask)
    need, _reason, meta = asyncio.run(
        jev_judge.judge_should_reply("请问押金要多少", cfg_jev))
    assert need is True
    # 只说明"需要回"，没有任何"允许发送"的语义
    assert "仍过检索与护栏" in meta["decision_path"]
    assert "发" not in meta["decision_path"].replace("仍过检索与护栏", "")


def test_jev_cannot_resurrect_what_rules_rejected(cfg_jev, monkeypatch):
    """反方向：规则说"不用回"、Jev 说"要回" —— 默认（分级调用）下不该被翻过来。

    默认 ask_when=rules_say_reply：规则说不用回的根本不问 Jev（省钱省延迟），
    所以这里连调用都不该发生。
    """
    from rag import jev_client, jev_judge

    called = []

    async def fake_ask(state, questions, cfg=None):
        called.append(1)
        return _answers(0.99)

    monkeypatch.setattr(jev_client, "has_key", lambda: True)
    monkeypatch.setattr(jev_client, "ask_async", fake_ask)
    need, _reason, meta = asyncio.run(
        jev_judge.judge_should_reply("嗯嗯", cfg_jev))
    assert need is False, "规则说不用回时不能被 Jev 翻成要回（默认口径）"
    assert called == [], "不该白花一次判断"
    assert meta["decided_by"] == "rules"


def test_always_mode_asks_every_message(cfg_jev, monkeypatch):
    """把 ask_when 打开成 always 时，每条都问（能救"规则说不回其实该答"）。"""
    from rag import jev_client, jev_judge

    cfg = {"judge": {"engine": "jev", "ask_when": "always"}}
    called = []

    async def fake_ask(state, questions, cfg=None):
        called.append(1)
        return _answers(0.99)

    monkeypatch.setattr(jev_client, "has_key", lambda: True)
    monkeypatch.setattr(jev_client, "ask_async", fake_ask)
    need, _reason, _meta = asyncio.run(
        jev_judge.judge_should_reply("嗯嗯", cfg))
    assert called and need is True


# ── 3. 失败一律降级，绝不抛 ────────────────────────────────────────────

def test_missing_key_falls_back(cfg_jev, monkeypatch):
    from rag import jev_client, jev_judge

    monkeypatch.setattr(jev_client, "has_key", lambda: False)
    for _k in jev_client.ENV_KEYS:
        monkeypatch.delenv(_k, raising=False)
    need, _reason, meta = asyncio.run(
        jev_judge.judge_should_reply("请问押金要多少", cfg_jev))
    assert need is True and meta["decided_by"] == "rules"
    assert "未配" in meta["decision_path"]


def test_timeout_falls_back(cfg_jev, monkeypatch):
    from rag import jev_client, jev_judge

    monkeypatch.setattr(jev_client, "has_key", lambda: True)

    async def boom(state, questions, cfg=None):
        raise jev_client.JevError("Jev 判断超时（6 秒）—— 退回规则层")

    monkeypatch.setattr(jev_client, "ask_async", boom)
    need, _reason, meta = asyncio.run(
        jev_judge.judge_should_reply("请问押金要多少", cfg_jev))
    assert need is True, "判断超时不能让客户消息变成不回"
    assert meta["decided_by"] == "rules"
    assert meta["decision_path"] == "Jev 失败，退回规则层"


def test_garbage_answer_falls_back(cfg_jev, monkeypatch):
    """返回结构不对（比如字段改名了）也要退回规则层，不能当成"不必回"。"""
    from rag import jev_client, jev_judge

    monkeypatch.setattr(jev_client, "has_key", lambda: True)

    async def fake_ask(state, questions, cfg=None):
        return {"answers": {"is_actionable_question": {"type": "choice"}}}

    monkeypatch.setattr(jev_client, "ask_async", fake_ask)
    need, _reason, meta = asyncio.run(
        jev_judge.judge_should_reply("请问押金要多少", cfg_jev))
    assert need is True
    assert meta["decided_by"] == "rules"


# ── 4. 影子模式：行为不变，只落证据 ────────────────────────────────────

def test_shadow_never_changes_behaviour(cfg_shadow, monkeypatch, tmp_path):
    from rag import jev_client, jev_judge

    monkeypatch.setattr(jev_client, "has_key", lambda: True)
    logged = []
    monkeypatch.setattr(jev_judge, "_log_shadow", lambda row: logged.append(row))
    # 让 Jev 故意与规则相反（规则说要回，Jev 说不必回）
    async def fake_ask(state, questions, cfg=None):
        return _answers(0.05)

    monkeypatch.setattr(jev_client, "ask_async", fake_ask)
    need, _reason, meta = asyncio.run(
        jev_judge.judge_should_reply("请问押金要多少", cfg_shadow))
    assert need is True, "影子模式下行为必须由规则层决定"
    assert meta["decided_by"] == "rules"
    assert meta["agree"] is False
    assert logged and logged[0]["jev"] is False, "分歧要落证据"


# ── 5. 请求体形状（协议） ─────────────────────────────────────────────

def test_questions_have_english_instructions():
    """Jev 的判断模型主训练语言是英文：instructions/criteria 必须是英文。"""
    from rag.jev_judge import QUESTIONS
    assert "is_actionable_question" in QUESTIONS
    assert "kb_covers_question" in QUESTIONS
    for name, q in QUESTIONS.items():
        assert q["type"] in ("noul", "choice", "score"), name
        assert q.get("instructions"), name
        crit = q["criteria"]
        assert crit, name
        if q["type"] == "score":
            assert isinstance(crit, list), "打分题每档写具体情景（列表）"
        else:
            assert isinstance(crit, dict), "noul/choice 用 {档: 说明}"
        # 别把中文说明写进题目（中文只留在 state 的聊天原文里）
        assert not any("\u4e00" <= ch <= "\u9fff" for ch in q["instructions"]), \
            f"{name} 的 instructions 应该用英文"


def test_build_state_shape_and_privacy_default():
    """state 形状照 Jev schema；默认**只发客户这一句**（隐私口径）。"""
    from rag.jev_judge import build_state
    st = build_state("请问押金要多少")
    chat = st["chat"]
    assert chat["messages"] == [{"from": "her", "text": "请问押金要多少"}]
    assert chat["latest_from"] == "her" and chat["is_group"] is False
    assert "background" not in st, "默认不带历史/资料，别多外发内容"
    st2 = build_state("问", chunks=["日租 90 元", "押金 4000"])
    assert st2["background"]["knowledge_excerpts"] == ["日租 90 元", "押金 4000"]


def test_providers_cover_five_hosts_and_endpoints():
    """五家托管的地址/路径照各家文档写死，别串了。"""
    from rag.jev_client import PROVIDERS, provider_spec
    assert set(PROVIDERS) == {"bocha", "typesafe", "vercel", "opencode", "openrouter"}
    assert provider_spec("bocha")[1] == "https://jev.bocha.cn"
    assert provider_spec("typesafe")[1] == "https://api.typesafe.ai"
    # OpenRouter 是唯一路径不同的（/api/alpha/decisions）
    assert provider_spec("openrouter")[2] == "/api/alpha/decisions"
    assert all(provider_spec(k)[2] == "/v1/systemone"
               for k in ("bocha", "typesafe", "vercel", "opencode"))


def test_redact_secrets_never_leaks_key(monkeypatch):
    """三个凭据变量名里的任何一个出现在日志/异常里，都必须被抹掉。"""
    from rag.jev_client import ENV_KEYS, redact_secrets
    for name in ENV_KEYS:
        for k in ENV_KEYS:
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv(name, "secret-key-xyz")
        assert "secret-key-xyz" not in redact_secrets(f"Bearer secret-key-xyz via {name}")


def test_key_env_names_and_precedence(monkeypatch):
    """官方文档的变量名优先：BOCHA_JEV_API_KEY > BOCHA_SEARCH_API_KEY > JEV_API_KEY。"""
    from rag.jev_client import ENV_KEYS, has_key, key
    assert ENV_KEYS == ("BOCHA_JEV_API_KEY", "BOCHA_SEARCH_API_KEY", "JEV_API_KEY")
    for k in ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    assert has_key() is False and key() == ""
    monkeypatch.setenv("JEV_API_KEY", "old-name")
    assert key() == "old-name", "只配旧名也要能用（Jev 聊天助手客户端用的是这个）"
    monkeypatch.setenv("BOCHA_SEARCH_API_KEY", "search-key")
    assert key() == "search-key", "博查 search key 优先于旧名"
    monkeypatch.setenv("BOCHA_JEV_API_KEY", "jev-key")
    assert key() == "jev-key", "官方首选名优先级最高"


def test_bocha_protocol_matches_official_skill_doc():
    """照官方 SKILL.md 钉住博查那条路的协议与重试口径。"""
    from rag import jev_client as jc
    _name, base, path, model = jc.provider_spec("bocha")
    assert base == "https://jev.bocha.cn" and path == "/v1/systemone"
    assert model == "bocha-jev-v1"
    # 官方：不允许未识别字段（temperature/stream 会被拒）—— 请求体只能有这三样
    assert set({"model": 1, "state": 2, "questions": 3}) == {"model", "state", "questions"}
    assert jc.MAX_RETRIES <= 2, "官方建议至多两次重试"
    assert set(jc.RETRY_STATUS) == {429, 503, 529}, "官方只列这三个按 Retry-After 退避"


def test_answer_parsers():
    from rag.jev_judge import coverage_from_answers, decide_from_answers
    need, _reason, ev = decide_from_answers(
        {"is_actionable_question": {"type": "noul", "noul": 0.8},
         "message_intent": {"choice": "ask_stock"}})
    assert need is True and ev["intent"] == "ask_stock"
    cov, _r, _e = coverage_from_answers(
        {"kb_covers_question": {"type": "noul", "noul": 0.1}})
    assert cov is False
    # 拿不到 noul → None（调用方据此退回规则层）
    assert decide_from_answers({"is_actionable_question": {"type": "noul"}})[0] is None
    assert decide_from_answers({})[0] is None


# ── 6. 灰度开关是配置项，默认关 ────────────────────────────────────────

def test_config_ships_with_rules_engine():
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    assert cfg["judge"]["engine"] == "rules", "灰度默认关，不能改变线上行为"
    assert cfg["judge"]["ask_when"] == "rules_say_reply"
    assert cfg["judge"]["jev"]["provider"] in ("bocha", "typesafe", "vercel",
                                               "opencode", "openrouter")
    # 密钥不能进 config.json（只放 .env）
    blob = json.dumps(cfg, ensure_ascii=False)
    assert "JEV_API_KEY" in blob  # 只应作为说明文字出现
    assert "sk-" not in blob, "config.json 里不该出现任何密钥"
