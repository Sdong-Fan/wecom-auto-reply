"""TDD tests for video transcript → Q&A generation pipeline.

Tests the generate_qa_from_transcript function that uses LLM to convert
raw video transcripts into structured Q&A pairs for knowledge base import.
"""
import json
import os
import pytest
from dotenv import load_dotenv

load_dotenv()

from pipeline.video_qa import generate_qa_from_transcript


class TestGenerateQaFromTranscript:
    """Tests for LLM-based Q&A generation from video transcripts."""

    def test_returns_list_of_qa_pairs(self):
        """Function should return a list of dicts with 'q' and 'a' keys."""
        # Use the actual transcript from data/videos/抖音检测.mp4.transcript.txt
        transcript = ("抖音帐号检测,首先打开抖音,打开右下角的我点开右上角的三个横杠,"
                      "点开以后我们进入创作者中心进入创作者中心以后,"
                      "点开下面这里有一个全部全部这一行有一个帐号检测,"
                      "如果这一行没有,那我们就点开全部在全部里面找到帐号检测"
                      "在蓝色的这个,就可以了,这里就是帐号检测")
        result = generate_qa_from_transcript(transcript)
        assert isinstance(result, list)
        assert len(result) > 0
        assert all("q" in item and "a" in item for item in result)

    def test_qa_pairs_are_nonempty_strings(self):
        """Each Q and A should be non-empty strings."""
        transcript = "课程价格是2980元，包含10节课，每节课30分钟"
        result = generate_qa_from_transcript(transcript)
        for item in result:
            assert isinstance(item["q"], str) and len(item["q"]) > 0
            assert isinstance(item["a"], str) and len(item["a"]) > 0

    def test_empty_transcript_returns_empty_list(self):
        """Empty transcript should return empty list, not crash."""
        result = generate_qa_from_transcript("")
        assert result == []

    def test_short_transcript_returns_empty_or_qa(self):
        """Very short transcript may return empty list (acceptable)."""
        transcript = "打开抖音，点击右下角的我"
        result = generate_qa_from_transcript(transcript)
        # Very short transcripts may not produce Q&A pairs
        assert isinstance(result, list)

    def test_qa_pairs_contain_relevant_content(self):
        """Q&A pairs should reference content from the transcript."""
        transcript = "课程价格是2980元，包含10节课，每节课30分钟，支持回放"
        result = generate_qa_from_transcript(transcript)
        # At least one Q&A should mention price, lessons, or duration
        all_text = " ".join(item["q"] + item["a"] for item in result)
        assert any(kw in all_text for kw in ["2980", "10节", "30分钟", "课程", "价格"])

    def test_output_is_valid_json_serializable(self):
        """Result should be JSON serializable (for persistence)."""
        transcript = "抖音帐号检测，首先打开抖音"
        result = generate_qa_from_transcript(transcript)
        # Should not raise
        json_str = json.dumps(result, ensure_ascii=False)
        assert len(json_str) > 0


@pytest.mark.skipif(
    os.environ.get("SKIP_EMBED_TESTS") == "1",
    reason="Embedding tests skipped (PyTorch DLL conflict in full suite)"
)
class TestQaImportToQdrant:
    """Tests for importing Q&A pairs into Qdrant with overwrite."""

    def test_import_qa_pairs_returns_count(self):
        """import_qa_pairs should return the number of stored pairs."""
        import uuid
        from pipeline.embedder import embed_and_store
        from qdrant_client import QdrantClient

        qdrant = QdrantClient(path="data/test_qdrant_qa")
        qa_pairs = [
            {"q": "课程多少钱", "a": "课程价格2980元"},
            {"q": "有优惠吗", "a": "当前没有优惠"},
        ]
        # Convert Q&A pairs to text chunks for embedding
        chunks = [f"问：{qa['q']}\n答：{qa['a']}" for qa in qa_pairs]
        coll = f"test_qa_import_{uuid.uuid4().hex[:8]}"
        count = embed_and_store(chunks, qdrant,
                                collection_name=coll,
                                source="test_video")
        assert count == 2

    def test_import_overwrites_same_content(self):
        """Re-importing same Q&A pairs should overwrite, not duplicate."""
        import uuid
        from pipeline.embedder import embed_and_store, chunk_id
        from qdrant_client import QdrantClient

        qdrant = QdrantClient(path="data/test_qdrant_qa")
        chunks = ["问：课程多少钱\n答：课程价格2980元"]
        coll = f"test_qa_overwrite_{uuid.uuid4().hex[:8]}"

        # Import twice
        embed_and_store(chunks, qdrant, collection_name=coll)
        embed_and_store(chunks, qdrant, collection_name=coll)

        # Should have exactly 1 point (upsert, not insert)
        count = qdrant.count(collection_name=coll, exact=True).count
        assert count == 1
