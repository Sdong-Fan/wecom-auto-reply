# tests/test_settings_placeholder_and_test_button.py
"""「首次启动的假密钥」与「测试连接按钮」两个实测 bug 的回归测试。

背景（2026-10-04，下载分发包实测报上来的）：
1. 分发包的 `启动.bat` 第一次运行会把 `.env.example` 复制成 `.env`，
   于是设置面板的**密钥框里出现了 `sk-your-deepseek-key`** ——
   看着像"已经配好了"，用户来问"这是不是你们的密钥？能用的？"
   而且 `resolve_config()` 会拿它去调接口，只会得到 401。
2. 点「测试连接」直接报 `TypeError: cannot unpack non-iterable coroutine object`
   —— `llm_client.ping` 是 async，`_run_async` 里 `ok, detail = fn()` 解的是协程对象，
   测试根本没发出去。
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


# ── 1. 占位符要当"没填" ────────────────────────────────────────────────

@pytest.mark.parametrize("value", [
    "sk-your-deepseek-key",          # .env.example 里那一行
    "your_api_key",
    "sk-your-key",
    "xxxx",
    "TODO",
    "changeme",
    "填这里",
    "",
    "   ",
])
def test_placeholder_is_recognized(value):
    from config.settings_store import looks_like_placeholder
    assert looks_like_placeholder(value) is True


@pytest.mark.parametrize("value", [
    "sk-99429abcdef0123456789abcdef0123",   # 真 DeepSeek key 的形态
    "sk-proj-AbCdEf123456",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",  # 有些网关给的是 JWT
])
def test_real_key_is_not_flagged(value):
    from config.settings_store import looks_like_placeholder
    assert looks_like_placeholder(value) is False


def test_settings_panel_does_not_show_placeholder_key(tmp_path):
    """启动.bat 复制出来的 .env → 面板里的密钥必须是空的，不能显示示例值。"""
    from config import settings_store as store
    env = tmp_path / ".env"
    env.write_text(
        "DEEPSEEK_API_KEY=sk-your-deepseek-key\n"
        "LLM_API_KEY=\n"
        "LLM_BASE_URL=https://api.deepseek.com/v1\n"
        "LLM_MODEL=deepseek-chat\n",
        encoding="utf-8")
    vals = store.read_current_settings(env_path=env,
                                       config_path=ROOT / "config.json")
    assert vals["llm_api_key"] == "", "占位符不能当成密钥显示出来"
    assert vals["llm_base_url"] == "https://api.deepseek.com/v1"
    assert vals["llm_model"] == "deepseek-chat"


def test_settings_panel_keeps_real_key(tmp_path):
    from config import settings_store as store
    env = tmp_path / ".env"
    env.write_text("LLM_API_KEY=sk-99429abcdef0123456789abcdef0123\n",
                   encoding="utf-8")
    vals = store.read_current_settings(env_path=env,
                                       config_path=ROOT / "config.json")
    assert vals["llm_api_key"] == "sk-99429abcdef0123456789abcdef0123"


def test_resolve_config_treats_placeholder_as_missing(monkeypatch):
    """拿占位符去调接口只会 401，必须当没填（→ 走"只转人工不外发"）。"""
    from rag import llm_client
    monkeypatch.setenv("LLM_API_KEY", "sk-your-deepseek-key")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    key, base, model = llm_client.resolve_config()
    assert key == ""
    assert base and model


def test_resolve_config_keeps_real_key(monkeypatch):
    from rag import llm_client
    monkeypatch.setenv("LLM_API_KEY", "sk-99429abcdef0123456789abcdef0123")
    key, _, _ = llm_client.resolve_config()
    assert key == "sk-99429abcdef0123456789abcdef0123"


def test_ping_says_it_is_a_placeholder(monkeypatch):
    """别只说"还没填密钥"，要说清"你填的是示例值" —— 否则用户找不到问题。"""
    import asyncio
    from rag import llm_client
    monkeypatch.setenv("LLM_API_KEY", "sk-your-deepseek-key")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    ok, why = asyncio.run(llm_client.ping())
    assert ok is False
    assert "占位符" in why or "示例" in why


# ── 2. 测试连接按钮：协程必须被 await ───────────────────────────────────

class _FakeLabel:
    """替身 label：记录文字，after() 立刻执行（免去等 Tk 主循环）。"""

    def __init__(self):
        self.text = ""
        self.color = None
        self._done = __import__("threading").Event()

    def config(self, text=None, foreground=None, **_kw):
        if text is not None:
            self.text = text
        if foreground is not None:
            self.color = foreground

    def after(self, _ms, fn):
        fn()
        self._done.set()

    def wait(self, timeout=3.0):
        return self._done.wait(timeout)


def test_run_async_awaits_coroutine_function():
    """回归：`ok, detail = fn()` 解协程对象会抛 TypeError。"""
    import types

    from gui.settings_dialog import SettingsDialog

    async def fake_ping():
        return True, "通了"

    label = _FakeLabel()
    stub = types.SimpleNamespace()
    SettingsDialog._run_async(stub, fake_ping, label)
    assert label.wait(), "后台线程没跑完"
    assert label.text.startswith("✓"), f"实际显示: {label.text!r}"
    assert "通了" in label.text


def test_run_async_still_handles_plain_sync_function():
    """「测试企业微信连接」传进来的是普通函数，不能只支持协程。"""
    import types

    from gui.settings_dialog import SettingsDialog

    label = _FakeLabel()
    stub = types.SimpleNamespace()
    SettingsDialog._run_async(stub, lambda: (False, "地址不通"), label)
    assert label.wait()
    assert label.text.startswith("✗")
    assert "地址不通" in label.text


def test_run_async_reports_exception_instead_of_crashing():
    import types

    from gui.settings_dialog import SettingsDialog

    def boom():
        raise RuntimeError("爆炸")

    label = _FakeLabel()
    stub = types.SimpleNamespace()
    SettingsDialog._run_async(stub, boom, label)
    assert label.wait()
    assert label.text.startswith("✗")
    assert "RuntimeError" in label.text


def test_test_llm_wires_ping():
    src = (ROOT / "gui" / "settings_dialog.py").read_text(encoding="utf-8")
    assert "self._run_async(llm_client.ping" in src


def test_run_async_detects_awaitable():
    src = (ROOT / "gui" / "settings_dialog.py").read_text(encoding="utf-8")
    assert "inspect.isawaitable" in src
    assert "asyncio.run" in src


# ── 3. .env 只能从程序目录读，不能往上翻 ────────────────────────────────

def _code_lines(rel: str) -> str:
    """去掉注释行 —— 注释里会提到错误写法，别把注释当代码断言。"""
    text = (ROOT / rel).read_text(encoding="utf-8")
    return "\n".join(ln for ln in text.splitlines()
                     if not ln.strip().startswith("#"))


def test_main_pins_dotenv_to_app_dir():
    """无参 load_dotenv() 会向上级目录找 .env，会把别人的密钥读进来。

    实测：程序目录在某个带 .env 的目录下时，密钥框直接显示了那份 .env 的内容。
    """
    src = _code_lines("main.py")
    assert "load_dotenv(" in src
    assert "load_dotenv()" not in src, "必须显式传路径"
    assert "load_dotenv(_ENV_FILE" in src
    assert "_ENV_FILE = _app_dir()" in src


# ── 4. 「预设」下拉框的文字曾经被盖掉 ──────────────────────────────────

def test_preset_combobox_is_child_of_its_row():
    """`ttk.Combobox(box)` + `pack(in_=prow)` 会让文字和箭头被 prow 底色盖掉，
    只剩一个空框（实测框内深色像素数 = 0）。父容器必须是 prow。"""
    src = _code_lines("gui/settings_dialog.py")
    assert 'ttk.Combobox(prow, state="readonly"' in src
    assert "in_=prow" not in src, "跨父容器 pack 会盖掉下拉框文字"


def test_readonly_combobox_style_is_flat_with_entries():
    src = (ROOT / "gui" / "theme.py").read_text(encoding="utf-8")
    assert 'style.map("TCombobox"' in src
    assert '"readonly"' in src
