"""提示词外置 + 前端编辑：改完立即生效，且不能被改坏。

两个必须挡住的事：
1. 占位符 `{context}` 被删 → `str.format` 抛 KeyError → 生成回复直接崩
2. 安全规则被删 → guard 把大量回复拦成"需要人工处理"，用户以为程序坏了
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
    assert "MainWindow(on_settings=_open_settings, on_kb=_open_kb)" in m
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
