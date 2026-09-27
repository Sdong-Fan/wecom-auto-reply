# pipeline/run_pipeline.py
"""CLI entry point for running the knowledge extraction pipeline."""

import argparse
import logging
import os

from qdrant_client import QdrantClient

from pipeline.chat_importer import parse_chat_file
from pipeline.video_transcribe import transcribe_videos_in_directory
from pipeline.chunker import split_text, split_qa_pairs
from pipeline.embedder import embed_and_store

logging.basicConfig(
    level="INFO",
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description="Run the knowledge extraction pipeline"
    )
    parser.add_argument("--chat-dir", default="data/chat_raw",
                        help="Directory with WeCom chat export .txt files")
    parser.add_argument("--video-dir", default="data/videos",
                        help="Directory with training videos")
    parser.add_argument("--skip-videos", action="store_true")
    parser.add_argument("--skip-chat", action="store_true")
    parser.add_argument("--chunk-size", type=int, default=500)
    parser.add_argument("--whisper-model", default="medium",
                        choices=["tiny", "small", "medium", "large"])
    args = parser.parse_args()

    qdrant_path = os.environ.get("QDRANT_PATH", "data/qdrant")
    logger.info(f"Qdrant local path: {qdrant_path}")
    qdrant = QdrantClient(path=qdrant_path)

    all_chunks: list[str] = []

    # 1. Import chat records
    if not args.skip_chat and os.path.isdir(args.chat_dir):
        logger.info(f"=== Importing chat records from {args.chat_dir} ===")
        for filename in sorted(os.listdir(args.chat_dir)):
            if filename.endswith(".txt"):
                filepath = os.path.join(args.chat_dir, filename)
                qa_pairs = parse_chat_file(filepath)
                if qa_pairs:
                    chunks = split_qa_pairs(qa_pairs)
                    all_chunks.extend(chunks)
                    logger.info(
                        f"  {filename}: {len(qa_pairs)} QA pairs "
                        f"→ {len(chunks)} chunks"
                    )

    # 2. Transcribe videos
    if not args.skip_videos and os.path.isdir(args.video_dir):
        logger.info(f"=== Transcribing videos from {args.video_dir} ===")
        results = transcribe_videos_in_directory(
            args.video_dir, args.whisper_model)
        for r in results:
            chunks = split_text(r["text"], args.chunk_size)
            all_chunks.extend(chunks)
            logger.info(
                f"  {r['video_file']}: {len(r['segments'])} segments "
                f"→ {len(chunks)} chunks"
            )

    # 3. Embed and store
    if all_chunks:
        logger.info(f"=== Embedding and storing {len(all_chunks)} chunks ===")
        count = embed_and_store(all_chunks, qdrant)
        logger.info(f"Done! Stored {count} chunks to Qdrant.")
    else:
        logger.warning(
            "No chunks generated. Is data/chat_raw/ or data/videos/ populated?"
        )


if __name__ == "__main__":
    main()
