"""本地直答：问"现在几点/今天几号"读系统时钟，不该问知识库、更不该让模型猜。

用户提的："我问了现在几点，但是现在几点都回答不了吗，直接读取时间不就行了"
"""
import datetime

import pytest

from rag.local_answers import answer_locally, now_line

NOW = datetime.datetime(2026, 9, 25, 1, 12, 30)
# 星期别写死 —— 2026-09-25 其实是星期五，写死会让测试和代码一起错。
WD = "星期" + "一二三四五六日"[NOW.weekday()]


# ── 时间 ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("q", ["现在几点", "现在几点了", "几点了", "现在时间",
                               "现在什么时间", "报一下时间"])
def test_time_questions_answered_locally(q):
    got = answer_locally(q, now=NOW)
    assert got == "现在 1 点 12 分", f"{q!r} → {got!r}"


def test_whole_hour_reads_naturally():
    assert answer_locally("现在几点", now=NOW.replace(minute=0)) == "现在 1 点整"


# ── 日期 / 星期 ───────────────────────────────────────────────────────

@pytest.mark.parametrize("q", ["今天几号", "今天多少号", "几月几号", "今天日期", "现在几号"])
def test_date_questions(q):
    assert answer_locally(q, now=NOW) == "今天是 9 月 25 日"


@pytest.mark.parametrize("q", ["今天星期几", "今天周几", "星期几了", "今天礼拜几"])
def test_weekday_questions(q):
    assert answer_locally(q, now=NOW) == f"今天{WD}"


def test_weekday_takes_priority_over_date():
    """"今天星期几"里也含"今天"，必须答星期而不是日期。"""
    assert answer_locally("今天星期几", now=NOW) == f"今天{WD}"


# ── 关键：不能抢知识库的活 ────────────────────────────────────────────

@pytest.mark.parametrize("q", ["你们几点下班", "营业时间到几点", "几点上班",
                               "现在几点关门", "你们打烊了吗"])
def test_business_hours_questions_go_to_kb(q):
    """问营业/上下班是**知识库**的事，不能被当前时间糊弄过去。"""
    assert answer_locally(q, now=NOW) is None


@pytest.mark.parametrize("q", ["你们有富士吗", "多少钱一天", "押金多少",
                               "在吗", "谢谢", "", "现在几点了你们店里还有人吗"])
def test_other_questions_not_hijacked(q):
    assert answer_locally(q, now=NOW) is None


def test_long_message_not_treated_as_time_question():
    assert answer_locally("现在几点了我还在等你们回复到底什么时候能好", now=NOW) is None


# ── now_line ──────────────────────────────────────────────────────────

def test_now_line_is_groundable_text():
    line = now_line(NOW)
    assert "[当前时间]" in line and "2026-09-25" in line and "01:12" in line
    assert WD in line


# ── 接进 responder ────────────────────────────────────────────────────

def test_responder_answers_clock_without_network():
    """必须**在任何 embedding / 检索 / LLM 之前**返回 —— 之前会白跑一遍还答不上。"""
    import asyncio
    from rag.responder import Responder
    r = Responder.__new__(Responder)
    r._config = None
    r._allow_auto_send = True
    out = asyncio.run(r.generate_reply("客户A", ["现在几点"]))
    assert out.success is True
    assert out.dispatch_level == "auto_send"
    assert "点" in out.reply_text
    assert out.guard_reason.startswith("本地直答")


def test_current_time_is_passed_as_grounding_chunk():
    """当前时间要作为知识片段传下去，否则回复里的时间数字会被 guard 判成编造。"""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "rag/responder.py").read_text(encoding="utf-8")
    assert "chunks = [now_line()] + chunks" in src
    assert "from rag.local_answers import answer_locally, now_line" in src
