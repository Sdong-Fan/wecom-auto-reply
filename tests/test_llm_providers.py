"""LLM 接口不绑定 DeepSeek：地址 / 模型名 / 密钥三项可配。

背景：原来 `llm_client.py` 把 `model="deepseek-chat"` 写死，用户就算把
`DEEPSEEK_BASE_URL` 换成通义/OpenRouter，也会因为模型名不对而 400。
"""
import asyncio
import importlib

import pytest

from rag import llm_client


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL",
              "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL"):
        monkeypatch.delenv(k, raising=False)
    llm_client.reset_client()
    yield
    llm_client.reset_client()


# ── 配置解析 ──────────────────────────────────────────────────────────

def test_defaults_to_deepseek(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-x")
    key, base, model = llm_client.resolve_config()
    assert key == "sk-x"
    assert base == "https://api.deepseek.com/v1"
    assert model == "deepseek-chat"


def test_llm_vars_take_priority(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "old")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://old/v1")
    monkeypatch.setenv("LLM_API_KEY", "new")
    monkeypatch.setenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setenv("LLM_MODEL", "qwen-plus")
    key, base, model = llm_client.resolve_config()
    assert (key, base, model) == ("new",
                                 "https://dashscope.aliyuncs.com/compatible-mode/v1",
                                 "qwen-plus")


def test_falls_back_to_legacy_deepseek_vars(monkeypatch):
    """老配置（只有 DEEPSEEK_*）必须照旧能跑，不用改 .env。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-old")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    key, base, model = llm_client.resolve_config()
    assert key == "sk-old" and model == "deepseek-chat"


def test_model_is_configurable_without_base_url(monkeypatch):
    """只换模型名（同一家服务）也要生效。"""
    monkeypatch.setenv("LLM_API_KEY", "sk-x")
    monkeypatch.setenv("LLM_MODEL", "deepseek-reasoner")
    assert llm_client.resolve_config()[2] == "deepseek-reasoner"


def test_describe_config_does_not_leak_key(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-1234567890abcdef")
    text = llm_client.describe_config()
    assert "sk-1234567890abcdef" not in text, "不能把整把密钥打出来"
    assert "cdef" in text, "只留末 4 位方便确认是哪把"
    assert "模型=" in text and "地址=" in text


# ── client 缓存 ───────────────────────────────────────────────────────

def test_client_rebuilt_when_key_changes(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-a")
    c1 = llm_client.get_client()
    monkeypatch.setenv("LLM_API_KEY", "sk-b")
    c2 = llm_client.get_client()
    assert c1 is not c2, "换密钥后必须重建 client，否则还在用旧连接"


def test_client_reused_when_unchanged(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-a")
    assert llm_client.get_client() is llm_client.get_client()


def test_missing_key_gives_actionable_message():
    with pytest.raises(RuntimeError) as e:
        llm_client.get_client()
    assert "设置" in str(e.value) or "LLM_API_KEY" in str(e.value)


# ── 模型名确实传下去了 ────────────────────────────────────────────────

def test_chat_uses_configured_model(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-x")
    monkeypatch.setenv("LLM_MODEL", "qwen-plus")
    captured = {}

    class _Completions:
        async def create(self, **kw):
            captured.update(kw)
            class _M:
                content = "收到"
            class _C:
                message = _M()
            class _R:
                choices = [_C()]
            return _R()

    class _FakeClient:
        chat = type("C", (), {"completions": _Completions()})()

    monkeypatch.setattr(llm_client, "get_client", lambda: _FakeClient())
    out = asyncio.run(llm_client.chat([{"role": "user", "content": "hi"}]))
    assert out == "收到"
    assert captured["model"] == "qwen-plus", "必须用配置里的模型名，不能写死"


def test_chat_accepts_explicit_model_override(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-x")
    monkeypatch.setenv("LLM_MODEL", "qwen-plus")
    captured = {}

    class _Completions:
        async def create(self, **kw):
            captured.update(kw)
            class _M:
                content = "ok"
            return type("R", (), {"choices": [type("C", (), {"message": _M()})()]})()

    monkeypatch.setattr(llm_client, "get_client",
                        lambda: type("F", (), {"chat": type("C", (), {"completions": _Completions()})()})())
    asyncio.run(llm_client.chat([{"role": "user", "content": "hi"}], model="glm-4"))
    assert captured["model"] == "glm-4"


# ── 预设 ──────────────────────────────────────────────────────────────

def test_presets_cover_major_providers():
    names = [p[0] for p in llm_client.PRESETS]
    joined = " ".join(names)
    for want in ("DeepSeek", "OpenRouter", "通义", "Kimi", "自定义"):
        assert want in joined, f"预设里缺少 {want}"
    for name, base, model in llm_client.PRESETS:
        if "自定义" in name:
            continue
        assert base.startswith("http"), f"{name} 的地址不对"
        assert model, f"{name} 缺默认模型名"


def test_presets_are_all_openai_compatible_paths():
    """地址必须是 /v1 结尾的 OpenAI 兼容端点（不是各家私有协议）。"""
    for name, base, _ in llm_client.PRESETS:
        if "自定义" in name:
            continue
        assert base.rstrip("/").endswith("/v1"), f"{name} 不是 OpenAI 兼容端点: {base}"


# ── ping ──────────────────────────────────────────────────────────────

def test_ping_without_key():
    ok, detail = asyncio.run(llm_client.ping())
    assert ok is False and "密钥" in detail


def test_ping_success(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-x")
    monkeypatch.setenv("LLM_MODEL", "qwen-plus")

    async def fake_chat(*a, **kw):
        return "收到"
    monkeypatch.setattr(llm_client, "chat", fake_chat)
    ok, detail = asyncio.run(llm_client.ping())
    assert ok is True and "qwen-plus" in detail


def test_ping_explains_bad_model(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-x")
    monkeypatch.setenv("LLM_MODEL", "wrong-model")

    async def fake_chat(*a, **kw):
        raise RuntimeError("LLM API error 404: model not found")
    monkeypatch.setattr(llm_client, "chat", fake_chat)
    ok, detail = asyncio.run(llm_client.ping())
    assert ok is False
    assert "模型名" in detail, "模型名错要明确指出来"


def test_ping_explains_bad_key(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-bad")

    async def fake_chat(*a, **kw):
        raise RuntimeError("LLM API error 401: invalid_api_key")
    monkeypatch.setattr(llm_client, "chat", fake_chat)
    ok, detail = asyncio.run(llm_client.ping())
    assert ok is False and "密钥" in detail


def test_ping_explains_bad_url(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-x")

    async def fake_chat(*a, **kw):
        raise RuntimeError("Connection error: getaddrinfo failed")
    monkeypatch.setattr(llm_client, "chat", fake_chat)
    ok, detail = asyncio.run(llm_client.ping())
    assert ok is False and "地址" in detail
