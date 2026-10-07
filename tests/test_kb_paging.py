# tests/test_kb_paging.py
"""资料库翻页 —— 必须用**真的** Qdrant 测。

为什么不能用 MagicMock：这个 bug 正是 mock 测不出来的那类。
`list_entries` 原来把数字 offset 传给了 `client.scroll(offset=...)`，
但 Qdrant 的 scroll 是**游标分页**（offset 要的是上一页的 next_page_offset、
一个 point id），不是数字偏移。我们的 id 是 UUID，传 100 毫无意义 →
**每页都返回同一批**，店主反馈"上一页下一页是假的"。

mock 会老实返回你喂给它的那批数据，所以"每页都一样"这个现象在 mock 下
完全看不出来。只有真 Qdrant 才能验出分页到底有没有动。
"""
import pytest

from rag import kb_tools

pytest.importorskip("qdrant_client")


@pytest.fixture
def client(tmp_path):
    from qdrant_client import QdrantClient
    c = QdrantClient(path=str(tmp_path / "q"))
    yield c
    c.close()


def _fill(client, n, collection="knowledge_base"):
    """塞 n 条 FAQ 进库（不走向量，只验分页逻辑，所以直接 upsert）。"""
    from pipeline.embedder import chunk_id
    from qdrant_client.models import PointStruct
    from rag.retriever import ensure_collection
    ensure_collection(client, collection)
    pts = []
    for i in range(n):
        text = "客户问题: 问题%03d\n销售回答: 答案%03d" % (i, i)
        pts.append(PointStruct(id=chunk_id(text), vector=[0.0] * 512,
                               payload={"text": text, "source": "test",
                                        "index": i, "chunk_id": chunk_id(text)}))
    client.upsert(collection_name=collection, points=pts)
    return pts


def test_pages_return_different_entries(client):
    """★ 核心回归：第 0 页和第 1 页必须是**不同**的条目（以前两页一模一样）。"""
    _fill(client, 25)
    p0 = kb_tools.list_entries(client, "knowledge_base", offset=0, limit=10)
    p1 = kb_tools.list_entries(client, "knowledge_base", offset=10, limit=10)
    p2 = kb_tools.list_entries(client, "knowledge_base", offset=20, limit=10)
    assert len(p0) == 10 and len(p1) == 10 and len(p2) == 5
    ids0 = {e["id"] for e in p0}
    ids1 = {e["id"] for e in p1}
    ids2 = {e["id"] for e in p2}
    assert not (ids0 & ids1), "第 0 页和第 1 页不能有重叠"
    assert not (ids1 & ids2), "第 1 页和第 2 页不能有重叠"
    assert len(ids0 | ids1 | ids2) == 25, "三页合起来要正好覆盖全部条目"


def test_no_page_is_a_repeat_of_the_first(client):
    """逐页翻过去，每一页都要和第一页不同 —— 钉住"翻页是假的"这个症状。"""
    _fill(client, 30)
    first = {e["id"] for e in kb_tools.list_entries(client, "knowledge_base", 0, 10)}
    for off in (10, 20):
        page = {e["id"] for e in kb_tools.list_entries(client, "knowledge_base", off, 10)}
        assert page != first, "offset=%d 又返回了第一页" % off


def test_offset_beyond_end_is_empty(client):
    _fill(client, 5)
    assert kb_tools.list_entries(client, "knowledge_base", offset=50, limit=10) == []


def test_offset_zero_still_works(client):
    _fill(client, 3)
    got = kb_tools.list_entries(client, "knowledge_base", offset=0, limit=10)
    assert len(got) == 3


def test_page_size_larger_than_total(client):
    _fill(client, 3)
    got = kb_tools.list_entries(client, "knowledge_base", offset=0, limit=100)
    assert len(got) == 3
    assert kb_tools.list_entries(client, "knowledge_base", offset=100, limit=100) == []


def test_keyword_filters_within_the_page(client):
    """关键词只在**这一页**里过滤（本地模式没有全文检索，界面也是这么写的）。"""
    _fill(client, 12)
    got = kb_tools.list_entries(client, "knowledge_base", offset=0, limit=12,
                                keyword="问题001")
    assert len(got) == 1 and "问题001" in got[0]["text"]


def test_paging_is_stable_across_calls(client):
    """同一页取两次结果要一致（游标分页不能因为重复调用就漂）。"""
    _fill(client, 15)
    a = [e["id"] for e in kb_tools.list_entries(client, "knowledge_base", 0, 5)]
    b = [e["id"] for e in kb_tools.list_entries(client, "knowledge_base", 0, 5)]
    assert a == b


# ── 关键词搜索：必须**全库**找，不能只看当前页 ────────────────────────

def test_search_finds_entries_outside_the_first_page(client):
    """★ 回归「资料库里找不到」：条目在第 3 页，在第 1 页搜也得搜得到。

    原来搜索只在"取回的那一页"里过滤 —— 新学到的条目排在最后一页，
    用户在默认第 1 页搜什么都搜不到，看起来就像"资料库里没有这条"
    （实测搜 zve10：第 1 页 0 条、第 2 页 3 条）。
    """
    _fill(client, 25)
    # 「问题024」在 offset=20 起的那一页
    rows, total = kb_tools.search_entries(client, "knowledge_base", "问题024",
                                          offset=0, limit=10)
    assert total == 1, "全库搜索应当命中 1 条（不管它在第几页）"
    assert len(rows) == 1 and "问题024" in rows[0]["text"]


def test_list_entries_with_keyword_is_global(client):
    """`list_entries(keyword=...)` 也要走全库搜索（界面用的就是它）。"""
    _fill(client, 25)
    got = kb_tools.list_entries(client, "knowledge_base", offset=0, limit=10,
                                keyword="问题024")
    assert len(got) == 1 and "问题024" in got[0]["text"]


def test_search_reports_total_hits(client):
    """命中总数要报出来（界面显示"命中 N 条"）——否则用户不知道翻页还有没有。"""
    _fill(client, 12)
    rows, total = kb_tools.search_entries(client, "knowledge_base", "问题00",
                                          offset=0, limit=5)
    assert total == 10          # 问题000..问题009
    assert len(rows) == 5
    page2, total2 = kb_tools.search_entries(client, "knowledge_base", "问题00",
                                            offset=5, limit=5)
    assert total2 == 10 and len(page2) == 5
    assert {r["id"] for r in rows} & {r["id"] for r in page2} == set()


def test_search_no_hit_is_empty_not_error(client):
    _fill(client, 5)
    rows, total = kb_tools.search_entries(client, "knowledge_base", "不存在的词")
    assert rows == [] and total == 0
