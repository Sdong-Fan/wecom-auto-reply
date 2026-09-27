"""Local pipeline runner (no Docker needed).

Usage: python scripts/run_local.py [--skip-videos] [--skip-chat]

Uses local file-based Qdrant (data/qdrant/), no server needed.
Loads .env for API keys automatically.
"""

import argparse
import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()

from qdrant_client import QdrantClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.chat_importer import parse_chat_file
from pipeline.chunker import split_text, split_qa_pairs
from pipeline.embedder import embed_and_store

logging.basicConfig(
    level="INFO",
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

QDRANT_PATH = "data/qdrant"
# Use local path for pipeline; gateway can still use env var for Docker
CHAT_DIR = "data/chat_raw"
VIDEO_DIR = "data/videos"


def ensure_qdrant_dir():
    os.makedirs(QDRANT_PATH, exist_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Local knowledge pipeline")
    parser.add_argument("--skip-videos", action="store_true")
    parser.add_argument("--skip-chat", action="store_true")
    parser.add_argument("--chunk-size", type=int, default=500)
    parser.add_argument("--whisper-model", default="medium",
                        choices=["tiny", "small", "medium", "large"])
    parser.add_argument("--clean", action="store_true",
                        help="先删除 data/qdrant 再建库。embedder 只做 upsert、"
                             "从不删除，不清空会让旧内容与新内容同时被检索到"
                             "（例如改价后旧价格仍会命中）")
    args = parser.parse_args()

    if args.clean:
        import shutil
        shutil.rmtree(QDRANT_PATH, ignore_errors=True)
        logger.info(f"已清空旧向量库: {QDRANT_PATH}")

    ensure_qdrant_dir()

    # Local Qdrant (file-based, no server needed)
    qdrant = QdrantClient(path=QDRANT_PATH)

    all_chunks: list[str] = []

    # 1. Chat records
    if not args.skip_chat and os.path.isdir(CHAT_DIR):
        logger.info(f"=== Importing chat records from {CHAT_DIR} ===")
        txt_files = [f for f in sorted(os.listdir(CHAT_DIR)) if f.endswith(".txt")]
        if not txt_files:
            logger.info("  No .txt files found, skipping.")
        for filename in txt_files:
            filepath = os.path.join(CHAT_DIR, filename)
            qa_pairs = parse_chat_file(filepath)
            if qa_pairs:
                chunks = split_qa_pairs(qa_pairs)
                all_chunks.extend(chunks)
                logger.info(f"  {filename}: {len(qa_pairs)} QA pairs → {len(chunks)} chunks")

    # 2. Videos
    if not args.skip_videos and os.path.isdir(VIDEO_DIR):
        logger.info(f"=== Transcribing videos from {VIDEO_DIR} ===")
        video_files = [f for f in sorted(os.listdir(VIDEO_DIR)) if
                       f.lower().endswith((".mp4", ".avi", ".mov", ".mkv", ".wmv"))]
        if not video_files:
            logger.info("  No video files found, skipping.")
        for filename in video_files:
            filepath = os.path.join(VIDEO_DIR, filename)
            logger.info(f"  Transcribing: {filename} ({os.path.getsize(filepath) / 1024 / 1024:.0f}MB)...")
            try:
                # Import here to avoid loading whisper module until needed
                from pipeline.video_transcribe import transcribe_video
                result = transcribe_video(filepath, args.whisper_model)
                chunks = split_text(result["text"], args.chunk_size)
                all_chunks.extend(chunks)
                logger.info(f"    {len(result['segments'])} segments → {len(chunks)} chunks")
            except Exception as e:
                logger.error(f"    Failed: {e}")

    # 3. Embed and store
    if all_chunks:
        logger.info(f"=== Embedding {len(all_chunks)} chunks ===")
        count = embed_and_store(
            all_chunks, qdrant,
            source="local_pipeline",
        )
        logger.info(f"=== Done! {count} chunks stored in {QDRANT_PATH} ===")
    else:
        logger.warning("No content generated. Put .txt files in data/chat_raw/ or videos in data/videos/.")


if __name__ == "__main__":
    main()
