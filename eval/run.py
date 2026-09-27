# eval/run.py
"""Eval harness main entry: python -m eval.run

Pipeline: load QA → embed → search → generate → guard → score → report.
Outputs to logs/eval_report.txt.

RAG modules (embed_query, retriever, generator, guard) are imported lazily
inside async functions to avoid triggering torch/sentence_transformers
DLL loading at module import time.
"""

import asyncio
import json
import logging
import time
from pathlib import Path

from eval.scorer import score_reply
from eval.reporter import format_report

logger = logging.getLogger(__name__)

QA_PATH = Path("eval/labeled_qa.json")
REPORT_PATH = Path("logs/eval_report.txt")
QDRANT_PATH = "data/qdrant"


def load_qa_pairs() -> list[dict]:
    """Load labeled QA pairs from JSON file."""
    return json.loads(QA_PATH.read_text(encoding="utf-8"))


def add_ocr_noise(question: str, noise_type: str = "prefix") -> str:
    """Add realistic OCR noise to a question.

    Args:
        question: Original clean question.
        noise_type: "prefix" | "suffix" | "multi"
    """
    if noise_type == "prefix":
        return f"在吗?  {question}"
    elif noise_type == "suffix":
        return f"{question} 期五 少"
    elif noise_type == "multi":
        return f"你好 你们的产品有哪些？  {question}"
    return question


async def run_single_qa(qa: dict, qdrant) -> dict:
    """Run a single QA through the full pipeline and return scored result."""
    from rag.embed_query import embed_query
    from rag.retriever import search
    from rag.generator import generate_reply
    from rag.guard import classify_and_dispatch

    question = qa["question"]
    expected = qa.get("expected_points", [])
    forbidden = qa.get("forbidden_points", [])

    # Embed → Search
    qv = embed_query(question)
    results = search(qv, qdrant, top_k=5)
    chunks = [(r.payload or {}).get("text", "") for r in results]
    scores = [r.score for r in results]
    top_score = scores[0] if scores else 0.0

    # Generate
    reply = await generate_reply(question, chunks)

    # Guard
    guard_result = classify_and_dispatch(scores, reply, chunks)

    # LLM Judge
    judge_scores = await score_reply(question, reply, expected, forbidden, chunks)

    return {
        "qa_id": qa["id"],
        "question": question,
        "reply": reply,
        "expected_points": expected,
        "forbidden_points": forbidden,
        "chunks": chunks,
        "category": qa.get("category", "未分类"),
        "scores": judge_scores,
        "guard_decision": guard_result.decision,
        "guard_reason": guard_result.reason,
        "retrieval_score": top_score,
    }


async def run_eval() -> list[dict]:
    """Run full eval pipeline. Returns list of scored results."""
    from qdrant_client import QdrantClient
    from rag.retriever import ensure_collection

    qa_pairs = load_qa_pairs()
    qdrant = QdrantClient(path=QDRANT_PATH)
    ensure_collection(qdrant)

    results = []
    for qa in qa_pairs:
        try:
            result = await run_single_qa(qa, qdrant)
            results.append(result)
            total = result["scores"]["total"]
            logger.info(
                f"{qa['id']}: {total}/15 "
                f"(acc={result['scores']['accuracy']} "
                f"cmp={result['scores']['completeness']} "
                f"saf={result['scores']['safety']}) "
                f"guard={result['guard_decision']}"
            )
        except Exception as e:
            logger.error(f"{qa['id']} failed: {e}")
            results.append({
                "qa_id": qa["id"],
                "question": qa["question"],
                "reply": f"ERROR: {e}",
                "expected_points": qa.get("expected_points", []),
                "forbidden_points": qa.get("forbidden_points", []),
                "chunks": [],
                "category": qa.get("category", "未分类"),
                "scores": {
                    "accuracy": 0, "completeness": 0, "safety": 0,
                    "has_hallucination": False, "hallucination_detail": str(e),
                    "overall_comment": "PIPELINE_ERROR", "total": 0,
                    "parse_error": True,
                },
                "guard_decision": "error",
                "guard_reason": str(e),
                "retrieval_score": 0.0,
            })

    qdrant.close()
    return results


async def run_ocr_noise_eval() -> list[dict]:
    """Re-run 5 selected QAs with OCR noise and return results."""
    from qdrant_client import QdrantClient
    from rag.retriever import ensure_collection

    qa_pairs = load_qa_pairs()
    qdrant = QdrantClient(path=QDRANT_PATH)
    ensure_collection(qdrant)

    selected_ids = {"qa_01", "qa_04", "qa_11", "qa_17", "qa_19"}
    noise_types = ["prefix", "suffix", "multi", "prefix", "suffix"]

    results = []
    noise_idx = 0
    for qa in qa_pairs:
        if qa["id"] not in selected_ids:
            continue
        noise_type = noise_types[noise_idx % len(noise_types)]
        noise_idx += 1
        noisy_qa = dict(qa)
        noisy_qa["question"] = add_ocr_noise(qa["question"], noise_type)
        try:
            result = await run_single_qa(noisy_qa, qdrant)
            result["qa_id"] = qa["id"]
            result["noise_type"] = noise_type
            results.append(result)
        except Exception as e:
            logger.error(f"Noise eval {qa['id']} failed: {e}")

    qdrant.close()
    return results


def main():
    """Entry point: python -m eval.run"""
    from dotenv import load_dotenv
    load_dotenv()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    start = time.monotonic()
    logger.info("Starting eval harness...")

    results = asyncio.run(run_eval())
    logger.info(f"Main eval complete: {len(results)} QAs")

    noise_results = asyncio.run(run_ocr_noise_eval())
    logger.info(f"OCR noise eval complete: {len(noise_results)} variants")

    report = format_report(results, noise_results)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")

    elapsed = time.monotonic() - start
    logger.info(f"Report written to {REPORT_PATH} ({elapsed:.1f}s)")
    try:
        print(report)
    except UnicodeEncodeError:
        # Windows GBK console can't render emoji — report is already in the file
        print(report.encode("ascii", errors="replace").decode("ascii"))


if __name__ == "__main__":
    main()
