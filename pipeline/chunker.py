# pipeline/chunker.py
"""Text chunking for knowledge base preparation.

Uses sentence-boundary-aware splitting with configurable overlap.
"""

import re
import logging

logger = logging.getLogger(__name__)

SENTENCE_BOUNDARY = re.compile(r'[。！？!?\n](?=\s*\S)')


def split_text(text: str, chunk_size: int = 500, overlap: int = 100) -> list[str]:
    """Split text into chunks roughly `chunk_size` chars, with `overlap` chars overlap."""
    if len(text) <= chunk_size:
        return [text]

    sentences = SENTENCE_BOUNDARY.split(text)

    if len(sentences) <= 1:
        chunks = []
        for i in range(0, len(text), chunk_size - overlap):
            chunk = text[i:i + chunk_size]
            if chunk.strip():
                chunks.append(chunk)
        return chunks

    chunks = []
    current = ""
    for sentence in sentences:
        if len(current) + len(sentence) > chunk_size and current:
            chunks.append(current.strip())
            overlap_text = current[-overlap:] if len(current) > overlap else current
            current = overlap_text + sentence
        else:
            current += sentence

    if current.strip():
        chunks.append(current.strip())

    return chunks


def split_qa_pairs(qa_pairs: list, chunk_size: int = 500,
                   overlap: int = 100) -> list[str]:
    """Convert QA pairs to searchable text chunks."""
    chunks = []
    for qa in qa_pairs:
        text = f"客户问题: {qa.question}\n销售回答: {qa.answer}"
        chunks.append(text)
    return chunks
