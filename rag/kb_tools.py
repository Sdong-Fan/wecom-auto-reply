# rag/kb_tools.py
"""资料库的读写小工具：给前端「知识库」面板用。

* ``list_entries`` —— 翻页/搜索看库里到底有什么
* ``probe``        —— 「试问一句」：输入客户问题，看检索到哪几条、分数多少
* ``update_entry`` / ``delete_entry`` —— 手动改/删一条

**为什么必须复用同一个 Qdrant 客户端**：Qdrant 本地文件模式（`path=`）
同一时刻只允许一个进程打开，同一进程重复开也会报 "already accessed"。
所以这里的函数都**接收调用方传进来的 client**，绝不自己 `QdrantClient(path=...)`。
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

COLLECTION = "knowledge_base"      # 兜底常量（清单读不到时用）


def _col(collection):
    """没显式给 collection 就用**当前档案**的 —— 界面切了档案立刻生效。"""
    if collection:
        return collection
    try:
        from rag.retriever import active_collection
        return active_collection()
    except Exception:
        return COLLECTION

# FAQ 型 chunk 的格式（和 pipeline/embedder.py 里 QA_QUESTION_MARK 一致）
Q_MARK = "客户问题: "
A_MARK = "销售回答: "


def split_faq(text: str) -> Tuple[str, str]:
    """FAQ 型 chunk → ``(客户问题, 销售回答)``；不是这个格式就返回 ``("", "")``。

    库里绝大多数条目都是 ``"客户问题: X\\n销售回答: Y"``。列表里如果只塞原文，
    Treeview 只显示第一行 —— 用户看到满屏"客户问题"，以为答案没导进来。
    """
    t = (text or "").lstrip()
    if not t.startswith(Q_MARK):
        return "", ""
    body = t[len(Q_MARK):]
    if A_MARK not in body:
        return body.strip(), ""
    q, a = body.split(A_MARK, 1)
    return q.strip(), a.strip()


def make_faq(question: str, answer: str) -> str:
    """``(客户问题, 销售回答)`` → chunk 原文。"""
    q = (question or "").strip().replace("\n", " ")
    a = (answer or "").strip()
    return f"{Q_MARK}{q}\n{A_MARK}{a}"


def count(client, collection: str = None) -> int:
    collection = _col(collection)
    try:
        return int(client.count(collection_name=collection).count)
    except Exception as e:
        logger.warning(f"统计条目数失败: {e}")
        return 0


def _entry(point) -> dict:
    payload = point.payload or {}
    text = str(payload.get("text") or payload.get("content") or "")
    q, a = split_faq(text)
    meta = payload.get("meta")
    src = payload.get("source") or payload.get("file") or ""
    if not src and isinstance(meta, dict):
        src = meta.get("source", "")
    return {"id": str(point.id), "text": text, "question": q, "answer": a,
            "faq": bool(q), "source": str(src or "")}


def list_entries(client, collection: str = None, offset: int = 0,
                 limit: int = 100, keyword: str = "") -> List[dict]:
    """翻页取条目。``keyword`` 给了就在**取回的这一页**里做文本过滤。

    ★ 2026-10-06 修「翻页是假的」：
    原来把数字 ``offset`` 直接传给 ``client.scroll(offset=...)`` ——
    但 Qdrant 的 scroll 是**游标分页**，那个参数要的是**上一页返回的
    ``next_page_offset``**（一个 point id），**不是数字偏移**。
    我们的 id 是 UUID，传 ``100`` 等于"从 id=100 这个点往后找"，
    找不到就从头开始 —— 结果**每一页都返回同一批**，点上一页/下一页没任何变化。
    （而且真正的游标 ``_next`` 原来被丢掉了。）

    现在按游标逐页走到目标页：本地模式数据量小，翻到第 3 页也就 3 次 scroll。
    """
    collection = _col(collection)
    limit = max(1, int(limit))
    want = max(0, int(offset))
    kw = (keyword or "").strip()
    out: List[dict] = []
    try:
        cursor = None
        remaining = want
        while remaining > 0:
            step = min(limit, remaining)
            batch, cursor = client.scroll(
                collection_name=collection, limit=step, offset=cursor,
                with_payload=True, with_vectors=False)
            if not batch:
                return []                    # 目标页超出范围
            remaining -= len(batch)
            if remaining > 0 and cursor is None:
                return []                    # 没有下一页游标了
        points, _next = client.scroll(
            collection_name=collection, limit=limit, offset=cursor,
            with_payload=True, with_vectors=False)
    except Exception as e:
        logger.warning(f"读取资料库失败: {e}")
        return out
    for p in points:
        e = _entry(p)
        if kw and kw not in e["text"]:
            continue
        out.append(e)
    return out


def get_entry(client, point_id: str, collection: str = None) -> Optional[dict]:
    collection = _col(collection)
    try:
        got = client.retrieve(collection_name=collection, ids=[point_id],
                              with_payload=True, with_vectors=False)
    except Exception as e:
        logger.warning(f"读取条目失败: {e}")
        return None
    return _entry(got[0]) if got else None


def add_entry(client, question: str, answer: str, source: str = "手动添加",
              collection: str = None) -> dict:
    """手动加一条。有问题就写成 FAQ 格式，没有就当整段资料。"""
    collection = _col(collection)
    text = (make_faq(question, answer) if (question or "").strip()
            else (answer or "").strip())
    if not text.strip():
        raise ValueError("内容不能为空")
    from pipeline.embedder import chunk_id, embed_and_store
    n = embed_and_store([text], client, collection, source=source)
    return {"id": str(chunk_id(text)), "text": text, "source": source, "chunks": n,
            "question": split_faq(text)[0], "answer": split_faq(text)[1]}


def update_entry(client, point_id: str, text: str,
                 collection: str = None) -> dict:
    """改一条：重新嵌入 → 删旧点 → 写新点。

    chunk_id 是文本的 md5，改了内容就是新 id，所以是"删旧 + 插新"而不是原地改。
    嵌入走 ``embed_and_store``（和导入完全同一条路），改完的条目和正常条目一模一样。
    """
    collection = _col(collection)
    text = (text or "").strip()
    if not text:
        raise ValueError("内容不能为空")
    old = get_entry(client, point_id, collection)
    if old is None:
        raise ValueError("这条已经不在了（可能刚被删过），刷新看看新列表")
    source = old["source"]

    from pipeline.embedder import chunk_id, embed_and_store
    new_id = str(chunk_id(text))
    n = embed_and_store([text], client, collection, source=source)
    if new_id != str(point_id):
        client.delete(collection_name=collection, points_selector=[point_id])
    return {"id": new_id, "text": text, "source": source, "chunks": n,
            "question": split_faq(text)[0], "answer": split_faq(text)[1]}


def delete_entry(client, point_id: str, collection: str = None) -> bool:
    collection = _col(collection)
    try:
        client.delete(collection_name=collection, points_selector=[point_id])
        return True
    except Exception as e:
        logger.warning(f"删除条目失败: {e}")
        return False


def copy_collection(client, src: str, dst: str, batch: int = 256) -> int:
    """把一个档案的条目整份拷到另一个 collection（"复制档案"用）。

    连**向量一起拷** —— 不重新嵌入，所以 237 条只要一两秒，也不会因为
    模型版本差异导致两边打分不一样。
    """
    from qdrant_client.models import PointStruct
    from rag.retriever import ensure_collection

    ensure_collection(client, dst)
    total, offset = 0, None
    while True:
        points, offset = client.scroll(
            collection_name=src, limit=batch, offset=offset,
            with_payload=True, with_vectors=True)
        if not points:
            break
        client.upsert(collection_name=dst, points=[
            PointStruct(id=p.id, vector=p.vector, payload=p.payload or {})
            for p in points])
        total += len(points)
        if offset is None:
            break
    logger.info(f"复制 collection {src} → {dst}：{total} 条")
    return total


def probe(client, question: str, top_k: int = 5,
          collection: str = None) -> List[dict]:
    """「试问一句」：真的走一遍检索，把分数和出处摊开。

    用的是**和线上完全一样的**嵌入模型 + 检索函数 —— 所以这里看到几分，
    线上就是几分。这是判断"资料改对没有"的唯一可靠方式。
    """
    collection = _col(collection)
    q = (question or "").strip()
    if not q:
        return []
    try:
        from rag.embed_query import embed_query
        from rag.retriever import search
        qv = embed_query(q)
        hits = search(qv, client, collection_name=collection, top_k=top_k)
    except Exception as e:
        logger.warning(f"试问检索失败: {e}")
        return []
    out = []
    for h in hits:
        payload = h.payload or {}
        text = str(payload.get("text") or payload.get("content") or "")
        qq, aa = split_faq(text)
        out.append({
            "score": float(h.score),
            "text": text,
            "question": qq,
            "answer": aa,
            "faq": bool(qq),
            "source": str(payload.get("source") or payload.get("file") or ""),
        })
    return out


def verdict(score: float, high_threshold: float) -> str:
    """给分数一句人话结论（和线上分流门槛对齐）。"""
    if score > high_threshold:
        return "会直接回给客户"
    if score >= 0.4:
        return "偏低 → 转人工（会先回一句'稍等'）"
    return "太低 → 转人工"
