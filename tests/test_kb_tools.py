"""资料库浏览 + 「试问一句」检索验证。

没有"试问一句"，用户改资料库就是**盲改** —— 这是判断"改对没有"的唯一可靠方式。
"""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from rag import kb_tools

ROOT = Path(__file__).resolve().parent.parent


def _client(points, scores=None):
    """假的 Qdrant 客户端：scroll 返回条目，query_points 返回检索结果。"""
    c = MagicMock()
    c.count.return_value = MagicMock(count=len(points))
    c.scroll.return_value = (points, None)
    if scores is not None:
        c.query_points.return_value = MagicMock(points=scores)
    return c


def _pt(i, text, source=""):
    p = MagicMock()
    p.id = i
    p.payload = {"text": text, "source": source}
    return p


# ── list_entries ──────────────────────────────────────────────────────

def test_list_entries_returns_text_and_source():
    c = _client([_pt(1, "富士 X-T5 日租 95 元", "价目表.csv"),
                 _pt(2, "支持开票", "FAQ.txt")])
    got = kb_tools.list_entries(c)
    assert [g["text"] for g in got] == ["富士 X-T5 日租 95 元", "支持开票"]
    assert got[0]["source"] == "价目表.csv"


def test_list_entries_keyword_filters_current_page():
    c = _client([_pt(1, "富士 X-T5 日租 95 元"), _pt(2, "支持开票")])
    got = kb_tools.list_entries(c, keyword="富士")
    assert len(got) == 1 and "富士" in got[0]["text"]


def test_list_entries_never_opens_its_own_client():
    """Qdrant 本地文件模式不能开第二个客户端 —— 必须用传进来的那个。"""
    src = (ROOT / "rag/kb_tools.py").read_text(encoding="utf-8")
    assert "= QdrantClient(" not in src, "kb_tools 绝不能自己 new 客户端"
    assert "QdrantClient" not in src.split('"""')[0], "也不该在代码层 import 它"


def test_list_entries_survives_broken_client():
    c = MagicMock()
    c.scroll.side_effect = RuntimeError("boom")
    assert kb_tools.list_entries(c) == []


def test_count_survives_broken_client():
    c = MagicMock()
    c.count.side_effect = RuntimeError("boom")
    assert kb_tools.count(c) == 0


# ── probe（试问一句）──────────────────────────────────────────────────

def test_probe_uses_the_same_embed_and_search_as_production(monkeypatch):
    """必须走线上同一套 embed_query + search，否则这里看到的分数不可信。"""
    called = {}

    def fake_embed(q):
        called["q"] = q
        return [0.1] * 8

    def fake_search(qv, client, collection_name="knowledge_base", top_k=5):
        called["search"] = (collection_name, top_k)
        h = MagicMock()
        h.score = 0.72
        h.payload = {"text": "富士 X-T5 日租 95 元", "source": "价目表.csv"}
        return [h]

    monkeypatch.setattr("rag.embed_query.embed_query", fake_embed)
    monkeypatch.setattr("rag.retriever.search", fake_search)
    got = kb_tools.probe(MagicMock(), "富士多少钱", top_k=3)
    assert called["q"] == "富士多少钱"
    assert called["search"] == ("knowledge_base", 3)
    assert got[0]["score"] == 0.72 and got[0]["source"] == "价目表.csv"


def test_probe_empty_question_returns_nothing():
    assert kb_tools.probe(MagicMock(), "   ") == []


def test_probe_survives_failure(monkeypatch):
    monkeypatch.setattr("rag.embed_query.embed_query",
                        MagicMock(side_effect=RuntimeError("模型没加载")))
    assert kb_tools.probe(MagicMock(), "随便") == []


# ── 结论文案要和线上门槛一致 ──────────────────────────────────────────

def test_verdict_matches_dispatch_threshold():
    assert "直" in kb_tools.verdict(0.80, 0.65)
    assert "转人工" in kb_tools.verdict(0.50, 0.65)
    assert "转人工" in kb_tools.verdict(0.20, 0.65)


# ── 界面接线 ──────────────────────────────────────────────────────────

def test_dialog_has_kb_and_probe_pages():
    src = (ROOT / "gui/kb_dialog.py").read_text(encoding="utf-8")
    assert 'nb.add(f_kb, text="资料库")' in src
    assert 'nb.add(f_probe, text="试问一句")' in src
    assert "def _kb_load" in src and "def _probe_run" in src


def test_dialog_reuses_caller_qdrant_client():
    src = (ROOT / "gui/kb_dialog.py").read_text(encoding="utf-8")
    assert "self.qdrant = qdrant" in src
    m = (ROOT / "main.py").read_text(encoding="utf-8")
    assert 'qdrant=getattr(responder, "qdrant", None)' in m


def test_dialog_builds_with_pages(tmp_path):
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except Exception as e:
        pytest.skip(f"没有可用的显示环境: {e}")
    root.withdraw()
    try:
        from gui.kb_dialog import KbDialog
        c = _client([_pt(1, "富士 X-T5 日租 95 元", "价目表.csv")])
        dlg = KbDialog(root, {}, qdrant=c)
        root.update()
        assert dlg._kb_tree.get_children(), "资料库页应当列出条目"
        dlg.win.destroy()
    finally:
        root.destroy()
