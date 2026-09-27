# rag/embed_query.py
"""Generate embedding vector using local BGE Chinese model.

BAAI/bge-small-zh-v1.5: ~100MB, CPU-friendly, optimized for Chinese retrieval.
No API key, no network, completely offline.
"""

import os
import sys
from pathlib import Path

# Must be set BEFORE importing sentence_transformers / transformers
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import logging
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

MODEL_NAME = "BAAI/bge-small-zh-v1.5"
VECTOR_SIZE = 512

_model: SentenceTransformer | None = None


def _resolve_model_path() -> str | Path:
    """Return local model path if bundled, else HF model name."""
    # Check for bundled model (PyInstaller one-folder mode)
    if getattr(sys, "frozen", False):
        bundled = Path(sys._MEIPASS) / "bge_model"
        if (bundled / "model.safetensors").exists():
            logger.info(f"Using bundled BGE model: {bundled}")
            return str(bundled)
    # Check for model beside the source (dev convenience)
    local = Path(__file__).resolve().parent.parent / "bge_model"
    if (local / "model.safetensors").exists():
        logger.info(f"Using local BGE model: {local}")
        return str(local)
    return MODEL_NAME


def get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        model_path = _resolve_model_path()
        logger.info(f"Loading embedding model: {model_path}")
        _model = SentenceTransformer(str(model_path))
    return _model


# BGE 模型要求检索查询前加此前缀，否则检索精度大幅下降
BGE_QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："


def embed_query(text: str) -> list[float]:
    """Generate embedding vector for a query text.

    BGE models require a query prefix for retrieval — see BAAI/BGE docs.
    """
    model = get_model()
    embedding = model.encode(
        BGE_QUERY_PREFIX + text, normalize_embeddings=True)
    return embedding.tolist()
