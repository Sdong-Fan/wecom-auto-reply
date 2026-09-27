"""TDD tests for pipeline configuration issues.

Bug 1: run_pipeline.py uses QdrantClient(url=...) (server mode)
        but main app uses QdrantClient(path=...) (local file mode).
        Pipeline should use local file mode to match main app.

Bug 2: embedder.py uses SentenceTransformer(MODEL_NAME) which downloads
        from HuggingFace. Should use local bge_model/ when available.
"""
import os
import ast
import pytest


def test_run_pipeline_uses_local_qdrant_path():
    """run_pipeline.py should use QdrantClient(path=...) not url=...

    The main app uses local file mode (data/qdrant/). The pipeline must
    use the same mode so imported knowledge is directly available.
    Using url= requires a running Qdrant server which users don't have.
    """
    with open("pipeline/run_pipeline.py", encoding="utf-8") as f:
        source = f.read()

    # Should NOT contain url-based QdrantClient
    assert 'QdrantClient(url=' not in source, \
        "run_pipeline.py uses QdrantClient(url=...) — should use path= for local mode"

    # Should contain path-based QdrantClient
    assert 'QdrantClient(path=' in source, \
        "run_pipeline.py should use QdrantClient(path=...) for local file mode"


def test_embedder_uses_local_bge_model():
    """embedder.py should load BGE model from local bge_model/ directory.

    The project bundles bge_model/ (~92MB) for offline use. The embedder
    should use _resolve_model_path() from rag/embed_query.py instead of
    downloading from HuggingFace every time.
    """
    with open("pipeline/embedder.py", encoding="utf-8") as f:
        source = f.read()

    # Should NOT hardcode MODEL_NAME for SentenceTransformer download
    assert 'SentenceTransformer(MODEL_NAME)' not in source, \
        "embedder.py downloads model from HuggingFace — use local bge_model/ instead"

    # Should reference local model path resolution
    assert '_resolve_model_path' in source or 'bge_model' in source, \
        "embedder.py should use local bge_model/ for offline embedding"
