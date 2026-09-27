# tests/test_unanswered.py
"""「最常转人工的问题」排行榜。

转人工 = 资料库里没写这条。记下来排个序，店主照单补资料库，
比天天在「待人工」里救火强。
"""

import json
from pathlib import Path

import pytest

from rag import unanswered


@pytest.fixture
def store(tmp_path, monkeypatch):
    p = tmp_path / "unanswered.json"
    monkeypatch.setattr(unanswered, "PATH", str(p))
    return p


# ── 记与合并 ──────────────────────────────────────────────────────────

def test_record_then_top(store):
    unanswered.record("房租多少钱", "low_confidence", "张三@微信")
    got = unanswered.top(10)
    assert len(got) == 1
    assert got[0]["question"] == "房租多少钱"
    assert got[0]["count"] == 1
    assert got[0]["reason"] == "low_confidence"
    assert store.is_file()


def test_same_question_with_punctuation_counts_once(store):
    """「房租多少钱？」和「房租多少钱」是同一个问题，不能各占一行。"""
    unanswered.record("房租多少钱？", "low_confidence")
    unanswered.record("房租多少钱", "low_confidence")
    unanswered.record("房租 多少钱！", "low_confidence")
    got = unanswered.top(10)
    assert len(got) == 1, f"同一句话被拆成了多条: {[g['question'] for g in got]}"
    assert got[0]["count"] == 3


def test_different_questions_are_separate(store):
    unanswered.record("房租多少钱")
    unanswered.record("几楼")
    assert unanswered.count() == 2
    assert unanswered.total() == 2


def test_top_sorted_by_count(store):
    for _ in range(3):
        unanswered.record("押金多少")
    unanswered.record("几楼")
    unanswered.record("几楼")
    got = unanswered.top(10)
    assert [g["question"] for g in got] == ["押金多少", "几楼"]
    assert [g["count"] for g in got] == [3, 2]


def test_top_limit(store):
    for i in range(5):
        unanswered.record(f"问题{i}甲")
    assert len(unanswered.top(2)) == 2
    assert unanswered.top(0) == []


def test_latest_reason_and_name_kept(store):
    unanswered.record("灯光暗不暗", "low_confidence", "张三")
    unanswered.record("灯光暗不暗", "hallucinated_number: {'6'}", "张三@微信")
    got = unanswered.top(1)[0]
    assert got["count"] == 2
    assert "hallucinated_number" in got["reason"], "要留最近一次的原因"
    assert got["name"] == "张三@微信"


def test_blank_question_ignored(store):
    unanswered.record("")
    unanswered.record("   ")
    unanswered.record(None)
    assert unanswered.count() == 0
    assert not store.exists()


# ── 落盘 / 坏文件 ─────────────────────────────────────────────────────

def test_survives_reload(store):
    unanswered.record("押金多少", "low_confidence", "李四")
    # 重新从磁盘读（模拟重启）
    got = unanswered.top(1)[0]
    assert got["count"] == 1
    saved = json.loads(store.read_text(encoding="utf-8"))
    assert saved["items"], "排行要落盘"


def test_corrupt_file_ignored(store):
    store.write_text("{坏掉的", encoding="utf-8")
    assert unanswered.count() == 0          # 不该炸
    unanswered.record("押金多少")            # 还能继续记
    assert unanswered.count() == 1


def test_clear(store):
    unanswered.record("押金多少")
    unanswered.clear()
    assert unanswered.count() == 0
    assert unanswered.top(10) == []


# ── 「已补进资料库」标记 ──────────────────────────────────────────────

def test_mark_done_and_todo_count(store):
    unanswered.record("押金多少")
    unanswered.record("几楼")
    assert unanswered.todo_count() == 2
    assert unanswered.mark_done("押金多少") is True
    assert unanswered.todo_count() == 1
    item = [i for i in unanswered.top(10) if i["question"] == "押金多少"][0]
    assert item.get("done_ts"), "补过资料库的要标上"


def test_mark_done_unknown_question(store):
    assert unanswered.mark_done("没记过的问题") is False


def test_same_question_again_goes_back_to_todo(store):
    """补过资料库**又**转人工了 → 说明那条资料没解决问题，退回「待补」。"""
    unanswered.record("押金多少")
    unanswered.mark_done("押金多少")
    assert unanswered.todo_count() == 0
    unanswered.record("押金多少？")           # 又转了一次
    assert unanswered.todo_count() == 1
    assert unanswered.top(1)[0]["count"] == 2


# ── 界面接线（只查源码，不弹 Tk 窗口）────────────────────────────────

def test_why_text_is_human_readable():
    from gui.unanswered_dialog import why_text
    assert why_text("low_confidence") == "资料里有，但没把握"
    assert "没找到" in why_text("retrieval_low_confidence")
    assert "数字" in why_text("hallucinated_number: {'6000'}")
    assert "可能" in why_text("uncertainty_keyword: 可能")
    assert why_text("") == "(未记录)"
    assert why_text("某种新原因") == "某种新原因"


def test_kb_dialog_has_ranking_button():
    s = _src("gui/kb_dialog.py")
    assert "最常转人工的问题…" in s
    assert "open_unanswered(" in s
    # 加进去的时候要把这句话预填进"客户问题"框
    d = _src("gui/unanswered_dialog.py")
    assert 'prefill={"question": q}' in d
    e = _src("gui/kb_edit.py")
    assert "prefill" in e


def _src(rel):
    return (Path(__file__).resolve().parent.parent / rel).read_text(encoding="utf-8")


# ── 接线：转人工的地方都要记 ──────────────────────────────────────────

def test_main_records_on_both_paths():
    s = _src("main.py")
    assert s.count("unanswered.record(") == 2, "红点路径 + 兜底路径都要记"
    assert s.count("from rag import unanswered") == 1


def test_api_policy_records_on_escalate():
    s = _src("gateway/api_policy.py")
    assert "unanswered.record(" in s
    i = s.index("if result.escalated:")
    assert "unanswered.record(" in s[i:i + 400], "要在 escalate 分支里记"
