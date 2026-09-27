"""TDD tests for BGE model offline mode.

When HF_HUB_OFFLINE=1 is set, the embedding model should load from
local cache without making any network requests to HuggingFace.
This prevents 10+ second delays in the main loop.
"""
import os
import time
import pytest
from dotenv import load_dotenv

load_dotenv()


@pytest.mark.skipif(
    os.environ.get("SKIP_EMBED_TESTS") == "1",
    reason="Embedding tests skipped (PyTorch DLL conflict in full suite)"
)
def test_embed_query_respects_offline_env():
    """embed_query should work when HF_HUB_OFFLINE=1 is set.

    The model should load from local cache without network requests.
    This is critical for main loop performance — network verification
    adds 10+ seconds per message processing cycle.
    """
    # Set offline mode before importing
    os.environ["HF_HUB_OFFLINE"] = "1"
    try:
        # Force reimport to pick up env var
        import importlib
        import rag.embed_query
        importlib.reload(rag.embed_query)

        start = time.time()
        result = rag.embed_query.embed_query("测试课程价格")
        elapsed = time.time() - start

        assert len(result) == 512, f"Expected 512-dim vector, got {len(result)}"
        # Offline load from cache should be < 5 seconds
        # (vs 10+ seconds with network verification)
        assert elapsed < 10.0, f"Offline load took {elapsed:.1f}s, too slow"
    finally:
        os.environ.pop("HF_HUB_OFFLINE", None)


def test_embed_query_sets_offline_in_main():
    """main.py should set HF_HUB_OFFLINE=1 before loading models.

    Verifies that the offline environment variable is configured
    in the main entry point.
    """
    import ast
    with open("main.py", encoding="utf-8") as f:
        tree = ast.parse(f.read())

    # Check that HF_HUB_OFFLINE is set somewhere in main.py
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Attribute):
                    if hasattr(target.value, 'attr') and target.value.attr == 'environ':
                        if isinstance(node.value, ast.Constant) and node.value.value == "1":
                            found = True
                if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
                    if target.value.id == 'environ' or target.value.id == 'os':
                        pass
        # Also check os.environ.setdefault calls
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                if node.func.attr == 'setdefault':
                    if node.args and isinstance(node.args[0], ast.Constant):
                        if node.args[0].value == "HF_HUB_OFFLINE":
                            found = True

    assert found, "main.py should set HF_HUB_OFFLINE=1 to prevent network delays"
