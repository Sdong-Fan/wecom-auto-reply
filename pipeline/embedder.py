# pipeline/embedder.py
"""Generate embeddings via local BGE model and upsert to Qdrant.

BAAI/bge-small-zh-v1.5: ~100MB, bundled in bge_model/, runs on CPU, zero cost.
"""

import hashlib
import os
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import logging
from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct
from rag.retriever import ensure_collection
from rag.embed_query import _resolve_model_path

logger = logging.getLogger(__name__)

_model: SentenceTransformer | None = None


def get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        model_path = _resolve_model_path()
        logger.info(f"Loading embedding model: {model_path}")
        _model = SentenceTransformer(str(model_path))
    return _model


def chunk_id(text: str) -> str:
    """Generate a valid UUID from chunk text content (MD5→UUID v3 format)."""
    h = hashlib.md5(text.encode()).hexdigest()
    # Format as UUID: 8-4-4-4-12
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


QA_QUESTION_MARK = "客户问题: "


def embed_text_for(chunk: str) -> str:
    """返回用于计算向量的文本。

    FAQ 型 chunk（``"客户问题: X\\n销售回答: Y"``）只嵌入**问题部分**。
    整段一起嵌入会被长答案稀释问题语义：实测拿「价格表发我一下」原句去查
    它自己所在的 chunk，余弦只有 0.335，而问题与问题之间是 0.818 —— 阈值
    0.7 的自动发送因此永远够不到。

    检索本来就是在比"问题像不像"；答案由 payload 原样交给 LLM，不必参与匹配。
    非 FAQ 型 chunk（如视频转录切出的段落）照旧整段嵌入。
    """
    if chunk.startswith(QA_QUESTION_MARK):
        question = chunk.split("\n", 1)[0][len(QA_QUESTION_MARK):].strip()
        if question:
            return question
    return chunk


def embed_and_store(
    chunks: list[str],
    qdrant: QdrantClient,
    collection_name: str = None,
    source: str = "unknown",
) -> int:
    """Embed all chunks and upsert into Qdrant. Returns count of points upserted.

    Uses local BGE model — no API calls, no keys, no limits.
    """
    model = get_model()
    if not collection_name:
        from rag.retriever import active_collection
        collection_name = active_collection()
    ensure_collection(qdrant, collection_name)

    points = []
    for i, chunk in enumerate(chunks):
        if not chunk.strip():
            continue
        embedding = model.encode(embed_text_for(chunk),
                                 normalize_embeddings=True).tolist()
        points.append(PointStruct(
            id=chunk_id(chunk),
            vector=embedding,
            payload={
                "text": chunk, "source": source, "index": i,
                "chunk_id": chunk_id(chunk),
            },
        ))
        if (i + 1) % 50 == 0:
            logger.info(f"Embedded {i + 1}/{len(chunks)} chunks")

    if points:
        qdrant.upsert(collection_name=collection_name, points=points)
        logger.info(f"Stored {len(points)} chunks to Qdrant")

    return len(points)
