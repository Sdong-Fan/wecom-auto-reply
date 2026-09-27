# tests/test_upload.py
"""上传资料进知识库：解析 / 留源 / 行级切块 / 重传替换 / 批量容错。

**不加载嵌入模型**：把 pipeline.embedder 换成桩，只验"解析 + 留源 + 切块 + 分流"。
真正的嵌入由 test_video_qa 那类隔离测试覆盖。
"""

import sys
import types

import pytest

from pipeline import upload as up


@pytest.fixture
def fake_embedder(monkeypatch):
    """桩掉嵌入：记录每次入库的 chunk，返回条数。"""
    calls = []
    mod = types.ModuleType("pipeline.embedder")

    def embed_and_store(chunks, qdrant, collection_name="knowledge_base", source="unknown"):
        calls.append({"chunks": list(chunks), "collection": collection_name,
                      "source": source, "qdrant": qdrant})
        return len(chunks)

    mod.embed_and_store = embed_and_store
    monkeypatch.setitem(sys.modules, "pipeline.embedder", mod)
    return calls


@pytest.fixture
def upload_dir(tmp_path, monkeypatch):
    """源目录换到临时目录（解析/切块测试够用）。"""
    d = tmp_path / "uploaded"
    monkeypatch.setattr(up, "UPLOAD_DIR", d)
    return d


@pytest.fixture
def home(tmp_path, monkeypatch):
    """连 HERE 也换掉：already_home 判定要用到 data/chat_raw。"""
    monkeypatch.setattr(up, "HERE", tmp_path)
    d = tmp_path / "data" / "chat_raw" / "uploaded"
    monkeypatch.setattr(up, "UPLOAD_DIR", d)
    return tmp_path


# ── 格式清单 ──────────────────────────────────────────────────────────

def test_supported_formats():
    assert up.SUPPORTED == (".txt", ".md", ".csv", ".xlsx", ".xlsm", ".xls", ".docx")


def test_legacy_doc_not_in_supported():
    """老格式靠 LEGACY_HINT 给可操作的提示，不混进 SUPPORTED。"""
    assert ".doc" not in up.SUPPORTED
    assert ".wps" not in up.SUPPORTED
    assert ".doc" in up.LEGACY_HINT


# ── 纯文本 ────────────────────────────────────────────────────────────

def test_parse_txt_utf8(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("索尼 A7M4 日租 120 元\n", encoding="utf-8")
    assert "A7M4" in up.parse_file(f)


def test_parse_gbk_txt(tmp_path):
    """中文 Windows 上 GBK 很常见，不能只试 utf-8。"""
    f = tmp_path / "gbk.txt"
    f.write_bytes("佳能 R5 押金 5000 元\n".encode("gbk"))
    assert "佳能 R5" in up.parse_file(f)


def test_parse_md(tmp_path):
    f = tmp_path / "b.md"
    f.write_text("# 价格表\n\n- A7M4：120/天\n", encoding="utf-8")
    assert "价格表" in up.parse_file(f)


def test_txt_has_no_custom_chunks(tmp_path):
    """纯文本交给通用切块器（None），不硬塞行级切块。"""
    f = tmp_path / "a.txt"
    f.write_text("随便什么", encoding="utf-8")
    text, chunks = up.parse_records(f)
    assert chunks is None


def test_parse_unsupported_suffix(tmp_path):
    f = tmp_path / "d.pdf"
    f.write_bytes(b"%PDF-1.4")
    with pytest.raises(ValueError, match="暂不支持"):
        up.parse_file(f)


def test_parse_missing_file(tmp_path):
    with pytest.raises(ValueError, match="不存在"):
        up.parse_file(tmp_path / "nope.txt")


# ── CSV：也按行切 ─────────────────────────────────────────────────────

def test_csv_joins_cells(tmp_path):
    f = tmp_path / "c.csv"
    f.write_text("器材,日租\n索尼A7M4,120\n佳能R5,150\n", encoding="utf-8")
    out = up.parse_file(f)
    assert "器材 | 日租" in out
    assert "索尼A7M4 | 120" in out
    assert "," not in out.splitlines()[0]


def test_csv_rows_become_chunks_with_header(tmp_path):
    """csv 也是表格，整段切会稀释检索；每块要带上表头才看得懂。"""
    f = tmp_path / "c.csv"
    f.write_text("器材,日租\n索尼A7M4,120\n佳能R5,150\n", encoding="utf-8")
    _text, chunks = up.parse_records(f)
    assert chunks == ["索尼A7M4：日租（一天）120",
                      "佳能R5：日租（一天）150"]


def test_csv_readable_text_stays_pipe_table(tmp_path):
    """留源/预览还是看得懂的表格样子，只有进库的块换成句子。"""
    f = tmp_path / "c.csv"
    f.write_text("器材,日租\n索尼A7M4,120\n", encoding="utf-8")
    text, _chunks = up.parse_records(f)
    assert text.splitlines() == ["器材 | 日租", "索尼A7M4 | 120"]


# ── 留源（原文件原样） ────────────────────────────────────────────────

def test_save_source_copies_file(tmp_path, upload_dir):
    src = tmp_path / "价格表.txt"
    src.write_text("A7M4 120", encoding="utf-8")
    dest = up.save_source(src)
    assert dest.parent == upload_dir
    assert dest.read_text(encoding="utf-8") == "A7M4 120"


def test_save_source_never_overwrites(tmp_path, upload_dir):
    src = tmp_path / "价格表.txt"
    src.write_text("第一版", encoding="utf-8")
    a = up.save_source(src)
    src.write_text("第二版", encoding="utf-8")
    b = up.save_source(src)
    assert a != b
    assert a.read_text(encoding="utf-8") == "第一版"
    assert b.read_text(encoding="utf-8") == "第二版"


def test_source_keeps_original_format(tmp_path, home):
    """源必须留**原文件**：存成文本的话，重建索引时表格结构就没了，切块方式跟着变。"""
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    wb.active.append(["器材", "日租"])
    wb.active.append(["索尼A7M4", 120])
    x = tmp_path / "价目表.xlsx"
    wb.save(x)

    nonlocal_upload = up.UPLOAD_DIR
    up.replace_source(x)
    saved = list(nonlocal_upload.iterdir())
    assert [f.name for f in saved] == ["价目表.xlsx"]      # 后缀还在
    # 重新解析能得到同样的分块
    assert up.parse_records(saved[0])[1] == up.parse_records(x)[1]


# ── 重传＝替换（老板改了价目表再传一次） ──────────────────────────────

def test_reimport_replaces_old_source(tmp_path, upload_dir, fake_embedder):
    f = tmp_path / "价目表.txt"
    f.write_text("索尼A7M4 日租 120", encoding="utf-8")
    up.import_files([f], qdrant="Q")

    f.write_text("索尼A7M4 日租 100", encoding="utf-8")
    res = up.import_files([f], qdrant="Q")

    assert res["ok"][0]["replaced"] == "价目表.txt"
    srcs = [p for p in upload_dir.iterdir() if p.is_file()]
    assert len(srcs) == 1                              # 不是两份源并存
    assert "100" in srcs[0].read_text(encoding="utf-8")
    assert len(list(up.backup_dir().glob("*.txt"))) == 1   # 旧版留档，没删


def test_reimport_deletes_old_points(tmp_path, upload_dir, fake_embedder, monkeypatch):
    """新旧价格并存 = 机器人可能报旧价，比不回答严重。"""
    calls = []
    monkeypatch.setattr(up, "delete_source_points",
                        lambda q, c, s: calls.append((q, c, s)) or 7)
    f = tmp_path / "价目表.txt"
    f.write_text("旧价 120", encoding="utf-8")
    up.import_files([f], qdrant="Q")

    f.write_text("新价 100", encoding="utf-8")
    res = up.import_files([f], qdrant="Q")

    assert calls == [("Q", "knowledge_base", "价目表.txt")]
    assert res["ok"][0]["removed_points"] == 7


def test_first_import_deletes_nothing(tmp_path, upload_dir, fake_embedder, monkeypatch):
    def boom(*a):
        raise AssertionError("首次上传不该删任何东西")

    monkeypatch.setattr(up, "delete_source_points", boom)
    f = tmp_path / "新表.txt"
    f.write_text("灯架 10", encoding="utf-8")
    res = up.import_files([f], qdrant="Q")
    assert res["ok"][0]["replaced"] is None
    assert res["ok"][0]["removed_points"] == 0


def test_same_stem_in_one_batch_does_not_clobber(tmp_path, upload_dir, fake_embedder):
    """两个目录里的同名文件一起选中，不能互相顶掉。"""
    d1, d2 = tmp_path / "甲", tmp_path / "乙"
    d1.mkdir(), d2.mkdir()
    (d1 / "价格.txt").write_text("甲店 120", encoding="utf-8")
    (d2 / "价格.txt").write_text("乙店 100", encoding="utf-8")

    res = up.import_files([d1 / "价格.txt", d2 / "价格.txt"], qdrant="Q")
    assert res["failed"] == []
    assert [o["source"] for o in res["ok"]] == ["价格.txt", "价格_1.txt"]
    assert res["ok"][1]["replaced"] is None


def test_delete_failure_warns_but_import_succeeds(tmp_path, upload_dir, fake_embedder,
                                                  monkeypatch):
    monkeypatch.setattr(up, "delete_source_points", lambda *a: -1)
    f = tmp_path / "价目表.txt"
    f.write_text("旧 120", encoding="utf-8")
    up.import_files([f], qdrant="Q")

    msgs = []
    f.write_text("新 100", encoding="utf-8")
    res = up.import_files([f], qdrant="Q", progress=msgs.append)
    assert len(res["ok"]) == 1                       # 上传本身没失败
    assert any("重建索引" in m for m in msgs)          # 但要告诉用户去清干净


# ── 重建索引：源已经在家，绝不能再动它 ────────────────────────────────

def test_already_home_detects_uploaded_and_chat_raw(home):
    updir = home / "data" / "chat_raw" / "uploaded"
    rawdir = home / "data" / "chat_raw"
    updir.mkdir(parents=True)
    assert up.already_home(updir / "价目表.xlsx") is True
    assert up.already_home(rawdir / "video.txt") is True
    assert up.already_home(home / "下载" / "价目表.xlsx") is False


def test_rebuild_does_not_touch_sources(home, fake_embedder):
    """踩过的坑：重建时把源当"重传"挪进 .old/，等于一边重建一边删源。"""
    updir = home / "data" / "chat_raw" / "uploaded"
    updir.mkdir(parents=True)
    src = updir / "价目表.txt"
    src.write_text("索尼A7M4 120", encoding="utf-8")

    files = up.source_files()
    assert files == [src]
    res = up.import_files(files, qdrant="Q")

    assert res["failed"] == []
    assert res["ok"][0]["replaced"] is None          # 没被当成重传
    assert src.is_file()                             # 源还在原地
    assert not list(up.backup_dir().glob("*"))       # 没往 .old 里塞东西
    assert list(updir.iterdir()) == [src]            # 也没多复制一份


def test_source_files_includes_both_places(home):
    updir = home / "data" / "chat_raw" / "uploaded"
    rawdir = home / "data" / "chat_raw"
    updir.mkdir(parents=True)
    (rawdir / "video.txt").write_text("老源", encoding="utf-8")
    (updir / "价目表.xlsx").write_bytes(b"x")
    (updir / "说明.docx").write_bytes(b"x")
    (updir / "笔记.md").write_text("m", encoding="utf-8")

    names = set(f.name for f in up.source_files())
    assert names == {"video.txt", "笔记.md", "价目表.xlsx", "说明.docx"}


def test_source_files_skips_unknown_suffix(home):
    updir = home / "data" / "chat_raw" / "uploaded"
    updir.mkdir(parents=True)
    (updir / "价目表.xlsx").write_bytes(b"x")
    (updir / "临时.tmp").write_bytes(b"x")
    assert [f.name for f in up.source_files()] == ["价目表.xlsx"]


def test_source_files_ignores_old_dir(home):
    updir = home / "data" / "chat_raw" / "uploaded"
    (updir / ".old").mkdir(parents=True)
    (updir / ".old" / "旧价目表.txt").write_text("旧", encoding="utf-8")
    (updir / "价目表.txt").write_text("新", encoding="utf-8")
    assert [f.name for f in up.source_files()] == ["价目表.txt"]


# ── 批量导入容错 ──────────────────────────────────────────────────────

def test_import_ok(tmp_path, upload_dir, fake_embedder):
    f = tmp_path / "价格.txt"
    f.write_text("索尼 A7M4 日租 120 元。", encoding="utf-8")
    res = up.import_files([f], qdrant="Q")
    assert res["failed"] == []
    assert res["chunks"] == 1
    assert res["ok"][0]["file"] == "价格.txt"
    assert fake_embedder[0]["qdrant"] == "Q"
    assert fake_embedder[0]["source"] == "价格.txt"


def test_import_one_bad_file_does_not_kill_others(tmp_path, upload_dir, fake_embedder):
    """批量上传最气的是第 3 个文件坏了、结果 1、2 也没进去。"""
    good1 = tmp_path / "1.txt"
    good1.write_text("佳能 R5 押金 5000", encoding="utf-8")
    bad = tmp_path / "2.pdf"
    bad.write_bytes(b"%PDF")
    good2 = tmp_path / "3.txt"
    good2.write_text("闪光灯 日租 30", encoding="utf-8")

    res = up.import_files([good1, bad, good2], qdrant="Q")
    assert [o["file"] for o in res["ok"]] == ["1.txt", "3.txt"]
    assert [f["file"] for f in res["failed"]] == ["2.pdf"]
    assert "暂不支持" in res["failed"][0]["reason"]
    assert res["chunks"] == 2


def test_import_empty_file_reported_not_crash(tmp_path, upload_dir, fake_embedder):
    f = tmp_path / "空.txt"
    f.write_text("   \n\n", encoding="utf-8")
    res = up.import_files([f], qdrant="Q")
    assert res["ok"] == []
    assert "没有可读文字" in res["failed"][0]["reason"]


def test_import_missing_file_reported(tmp_path, upload_dir, fake_embedder):
    res = up.import_files([tmp_path / "无.txt"], qdrant="Q")
    assert res["ok"] == []
    assert "不存在" in res["failed"][0]["reason"]


def test_import_reports_progress(tmp_path, upload_dir, fake_embedder):
    f = tmp_path / "p.txt"
    f.write_text("主机 日租 200", encoding="utf-8")
    msgs = []
    up.import_files([f], qdrant="Q", progress=msgs.append)
    assert any("嵌入" in m for m in msgs)
    assert any("已入库" in m for m in msgs)


def test_import_progress_callback_error_ignored(tmp_path, upload_dir, fake_embedder):
    """界面回调炸了不能把导入带崩。"""
    f = tmp_path / "p.txt"
    f.write_text("镜头 日租 80", encoding="utf-8")

    def boom(_m):
        raise RuntimeError("界面没了")

    res = up.import_files([f], qdrant="Q", progress=boom)
    assert len(res["ok"]) == 1


def test_import_source_name_recorded(tmp_path, upload_dir, fake_embedder):
    """入库 payload 里的 source 是留档文件的名字，重建后还能追溯。"""
    f = tmp_path / "租金表.txt"
    f.write_text("A7M4 120", encoding="utf-8")
    res = up.import_files([f], qdrant="Q")
    assert res["ok"][0]["source"] == "租金表.txt"
    assert fake_embedder[0]["source"] == "租金表.txt"


def test_import_passes_collection(tmp_path, upload_dir, fake_embedder):
    f = tmp_path / "x.txt"
    f.write_text("灯架 日租 10", encoding="utf-8")
    up.import_files([f], qdrant="Q", collection="kb2")
    assert fake_embedder[0]["collection"] == "kb2"


# ── 已上传列表 ────────────────────────────────────────────────────────

def test_list_uploaded_empty(upload_dir):
    assert up.list_uploaded() == []


def test_list_uploaded_shows_name_and_size(tmp_path, upload_dir):
    src = tmp_path / "a.txt"
    src.write_text("第一行\n第二行\n", encoding="utf-8")
    up.save_source(src)
    rows = up.list_uploaded()
    assert rows[0]["name"] == "a.txt"
    assert rows[0]["size_kb"] >= 0
    assert rows[0]["mtime"]


def test_list_uploaded_ignores_missing_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(up, "UPLOAD_DIR", tmp_path / "没这个目录")
    assert up.list_uploaded() == []


# ── 按 source 删向量（真 client，本地文件模式） ───────────────────────

def _seed_client(tmp_path, name="q"):
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, PointStruct, VectorParams
    c = QdrantClient(path=str(tmp_path / name))
    c.create_collection("kb", vectors_config=VectorParams(size=4, distance=Distance.COSINE))
    c.upsert("kb", points=[
        PointStruct(id=1, vector=[1, 0, 0, 0], payload={"source": "a.txt", "text": "x"}),
        PointStruct(id=2, vector=[0, 1, 0, 0], payload={"source": "b.txt", "text": "y"}),
        PointStruct(id=3, vector=[0, 0, 1, 0], payload={"source": "a.txt", "text": "z"}),
    ])
    return c


def test_delete_source_points_removes_only_that_source(tmp_path):
    c = _seed_client(tmp_path)
    try:
        assert up.delete_source_points(c, "kb", "a.txt") == 2
        left = c.scroll("kb", limit=10)[0]
        assert [p.payload["source"] for p in left] == ["b.txt"]
    finally:
        c.close()


def test_delete_source_points_missing_collection_is_minus_one(tmp_path):
    from qdrant_client import QdrantClient
    c = QdrantClient(path=str(tmp_path / "empty"))
    try:
        assert up.delete_source_points(c, "没这个库", "a.txt") == -1
    finally:
        c.close()


# ── 界面接线（不建窗口，只读源码，和 test_prompt_store 一个路子） ─────

def _kb_src():
    from pathlib import Path
    return (Path(__file__).resolve().parent.parent / "gui" / "kb_dialog.py").read_text(
        encoding="utf-8")


def test_kb_page_has_upload_and_rebuild_buttons():
    s = _kb_src()
    assert "选择文件…" in s
    assert "重建索引" in s
    assert "_kb_pick_files" in s
    assert "_kb_rebuild" in s


def test_kb_import_runs_in_background_thread():
    """嵌入要好几秒，跑主线程界面会假死。"""
    s = _kb_src()
    assert "threading.Thread(target=work, daemon=True).start()" in s
    assert "self.win.after(0" in s


def test_kb_rebuild_uses_source_files_helper():
    """重建必须走 source_files()，不能自己拼 glob —— 拼错就把源删了。"""
    s = _kb_src()
    assert "source_files()" in s
    assert "delete_collection" in s
    assert "ensure_collection" in s
    assert "askyesno" in s
