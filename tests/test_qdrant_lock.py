"""Tests for Qdrant cleanup on exit.

Responder.close() must release Qdrant client, and _cleanup() must call it.
"""

import re
from pathlib import Path


class TestResponderClose:
    """Responder must have a close() method that releases Qdrant."""

    def test_responder_has_close_method(self):
        """Responder should have a close() method."""
        source = Path("rag/responder.py").read_text(encoding="utf-8")
        assert "def close(self)" in source, (
            "Responder lacks close() method to release Qdrant client"
        )

    def test_close_calls_qdrant_close(self):
        """close() should call self.qdrant.close()."""
        source = Path("rag/responder.py").read_text(encoding="utf-8")
        match = re.search(
            r'def close\(self\).*?(?=\n    def |\nclass |\Z)',
            source, re.DOTALL
        )
        assert match, "close() method body not found"
        body = match.group()
        assert "qdrant" in body and "close" in body, (
            "close() must call self.qdrant.close()"
        )


class TestCleanupCallsQdrantClose:
    """main.py _cleanup() must close Qdrant on exit."""

    def test_cleanup_closes_responder(self):
        """_cleanup should call responder.close() to release Qdrant lock."""
        source = Path("main.py").read_text(encoding="utf-8")
        match = re.search(
            r'def _cleanup\(\).*?(?=\ndef |\Z)',
            source, re.DOTALL
        )
        assert match, "_cleanup() not found in main.py"
        body = match.group()
        assert "close" in body and ("responder" in body or "qdrant" in body), (
            "_cleanup() must close Qdrant client"
        )
