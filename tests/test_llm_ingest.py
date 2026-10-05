# tests/test_llm_ingest.py
"""LLM 整理导入的**校验器**：LLM 只许重构，不许创作。

这是价格库 —— LLM 把 4000 写成 40000、"日租 90"写成"月租 90"，
写进库之后**护栏反而会保护这个错数字**（"数字必须有出处"会把被污染的库当依据）。
所以自动校验不是锦上添花，是这条链路能不能用的前提。

踩过的两个真实坑（都在下面钉住）：
  1. 只比裸数字会被「押金 4000 元，即 4 万元？」骗过（"4"在 4000 里出现过）
     → 改成比「数字+单位」
  2. 「索尼 A7M4 日租 90 元」被读成单位配对「4日」（A7M4 的 4 + 日租的日）
     → 加后行断言 (?<![\\dA-Za-z])
"""
import pytest

from scripts.llm_ingest import check, parse_pairs

SRC = ("索尼 A7M4 日租 90 元，押金 4000 元，3300 万像素，支持 4K 60 帧。"
       "租期 3 天起，7 天以上有折扣。营业 10 点到 19 点。")


def _ok(answer):
    return check(SRC, [("随便问", answer)])[0][2] == []


@pytest.mark.parametrize("answer", [
    "索尼 A7M4 日租 90 元。",
    "押金 4000 元。",
    "3300 万像素。",
    "支持 4K 60 帧。",
    "租期 3 天起，7 天以上有折扣。",
    "营业 10 点到 19 点。",
])
def test_faithful_answers_pass(answer):
    assert _ok(answer), "%r 应通过" % answer


@pytest.mark.parametrize("answer,why", [
    ("索尼 A7M4 日租 900 元。", "数字改了"),
    ("支持 4K 120 帧。", "规格编的"),
    ("押金 4000 元，即 4 万元？", "数字+单位对不上"),
    ("索尼 A7M400 日租 90 元。", "型号编的"),
])
def test_fabricated_answers_are_blocked(answer, why):
    problems = check(SRC, [("随便问", answer)])[0][2]
    assert problems, "%r（%s）该被拦下" % (answer, why)


def test_bare_number_check_is_not_enough():
    """只比裸数字会漏 —— 这条就是为什么要比「数字+单位」。"""
    problems = check(SRC, [("q", "押金 4000 元，即 4 万元？")])[0][2]
    assert any("单位" in p for p in problems)


def test_model_digit_is_not_a_quantity():
    """型号里的数字不能被当数量（否则「A7M4 日租 90 元」会被误杀成「4日」）。"""
    assert _ok("索尼 A7M4 日租 90 元。")


def test_empty_question_is_blocked():
    assert check(SRC, [("", "日租 90 元。")])[0][2]


def test_too_short_answer_is_blocked():
    assert check(SRC, [("q", "90")])[0][2]


# ── 输出解析 ─────────────────────────────────────────────────────────

def test_parse_pairs_basic():
    out = parse_pairs("客户问题: A7M4多少钱\n销售回答: 日租 90 元\n"
                      "---\n客户问题: 押金多少\n销售回答: 4000 元")
    assert out == [("A7M4多少钱", "日租 90 元"), ("押金多少", "4000 元")]


def test_parse_pairs_strips_separators():
    """分隔线（---/===）不是回答内容，不能被拼进答案尾部。"""
    out = parse_pairs("客户问题: 甲\n销售回答: 乙\n---")
    assert out == [("甲", "乙")], out


def test_parse_pairs_joins_multiline_answer():
    out = parse_pairs("客户问题: 甲\n销售回答: 第一句\n第二句")
    assert out == [("甲", "第一句第二句")]


def test_parse_pairs_ignores_preamble():
    out = parse_pairs("好的，整理如下：\n客户问题: 甲\n销售回答: 乙")
    assert out == [("甲", "乙")]


# ── 批量整理（LLM 用假的，不花 token）────────────────────────────────

def test_build_entries_marks_bad_ones(monkeypatch):
    """整批整理：能过的过、改坏数字的标红，且**一条坏的不影响其它条**。"""
    import asyncio

    from rag import llm_ingest as li

    async def fake(chunk, model=None):
        return [("A7M4日租多少", "索尼 A7M4 日租 90 元。"),
                ("A7M4押金多少", "押金 4000 元，即 4 万元？")]

    monkeypatch.setattr(li, "restructure", fake)
    entries = asyncio.run(li.build_entries(["索尼 A7M4 日租 90 元，押金 4000 元。"]))
    assert [e["ok"] for e in entries] == [True, False]
    assert "单位" in " ".join(entries[1]["problems"])


def test_build_entries_survives_llm_failure(monkeypatch):
    """某一块 LLM 调用失败 → 记一条错误条目，不炸掉整批。"""
    import asyncio

    from rag import llm_ingest as li

    async def boom(chunk, model=None):
        raise RuntimeError("接口 500")

    monkeypatch.setattr(li, "restructure", boom)
    entries = asyncio.run(li.build_entries(["随便一段原文"]))
    assert len(entries) == 1 and entries[0]["ok"] is False
    assert "LLM 调用失败" in entries[0]["problems"][0]


def test_build_entries_reports_progress(monkeypatch):
    import asyncio

    from rag import llm_ingest as li

    async def fake(chunk, model=None):
        return [("问", "回答内容够长")]

    monkeypatch.setattr(li, "restructure", fake)
    seen = []
    asyncio.run(li.build_entries(["a", "b", "c"],
                                 on_progress=lambda d, t: seen.append((d, t))))
    assert seen == [(1, 3), (2, 3), (3, 3)]


def test_to_faq_roundtrips_through_split():
    from rag.kb_tools import split_faq
    from rag.llm_ingest import to_faq
    text = to_faq({"question": "A7M4多少钱", "answer": "日租 90 元"})
    assert split_faq(text) == ("A7M4多少钱", "日租 90 元")


def test_summary_counts():
    from rag.llm_ingest import summary
    out = summary([{"chunk": "x", "ok": True}, {"chunk": "x", "ok": False},
                   {"chunk": "y", "ok": True}])
    assert out == {"total": 3, "ok": 2, "bad": 1, "chunks": 2}


def test_extra_payload_is_merged_but_source_kept():
    """`extra` 只加字段，**不能动 source** —— 它是文件名，删除/替换索引靠它精确匹配。"""
    import inspect

    from pipeline import embedder
    src = inspect.getsource(embedder.embed_and_store)
    assert '"source": source' in src
    assert "payload.update(extra)" in src


def test_gui_dialog_refuses_multiple_files():
    """一次只整理一个文件：source 要精确对应文件名，混着多个就说不清来源。"""
    import pytest
    from gui.llm_ingest_dialog import LlmIngestDialog
    with pytest.raises(ValueError):
        LlmIngestDialog.__new__(LlmIngestDialog).__init__(
            None, ["a.txt", "b.txt"])
