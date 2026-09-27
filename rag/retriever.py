# rag/retriever.py
"""Qdrant vector search for retrieving relevant knowledge chunks."""

import logging
from qdrant_client import QdrantClient
from qdrant_client.models import ScoredPoint  # type: ignore

logger = logging.getLogger(__name__)

COLLECTION_NAME = "knowledge_base"
VECTOR_SIZE = 512  # bge-small-zh-v1.5 embedding dimension


def active_collection() -> str:
    """当前资料库档案的 collection。

    回复时**每次现读**：界面上切了档案，下一条回复就按新库检索，不用重启。
    档案之间的独立性靠这个保证 —— A 库的问题绝不会检索到 B 库的资料。
    """
    try:
        from rag import archives
        return archives.active_collection()
    except Exception as e:                     # 清单坏了也不能让回复挂掉
        logger.warning(f"取当前档案失败，回落默认库: {e}")
        return COLLECTION_NAME


def ensure_collection(client: QdrantClient,
                      collection_name: str = COLLECTION_NAME) -> None:
    """Create the collection if it doesn't exist."""
    from qdrant_client.models import Distance, VectorParams
    if not client.collection_exists(collection_name):
        client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )
        logger.info(f"Created collection: {collection_name}")


def search(query_vector: list[float], client: QdrantClient,
           collection_name: str = COLLECTION_NAME,
           top_k: int = 5) -> list[ScoredPoint]:
    """Search Qdrant for the most relevant knowledge chunks."""
    return client.query_points(
        collection_name=collection_name,
        query=query_vector,
        limit=top_k,
    ).points
