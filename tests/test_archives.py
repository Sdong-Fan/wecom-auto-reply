# tests/test_archives.py
"""资料库档案：多个库互相独立、可切换。

**最重要的一条是"防串味"**：A 库的问题绝对不能检索到 B 库的资料 ——
串味意味着拿服装的价格回答摄影客户，比不回答严重得多。
"""

import pytest

from rag import archives, kb_tools, learn_store, prompt_store


@pytest.fixture
def home(tmp_path, monkeypatch):
    """把 archives 的根换到临时目录，并清掉清单缓存。"""
    monkeypatch.setattr(archives, "HERE", tmp_path)
    archives.reset_cache()
    yield tmp_path
    archives.reset_cache()


@pytest.fixture
def two(home):
    """两个档案：老库（photo） + 新建的（服装）。"""
    photo = archives.active()                      # 默认那个＝从老库升上来
    cloth = archives.create("服装租赁")
    return photo, cloth


# ── 第一个档案＝老库原样 ──────────────────────────────────────────────

def test_default_manifest_is_the_old_kb(home):
    """没建过档案时，默认就是原来那个库，路径和 collection 都不变。"""
    a = archives.active()
    assert a["id"] == archives.DEFAULT_ID
    assert a["collection"] == "knowledge_base"
    assert archives.active_collection() == "knowledge_base"
    assert archives.upload_dir() == home / "data" / "chat_raw" / "uploaded"
    assert archives.prompts_dir() == home / "prompts"
    assert archives.learned_dir() == home / "data" / "learned"
    assert archives.history_path() == home / "data" / "kb_history.jsonl"


def test_no_manifest_file_written_until_needed(home):
    """老用户什么都不做，不该凭空多出一个清单文件。"""
    archives.load()
    assert not archives.manifest_path().exists()


# ── 新建 / 改名 / 切换 ────────────────────────────────────────────────

def test_create_gives_new_collection_and_dirs(two):
    photo, cloth = two
    assert cloth["collection"] == archives.collection_name(cloth["id"])
    assert cloth["collection"] != photo["collection"]
    assert not cloth["collection"].startswith("kb_kb")      # 别叠成 kb_kb1
    assert archives.upload_dir(cloth["id"]).name == cloth["id"]
    assert archives.prompts_dir(cloth["id"]) != archives.prompts_dir(photo["id"])
    assert archives.learned_dir(cloth["id"]) != archives.learned_dir(photo["id"])
    assert archives.history_path(cloth["id"]) != archives.history_path(photo["id"])


def test_create_makes_upload_dir(two):
    _photo, cloth = two
    assert archives.upload_dir(cloth["id"]).is_dir()


def test_create_rejects_empty_name(home):
    with pytest.raises(ValueError, match="不能为空"):
        archives.create("   ")


def test_slug_is_ascii_and_unique(home):
    a = archives.create("服装租赁")          # 中文→没有 ASCII 可用
    b = archives.create("服装租赁")
    assert a["id"] != b["id"]
    assert a["id"].isascii()


def test_slug_keeps_ascii_name(home):
    a = archives.create("Clothes")
    assert a["id"] == "clothes"


def test_switch_active(two):
    photo, cloth = two
    assert archives.active_id() == photo["id"]
    assert archives.set_active(cloth["id"]) is True
    assert archives.active_id() == cloth["id"]
    assert archives.active_collection() == cloth["collection"]
    assert archives.active_name() == "服装租赁"


def test_switch_to_unknown_archive_fails(two):
    assert archives.set_active("不存在的档案") is False
    assert archives.active_id() == archives.DEFAULT_ID


def test_rename(two):
    _photo, cloth = two
    assert archives.rename(cloth["id"], "服装道具租赁") is True
    assert archives.active_name() == "摄影器材租赁"      # 改的不是当前那个
    archives.set_active(cloth["id"])
    assert archives.active_name() == "服装道具租赁"


def test_rename_rejects_empty(two):
    _photo, cloth = two
    assert archives.rename(cloth["id"], "  ") is False


def test_survives_broken_manifest(home):
    """清单被手改坏了，程序也得能起来（退回第一个档案）。"""
    p = archives.manifest_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{坏掉的 json", encoding="utf-8")
    archives.reset_cache()
    assert archives.active_id() == archives.DEFAULT_ID
    assert archives.active_collection() == "knowledge_base"


def test_active_falls_back_when_id_missing(home):
    p = archives.manifest_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"active": "没这个", "archives": [{"id": "a", "name": "甲", '
                 '"collection": "kb_a"}]}', encoding="utf-8")
    archives.reset_cache()
    assert archives.active_id() == "a"


# ── 删除 ──────────────────────────────────────────────────────────────

def test_delete_moves_dirs_to_trash(two):
    _photo, cloth = two
    d = archives.upload_dir(cloth["id"])
    (d / "价目表.txt").write_text("羽绒服 30", encoding="utf-8")

    assert archives.delete(cloth["id"]) is True
    assert archives.get(cloth["id"]) is None
    assert not d.exists()                                   # 原目录没了
    moved = list(archives.trash_dir().rglob("价目表.txt"))
    assert len(moved) == 1                                  # 但没真删
    assert moved[0].read_text(encoding="utf-8") == "羽绒服 30"


def test_cannot_delete_active(two):
    _photo, cloth = two
    archives.set_active(cloth["id"])
    with pytest.raises(ValueError, match="正在用"):
        archives.delete(cloth["id"])


def test_delete_keeps_other_archives(two):
    photo, cloth = two
    archives.create("第三個")
    archives.delete(cloth["id"])
    assert archives.get(photo["id"]) is not None
    assert len(archives.all_archives()) == 2


def test_delete_refuses_last_one(two):
    """删到只剩一个就必须拦住 —— 再删程序就没库可用了。"""
    photo, cloth = two
    archives.set_active(cloth["id"])
    archives.delete(photo["id"])
    assert len(archives.all_archives()) == 1
    with pytest.raises(ValueError, match="至少要留一个"):
        archives.delete(cloth["id"])


# ── 提示词 / 学到的语气 跟着档案走 ────────────────────────────────────

def test_prompts_are_per_archive(two, monkeypatch):
    photo, cloth = two
    monkeypatch.setattr(prompt_store, "PROMPTS_DIR", None)
    assert prompt_store.prompts_dir() == archives.prompts_dir(photo["id"])
    archives.set_active(cloth["id"])
    assert prompt_store.prompts_dir() == archives.prompts_dir(cloth["id"])


def test_prompt_files_do_not_bleed(two, monkeypatch):
    """在一个档案里改的提示词，不能影响另一个档案。"""
    photo, cloth = two
    monkeypatch.setattr(prompt_store, "PROMPTS_DIR", None)
    prompt_store.ensure_files()
    prompt_store.set_prompt("hold", "摄影库专用的占位语\n")

    archives.set_active(cloth["id"])
    prompt_store.ensure_files()
    assert "摄影库专用" not in prompt_store.get("hold")


def test_learned_tone_is_per_archive(two, monkeypatch):
    photo, cloth = two
    monkeypatch.setattr(learn_store, "LEARN_DIR", None)
    learn_store.add_tone_sample("摄影库学到的语气")
    assert learn_store.learn_dir() == archives.learned_dir(photo["id"])

    archives.set_active(cloth["id"])
    assert learn_store.learn_dir() == archives.learned_dir(cloth["id"])
    assert learn_store.tone_samples() == []              # 没串过来
    assert learn_store.add_tone_sample("服装库学到的语气")
    assert [t["text"] for t in learn_store.tone_samples()] == ["服装库学到的语气"]

    archives.set_active(photo["id"])
    assert [t["text"] for t in learn_store.tone_samples()] == ["摄影库学到的语气"]


def test_history_is_per_archive(two, monkeypatch):
    from rag import kb_history
    photo, cloth = two
    monkeypatch.setattr(kb_history, "HISTORY_PATH", None)
    kb_history.record(photo["id"], "摄影库的旧内容")
    archives.set_active(cloth["id"])
    assert kb_history.versions(photo["id"]) == []        # 服装档案里看不到摄影的历史
    kb_history.record(cloth["id"], "服装库的旧内容")
    assert [v["text"] for v in kb_history.versions(cloth["id"])] == ["服装库的旧内容"]
    archives.set_active(photo["id"])
    assert [v["text"] for v in kb_history.versions(photo["id"])] == ["摄影库的旧内容"]


# ── 源文件与上传目录 ──────────────────────────────────────────────────

def test_upload_dir_per_archive(two, monkeypatch):
    from pipeline import upload as up
    photo, cloth = two
    monkeypatch.setattr(up, "UPLOAD_DIR", None)
    assert up.upload_dir() == archives.upload_dir(photo["id"])
    archives.set_active(cloth["id"])
    assert up.upload_dir() == archives.upload_dir(cloth["id"])


def test_source_files_do_not_pull_other_archive(home, two, monkeypatch):
    """在服装档案点重建索引，绝不能把摄影的老源一起灌进来。"""
    from pipeline import upload as up
    photo, cloth = two
    monkeypatch.setattr(up, "UPLOAD_DIR", None)
    monkeypatch.setattr(up, "HERE", home)                 # 让 chat_raw 也指向临时目录
    raw = home / "data" / "chat_raw"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "video_faq.txt").write_text("摄影的老源", encoding="utf-8")
    archives.upload_dir(photo["id"]).mkdir(parents=True, exist_ok=True)
    (archives.upload_dir(photo["id"]) / "价目表.txt").write_text("相机 90", encoding="utf-8")

    archives.set_active(photo["id"])
    names = {f.name for f in up.source_files()}
    assert names == {"video_faq.txt", "价目表.txt"}        # 第一个档案：老源算它的

    archives.set_active(cloth["id"])
    assert up.source_files() == []                        # 新档案：什么都没有，更没串味


def test_dialog_collection_follows_switch(two):
    """对话框里的 collection 要跟着当前档案变 —— 不然切了还在操作旧库。"""
    tk = pytest.importorskip("tkinter")
    from gui.kb_dialog import KbDialog

    photo, cloth = two
    try:
        root = tk.Tk()
    except Exception as e:                       # 没有显示环境就跳过
        pytest.skip(f"没有可用的显示环境: {e}")
    root.withdraw()
    try:
        dlg = KbDialog(root, {})
        assert dlg.collection == archives.collection_of(photo["id"])
        archives.set_active(cloth["id"])
        assert dlg.collection == archives.collection_of(cloth["id"])
        dlg.win.destroy()
    finally:
        root.destroy()


# ── 防串味（最关键） ──────────────────────────────────────────────────

def _client(tmp_path):
    from qdrant_client import QdrantClient
    return QdrantClient(path=str(tmp_path / "q"))


def _seed(client, collection, vec, text):
    from qdrant_client.models import Distance, PointStruct, VectorParams
    if not client.collection_exists(collection):
        client.create_collection(collection, vectors_config=VectorParams(
            size=4, distance=Distance.COSINE))
    client.upsert(collection, points=[
        PointStruct(id=abs(hash(text)) % (10 ** 9), vector=vec, payload={"text": text})])


def test_no_cross_archive_bleed(tmp_path, home, two, monkeypatch):
    """切到服装档案后，问摄影的问题**必须检索不到**摄影资料。"""
    import rag.embed_query as eq
    from rag.retriever import active_collection, search

    photo, cloth = two
    c = _client(tmp_path)
    try:
        _seed(c, archives.collection_of(photo["id"]), [1, 0, 0, 0], "索尼A7M4 日租 90 元")
        _seed(c, archives.collection_of(cloth["id"]), [0, 1, 0, 0], "羽绒服 日租 30 元")

        # 切到服装档案：向量检索也只会落在 kb_cloth 里
        archives.set_active(cloth["id"])
        hits = search([1, 0, 0, 0], c, collection_name=active_collection(), top_k=5)
        texts = [h.payload["text"] for h in hits]
        assert texts == ["羽绒服 日租 30 元"]
        assert "索尼A7M4 日租 90 元" not in texts

        # 切回摄影档案
        archives.set_active(photo["id"])
        hits = search([1, 0, 0, 0], c, collection_name=active_collection(), top_k=5)
        assert [h.payload["text"] for h in hits] == ["索尼A7M4 日租 90 元"]
    finally:
        c.close()


def test_kb_tools_default_follows_active(tmp_path, home, two):
    """kb_tools 不传 collection 时用当前档案。"""
    photo, cloth = two
    c = _client(tmp_path)
    try:
        _seed(c, archives.collection_of(photo["id"]), [1, 0, 0, 0], "摄影的资料")
        _seed(c, archives.collection_of(cloth["id"]), [0, 1, 0, 0], "服装的资料")

        assert kb_tools.count(c) == 1
        assert kb_tools.list_entries(c)[0]["text"] == "摄影的资料"

        archives.set_active(cloth["id"])
        assert kb_tools.list_entries(c)[0]["text"] == "服装的资料"
    finally:
        c.close()


def test_responder_searches_active_collection(home, two, monkeypatch):
    """回复路径必须传当前档案的 collection（串味就是在这里堵住的）。"""
    import inspect
    from rag import responder
    src = inspect.getsource(responder.Responder.generate_reply)
    assert "collection_name=active_collection()" in src


def test_active_collection_is_read_every_time(home, two):
    """切换要立刻生效 —— 不能是启动时读一次就定死。"""
    from rag.retriever import active_collection
    _photo, cloth = two
    first = active_collection()
    archives.set_active(cloth["id"])
    assert active_collection() != first


# ── 复制档案 ──────────────────────────────────────────────────────────

def test_copy_collection_keeps_vectors(tmp_path, home, two):
    """复制档案连向量一起拷：不重新嵌入，也不会有打分差异。"""
    photo, cloth = two
    c = _client(tmp_path)
    try:
        _seed(c, archives.collection_of(photo["id"]), [1, 0, 0, 0], "第一条")
        _seed(c, archives.collection_of(photo["id"]), [0, 1, 0, 0], "第二条")
        dst = archives.collection_of(cloth["id"])
        # 先把目标库按 4 维建出来：ensure_collection 只会建 512 维的正式库
        _seed(c, dst, [0, 0, 1, 0], "占位")
        c.delete(dst, points_selector=[p.id for p in c.scroll(dst, limit=10)[0]])
        n = kb_tools.copy_collection(c, archives.collection_of(photo["id"]), dst)
        assert n == 2
        got = kb_tools.list_entries(c, dst)
        assert {e["text"] for e in got} == {"第一条", "第二条"}
        # 原件还在
        assert kb_tools.count(c, archives.collection_of(photo["id"])) == 2
    finally:
        c.close()


def test_copy_dirs_can_be_partial(two, home):
    """新建档案只拷提示词和语气，不拷资料源（事实从零开始）。"""
    photo, cloth = two
    archives.upload_dir(photo["id"]).mkdir(parents=True, exist_ok=True)
    (archives.upload_dir(photo["id"]) / "价目表.txt").write_text("相机", encoding="utf-8")
    archives.prompts_dir(photo["id"]).mkdir(parents=True, exist_ok=True)
    (archives.prompts_dir(photo["id"]) / "hold.md").write_text("占位语", encoding="utf-8")

    archives.copy_dirs(photo["id"], cloth["id"], kinds=("prompts", "learned"))
    assert (archives.prompts_dir(cloth["id"]) / "hold.md").is_file()
    assert not (archives.upload_dir(cloth["id"]) / "价目表.txt").exists()

    archives.copy_dirs(photo["id"], cloth["id"], kinds=("uploads",))
    assert (archives.upload_dir(cloth["id"]) / "价目表.txt").is_file()


def test_copy_dirs_skips_nothing_missing(home, two):
    photo, cloth = two
    archives.copy_dirs(photo["id"], cloth["id"])          # 源目录都不存在也不该炸
    assert archives.get(cloth["id"]) is not None


# ── 界面接线 ──────────────────────────────────────────────────────────

def _src():
    from pathlib import Path
    return (Path(__file__).resolve().parent.parent / "gui" / "kb_dialog.py").read_text(
        encoding="utf-8")


def test_dialog_has_archive_bar():
    s = _src()
    for label in ("新建", "重命名", "复制档案", "删除"):
        assert label in s
    assert "_archive_combo" in s or "_arch_combo" in s
    assert "<<ComboboxSelected>>" in s


def test_dialog_collection_is_dynamic():
    """collection 不能是构造时定死的 —— 中途切档案要跟着换。"""
    s = _src()
    assert "@property\n    def collection" in s
    assert "_collection_override" in s


def test_dialog_switching_refreshes_all_pages():
    s = _src()
    block = s[s.index("def _on_archive_change"):s.index("def _refresh_all")]
    assert "_refresh_all()" in block
    refresh = s[s.index("def _refresh_all"):s.index("def _reload_archives")]
    for page in ("_load(", "_kb_load(", "_learn_load("):
        assert page in refresh


def test_dialog_refuses_deleting_active_archive():
    s = _src()
    block = s[s.index("def _archive_delete"):]
    assert "正在用的档案" in block
    assert "askyesno" in block
    assert "trash_dir" in block


def test_archive_bar_hidden_when_collection_forced():
    """测试/外部指定 collection 时不显示档案栏（免得误导）。"""
    s = _src()
    assert "if not self._collection_override:\n            self._build_archive_bar()" in s
