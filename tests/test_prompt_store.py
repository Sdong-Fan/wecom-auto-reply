"""提示词外置 + 前端编辑：改完立即生效，且不能被改坏。

必须挡住的三件事：
1. **写了程序不认识的花括号**（`{foo}` / 正文里随手一个 `{`）→ `str.format`
   抛 KeyError → 生成报错 → 机器人变成"全部转人工"，而且界面不报错
2. `{context}` 被删 → 知识片段填不进去，机器人只能凭印象答
3. 安全规则被删 → guard 把大量回复拦成"需要人工处理"，用户以为程序坏了
"""
import json
from pathlib import Path

import pytest

from rag import prompt_store as ps

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _tmp_prompts(tmp_path, monkeypatch):
    """每个用例用独立的 prompts 目录，别动仓库里的真文件。"""
    monkeypatch.setattr(ps, "PROMPTS_DIR", tmp_path)
    yield tmp_path


# ── 读取：文件优先、回退出厂默认 ──────────────────────────────────────

def test_falls_back_to_default_when_no_file():
    text = ps.get("system")
    assert "{context}" in text
    assert ps.is_customized("system") is False


def test_file_wins_over_default():
    ps.set_prompt("system", "自定义内容 {context} 结束")
    assert ps.get("system").startswith("自定义内容")
    assert ps.is_customized("system") is True


def test_reset_restores_default():
    ps.set_prompt("system", "自定义 {context}")
    ps.reset("system")
    assert ps.is_customized("system") is False
    assert ps.get("system") == ps._default("system")


def test_ensure_files_creates_all_but_does_not_overwrite():
    created = ps.ensure_files()
    assert set(created) == {"system.md", "smalltalk.md", "hold.md",
                            "tone_samples.md", "welcome.md", "nontext.md"}
    ps.set_prompt("system", "我改过的 {context}")
    assert ps.ensure_files() == [], "已存在的文件不能被覆盖"
    assert ps.get("system").startswith("我改过的")


# ── 校验：拦住会把程序搞崩的改动 ──────────────────────────────────────

def test_missing_placeholder_is_rejected():
    ok, why = ps.validate("system", "没有占位符的提示词")
    assert ok is False and "{context}" in why


def test_empty_is_rejected():
    assert ps.validate("system", "   ")[0] is False
    assert ps.validate("system", "")[0] is False


def test_save_refuses_bad_content():
    ok, why = ps.set_prompt("system", "坏内容")
    assert ok is False
    assert ps.is_customized("system") is False, "校验不过就不能落盘"
    assert "{context}" in ps.get("system"), "读出来还是可用的默认值"


def test_corrupt_file_on_disk_falls_back_instead_of_crashing():
    """用户直接用记事本把占位符删了 —— 不能让生成回复崩。"""
    ps.PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    (ps.PROMPTS_DIR / "system.md").write_text("我把占位符删了", encoding="utf-8")
    text = ps.get("system")
    assert "{context}" in text, "文件坏了要回退出厂默认，而不是原样返回坏内容"


def test_smalltalk_has_no_required_placeholder():
    assert ps.validate("smalltalk", "随便写点什么")[0] is True


# ── 校验：未知花括号必须在**保存时**拦住（运行时报错就晚了） ──────────────
# 背景：validate 只查"必需占位符在不在"，所以 {contex} 拼错、正文里打个 {，
# 都能存进文件；之后每次 build_prompt 的 .format() 抛 KeyError，
# responder 兜成"LLM错误 → 转人工"，用户只看到机器人突然什么都不答了。

def test_unknown_placeholder_is_rejected():
    ok, why = ps.validate("system", "写点东西 {context} 还有 {foo}")
    assert ok is False
    assert "{foo}" in why, "要指出到底是哪个花括号"
    assert "{context}" in why, "要告诉他能用哪些"


def test_stray_brace_in_body_is_rejected():
    """正文里随手一个 { 也是 KeyError（实测 format 会报 ' 100 '）。"""
    assert ps.validate("system", "价格表 { 100 } 加 {context}")[0] is False


def test_unknown_placeholder_does_not_reach_disk():
    ok, _ = ps.set_prompt("system", "拼错的 {contex} 加 {context}")
    assert ok is False
    assert ps.is_customized("system") is False, "校验不过就不能落盘"


def test_unknown_placeholder_on_disk_falls_back():
    """绕过界面直接改文件也不行 —— 读取时同样校验，坏了就回退出厂值。"""
    ps.PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    (ps.PROMPTS_DIR / "system.md").write_text(
        "手写的 {typo} 加 {context}", encoding="utf-8")
    assert ps.get("system") == ps._default("system")


def test_allowed_placeholders_pass():
    ok, why = ps.validate(
        "system", "用 {context} 和 {conversation_history} 和 {tone_samples}")
    assert ok is True, why


def test_smalltalk_allows_only_tone_samples():
    assert ps.validate("smalltalk", "闲聊 {tone_samples}")[0] is True
    assert ps.validate("smalltalk", "闲聊 {typo}")[0] is False


def test_unformatted_prompts_allow_any_brace():
    """占位语/欢迎语不过 format，正文里有花括号无所谓 —— 别误拦。"""
    for name in ("hold", "welcome", "tone_samples", "nontext"):
        assert ps.validate(name, "正文里有个 { 花括号")[0] is True, name


def test_format_never_raises_for_saved_prompts():
    """回归：凡是 validate 放过的 system 提示词，.format() 都不许抛异常。"""
    ps.set_prompt("system", "只用 {context} 和 {tone_samples}")
    text = ps.get("system").format(
        context="知识片段", conversation_history="", tone_samples="口吻")
    assert "知识片段" in text


# ── 口吻样本解析 ──────────────────────────────────────────────────────

def test_parse_tone_samples_ignores_blank_and_comments():
    got = ps.parse_tone_samples("在的\n\n# 注释\n- 好的呀\n* 收到\n")
    assert got == ["在的", "好的呀", "收到"]


def test_parse_tone_samples_respects_limit():
    assert len(ps.parse_tone_samples("\n".join(f"第{i}条" for i in range(30)), limit=5)) == 5


def test_build_tone_block_prefers_file():
    """口吻样本改了要能立刻用在提示词里 —— 这是"换成你自己说的话"的入口。"""
    from rag.smalltalk import build_tone_block
    ps.set_prompt("tone_samples", "我自己说的一句话")
    assert "我自己说的一句话" in build_tone_block()


# ── 接进生成链路 ──────────────────────────────────────────────────────

def test_generator_reads_prompt_from_store():
    src = (ROOT / "rag/generator.py").read_text(encoding="utf-8")
    assert 'get_prompt("system").format(' in src
    assert 'get_prompt("smalltalk")' in src
    assert 'get_prompt("hold")' in src


def test_safety_rules_are_exposed_readonly():
    assert "不许编造" in ps.SAFETY_RULES or "一律不许编造" in ps.SAFETY_RULES
    assert "用词必须确定" in ps.SAFETY_RULES


# ── 界面 ──────────────────────────────────────────────────────────────

def test_kb_button_exists_and_wired():
    w = (ROOT / "gui/main_window.py").read_text(encoding="utf-8")
    m = (ROOT / "main.py").read_text(encoding="utf-8")
    assert 'text="知识库"' in w
    assert "on_kb" in w
    # 2026-10-03：MainWindow 构造参数多了 on_dashboard（数据看板），
    # 所以这里校验"接线还在"，而不是钉死一整行
    assert "on_kb=_open_kb" in m
    assert "on_dashboard=_open_dashboard" in m
    assert "from gui.kb_dialog import open_kb" in m


def test_kb_dialog_has_readonly_safety_block():
    src = (ROOT / "gui/kb_dialog.py").read_text(encoding="utf-8")
    assert "受保护区" in src
    assert 'state="disabled"' in src, "安全规则必须只读"
    assert "SAFETY_RULES" in src


def test_kb_dialog_builds(tmp_path):
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except Exception as e:
        pytest.skip(f"没有可用的显示环境: {e}")
    root.withdraw()
    try:
        from gui.kb_dialog import KbDialog
        dlg = KbDialog(root, {})
        root.update()
        assert dlg._text.get("1.0", tk.END).strip(), "提示词编辑区要有内容"
        assert dlg._text.get("1.0", tk.END).count("{context}") >= 1
        dlg.win.destroy()
    finally:
        root.destroy()
