# tests/test_kb_edit.py
"""资料库：问题/答案拆列、手动改一条、删除、撤销。

**不加载嵌入模型**：把 ``pipeline.embedder`` 换成桩（保留真实的 chunk_id 算法，
因为"改内容就换 id"这条逻辑全压在它身上）。
"""

import hashlib
import sys
import types

import pytest

from rag import kb_history, kb_tools


# ── 桩：嵌入 ──────────────────────────────────────────────────────────

def _real_chunk_id(text: str) -> str:
    h = hashlib.md5(text.encode()).hexdigest()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


@pytest.fixture
def fake_embedder(monkeypatch):
    calls = []
    mod = types.ModuleType("pipeline.embedder")
    mod.chunk_id = _real_chunk_id

    def embed_and_store(chunks, qdrant, collection_name="knowledge_base", source="unknown"):
        from qdrant_client.models import PointStruct
        pts = [PointStruct(id=_real_chunk_id(c), vector=[0.0, 0.0, 0.0, 1.0],
                           payload={"text": c, "source": source, "index": i,
                                    "chunk_id": _real_chunk_id(c)})
               for i, c in enumerate(chunks)]
        qdrant.upsert(collection_name=collection_name, points=pts)
        calls.append({"chunks": list(chunks), "source": source, "n": len(pts)})
        return len(pts)

    mod.embed_and_store = embed_and_store
    monkeypatch.setitem(sys.modules, "pipeline.embedder", mod)
    return calls


@pytest.fixture
def client(tmp_path):
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, VectorParams
    c = QdrantClient(path=str(tmp_path / "q"))
    c.create_collection("kb", vectors_config=VectorParams(size=4, distance=Distance.COSINE))
    yield c
    c.close()


@pytest.fixture
def history_file(tmp_path, monkeypatch):
    p = tmp_path / "kb_history.jsonl"
    monkeypatch.setattr(kb_history, "HISTORY_PATH", p)
    return p


def _seed(client, text, source="local_pipeline", collection="kb"):
    from qdrant_client.models import PointStruct
    pid = _real_chunk_id(text)
    client.upsert(collection_name=collection, points=[
        PointStruct(id=pid, vector=[0.0, 0.0, 0.0, 1.0],
                    payload={"text": text, "source": source, "chunk_id": pid})])
    return str(pid)


FAQ = "客户问题: 索尼A7M4日租多少钱\n销售回答: 日租 120 元，押金 2000 元。"


# ── 问答拆分 ──────────────────────────────────────────────────────────

def test_split_faq():
    q, a = kb_tools.split_faq(FAQ)
    assert q == "索尼A7M4日租多少钱"
    assert a == "日租 120 元，押金 2000 元。"


def test_split_faq_keeps_multiline_answer():
    q, a = kb_tools.split_faq("客户问题: 配件租金\n销售回答: 第一行\n第二行")
    assert q == "配件租金"
    assert a == "第一行\n第二行"


def test_split_faq_non_faq():
    assert kb_tools.split_faq("这是一段普通资料，没有问答结构。") == ("", "")


def test_split_faq_only_question():
    assert kb_tools.split_faq("客户问题: 只写了问题") == ("只写了问题", "")


def test_make_faq_roundtrip():
    text = kb_tools.make_faq("能租无人机吗", "可以，日租 180 元。")
    assert text.startswith("客户问题: ")
    assert "\n销售回答: " in text
    assert kb_tools.split_faq(text) == ("能租无人机吗", "可以，日租 180 元。")


def test_make_faq_flattens_newline_in_question():
    """问题里的换行会把格式搞乱（答案就认不出来了）。"""
    text = kb_tools.make_faq("两行\n问题", "答案")
    assert kb_tools.split_faq(text) == ("两行 问题", "答案")


def test_make_faq_strips_whitespace():
    assert kb_tools.make_faq("  问  ", "  答  ") == "客户问题: 问\n销售回答: 答"


# ── 列表读出问题 + 答案 ──────────────────────────────────────────────

def test_list_entries_splits_question_and_answer(client):
    _seed(client, FAQ)
    rows = kb_tools.list_entries(client, "kb")
    assert rows[0]["question"] == "索尼A7M4日租多少钱"
    assert rows[0]["answer"] == "日租 120 元，押金 2000 元。"
    assert rows[0]["faq"] is True
    assert rows[0]["text"] == FAQ


def test_list_entries_marks_non_faq(client):
    _seed(client, "视频转录的一大段文字")
    rows = kb_tools.list_entries(client, "kb")
    assert rows[0]["faq"] is False
    assert rows[0]["question"] == ""
    assert rows[0]["text"] == "视频转录的一大段文字"


def test_list_entries_keyword_searches_answer_too(client):
    """搜"免押"要能搜到 —— 它只在答案里。"""
    _seed(client, FAQ)
    assert kb_tools.list_entries(client, "kb", keyword="押金")
    assert kb_tools.list_entries(client, "kb", keyword="不存在的词") == []


def test_get_entry(client):
    pid = _seed(client, FAQ)
    e = kb_tools.get_entry(client, pid, "kb")
    assert e["id"] == pid and e["faq"] is True


def test_get_entry_missing(client):
    assert kb_tools.get_entry(client, _real_chunk_id("没有这条"), "kb") is None


# ── 改一条 ────────────────────────────────────────────────────────────

def test_update_entry_replaces_and_reindexes(client, fake_embedder):
    pid = _seed(client, FAQ)
    new = kb_tools.make_faq("索尼A7M4日租多少钱", "日租 100 元，押金 2000 元。")

    res = kb_tools.update_entry(client, pid, new, "kb")

    assert res["text"] == new
    assert res["source"] == "local_pipeline"          # 来源要留着
    assert fake_embedder[-1]["chunks"] == [new]       # 走的是同一条嵌入路径
    assert kb_tools.get_entry(client, pid, "kb") is None    # 旧点没了
    assert kb_tools.get_entry(client, res["id"], "kb")["text"] == new
    assert kb_tools.count(client, "kb") == 1          # 没有变成两条


def test_update_entry_id_changes_with_content(client, fake_embedder):
    pid = _seed(client, FAQ)
    res = kb_tools.update_entry(client, pid, FAQ + " 补充一句。", "kb")
    assert res["id"] != pid


def test_update_entry_same_text_keeps_one_point(client, fake_embedder):
    """文本没变 → id 没变 → 不能把自己删掉。"""
    pid = _seed(client, FAQ)
    res = kb_tools.update_entry(client, pid, FAQ, "kb")
    assert res["id"] == pid
    assert kb_tools.count(client, "kb") == 1


def test_update_entry_rejects_empty(client, fake_embedder):
    pid = _seed(client, FAQ)
    with pytest.raises(ValueError, match="不能为空"):
        kb_tools.update_entry(client, pid, "   \n ", "kb")


def test_update_entry_missing_point(client, fake_embedder):
    with pytest.raises(ValueError, match="已经不在了"):
        kb_tools.update_entry(client, _real_chunk_id("没有"), FAQ, "kb")


def test_delete_entry(client):
    pid = _seed(client, FAQ)
    assert kb_tools.delete_entry(client, pid, "kb") is True
    assert kb_tools.count(client, "kb") == 0


# ── 新增一条 ──────────────────────────────────────────────────────────

def test_add_entry_faq(client, fake_embedder):
    res = kb_tools.add_entry(client, "能租无人机吗", "可以，日租 180 元。", collection="kb")
    assert res["chunks"] == 1
    e = kb_tools.get_entry(client, res["id"], "kb")
    assert e["question"] == "能租无人机吗"
    assert e["answer"] == "可以，日租 180 元。"
    assert e["source"] == "手动添加"


def test_add_entry_plain_text_when_no_question(client, fake_embedder):
    res = kb_tools.add_entry(client, "", "整段说明文字。", collection="kb")
    e = kb_tools.get_entry(client, res["id"], "kb")
    assert e["text"] == "整段说明文字。"
    assert e["faq"] is False


def test_add_entry_rejects_empty(client, fake_embedder):
    with pytest.raises(ValueError, match="不能为空"):
        kb_tools.add_entry(client, "  ", "   ", collection="kb")


def test_add_entry_uses_same_embedding_path(client, fake_embedder):
    """手动加的条目必须和导入的条目走同一条嵌入路径，否则检索行为会不一致。"""
    kb_tools.add_entry(client, "问", "答", collection="kb")
    assert fake_embedder[-1]["chunks"] == ["客户问题: 问\n销售回答: 答"]


# ── 历史 / 撤销 ───────────────────────────────────────────────────────

def test_history_record_and_versions(history_file):
    kb_history.record("id1", "旧内容", "src", keep=5)
    kb_history.record("id1", "更新一点", "src", keep=5)
    v = kb_history.versions("id1")
    assert [x["text"] for x in v] == ["更新一点", "旧内容"]      # 最新在前


def test_history_isolated_per_entry(history_file):
    kb_history.record("a", "A 的旧内容")
    kb_history.record("b", "B 的旧内容")
    assert [x["text"] for x in kb_history.versions("a")] == ["A 的旧内容"]
    assert [x["text"] for x in kb_history.versions("b")] == ["B 的旧内容"]


def test_history_keeps_only_n_versions(history_file):
    for i in range(8):
        kb_history.record("id1", f"第{i}版", keep=5)
    texts = [x["text"] for x in kb_history.versions("id1")]
    assert len(texts) == 5
    assert texts[0] == "第7版"                     # 新的留下
    assert "第0版" not in texts                     # 老的滚掉


def test_history_rotation_does_not_touch_other_entries(history_file):
    kb_history.record("other", "别人的内容", keep=5)
    for i in range(8):
        kb_history.record("id1", f"第{i}版", keep=5)
    assert [x["text"] for x in kb_history.versions("other")] == ["别人的内容"]


def test_history_pop_last_consumes(history_file):
    kb_history.record("id1", "第一版")
    kb_history.record("id1", "第二版")
    assert kb_history.pop_last("id1")["text"] == "第二版"
    assert kb_history.pop_last("id1")["text"] == "第一版"
    assert kb_history.pop_last("id1") is None       # 退到底了


def test_history_survives_corrupt_line(history_file):
    history_file.write_text('{"id":"id1","text":"好的"}\n不是 json\n', encoding="utf-8")
    assert [x["text"] for x in kb_history.versions("id1")] == ["好的"]


def test_history_missing_file(history_file):
    assert kb_history.versions("谁") == []
    assert kb_history.pop_last("谁") is None


def test_keep_versions_from_cfg():
    assert kb_history.keep_versions({"kb": {"history_versions": 3}}) == 3
    assert kb_history.keep_versions({}) == kb_history.DEFAULT_KEEP
    assert kb_history.keep_versions({"kb": {"history_versions": "坏了"}}) == 5
    assert kb_history.keep_versions({"kb": {"history_versions": 999}}) == 50   # 上限
    assert kb_history.keep_versions({"kb": {"history_versions": 0}}) == 1


def test_undo_restores_previous_text(client, fake_embedder, history_file):
    pid = _seed(client, FAQ)
    new = kb_tools.make_faq("索尼A7M4日租多少钱", "日租 100 元，押金 2000 元。")

    # 编辑流程：历史挂在**改完之后的新 id** 上
    new_id = _real_chunk_id(new)
    kb_history.record(new_id, FAQ, "local_pipeline")
    kb_tools.update_entry(client, pid, new, "kb")
    assert kb_tools.get_entry(client, new_id, "kb")["text"] == new

    res = kb_history.undo(client, new_id, "kb")

    assert res["text"] == FAQ
    assert kb_tools.get_entry(client, res["id"], "kb")["text"] == FAQ
    assert kb_tools.count(client, "kb") == 1


def test_undo_without_history(client, fake_embedder, history_file):
    pid = _seed(client, FAQ)
    with pytest.raises(ValueError, match="没有可撤销"):
        kb_history.undo(client, pid, "kb")


def test_undo_twice_walks_back_two_steps(client, fake_embedder, history_file):
    """连点两次撤销能退两步 —— 这就是历史挂在"新 id"上的原因。"""
    v0 = FAQ
    v1 = kb_tools.make_faq("索尼A7M4日租多少钱", "日租 100 元，押金 2000 元。")
    v2 = kb_tools.make_faq("索尼A7M4日租多少钱", "日租 80 元，押金 2000 元。")

    pid = _seed(client, v0)
    id1 = _real_chunk_id(v1)
    kb_history.record(id1, v0)
    kb_tools.update_entry(client, pid, v1, "kb")

    id2 = _real_chunk_id(v2)
    kb_history.record(id2, v1)
    kb_tools.update_entry(client, id1, v2, "kb")

    r1 = kb_history.undo(client, id2, "kb")        # v2 → v1
    assert r1["text"] == v1
    r2 = kb_history.undo(client, r1["id"], "kb")   # v1 → v0
    assert r2["text"] == v0
    assert kb_tools.count(client, "kb") == 1


def test_undo_when_point_vanished_reinserts(client, fake_embedder, history_file):
    kb_history.record("没了这条", FAQ, "src")
    res = kb_history.undo(client, "没了这条", "kb")
    assert res["chunks"] == 1
    assert kb_tools.get_entry(client, res["id"], "kb")["text"] == FAQ


def test_clear_history(history_file):
    kb_history.record("id1", "x")
    assert history_file.is_file()
    kb_history.clear()
    assert not history_file.is_file()


# ── 界面接线（读源码） ────────────────────────────────────────────────

def _src(name):
    from pathlib import Path
    return (Path(__file__).resolve().parent.parent / "gui" / name).read_text(encoding="utf-8")


def test_kb_list_shows_question_and_answer_columns():
    s = _src("kb_dialog.py")
    assert '"question", "客户问题"' in s
    assert '"answer", "销售回答"' in s


def test_kb_page_has_single_entry_operations():
    s = _src("kb_dialog.py")
    for label in ("新增一条…", "编辑这条…", "看全文", "撤销上次修改", "删除这条"):
        assert label in s
    assert "_kb_view" in s and "_kb_undo" in s and "_kb_delete" in s and "_kb_add" in s


def test_double_click_opens_full_text():
    s = _src("kb_dialog.py")
    assert '"<Double-1>"' in s


def test_probe_page_shows_answer_not_only_question():
    s = _src("kb_dialog.py")
    assert "对应的销售回答" in s


def test_editor_has_separate_question_and_answer_boxes():
    s = _src("kb_edit.py")
    assert "客户问题" in s and "销售回答" in s
    assert "make_faq" in s


def test_editor_warns_before_reindex():
    s = _src("kb_edit.py")
    assert "askyesno" in s
    assert "不得编" in s or "必须来自" in s      # 提醒别编数字
    assert "kb_history.record" in s


def test_editor_supports_add_mode():
    s = _src("kb_edit.py")
    assert "新增资料" in s
    assert "add_entry" in s
    assert "self.adding" in s
