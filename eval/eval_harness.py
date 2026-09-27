"""Evaluation harness for auto-reply quality.

Runs 20 labeled QA pairs through the RAG pipeline and scores
each reply on 3 dimensions: exact match, BLEU-4, embedding cosine similarity.
"""

import json
import sys
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))


def exact_match_score(predicted: str, expected: str) -> float:
    """Normalized exact match (case-insensitive, whitespace-normalized)."""
    p = "".join(predicted.split()).lower()
    e = "".join(expected.split()).lower()
    return 1.0 if p == e else 0.0


def bleu4_score(predicted: str, expected: str) -> float:
    """Simple BLEU-4 approximation (character n-gram for Chinese)."""
    def _ngrams(text: str, n: int) -> set:
        chars = list(text)
        return {"".join(chars[i:i+n]) for i in range(len(chars) - n + 1)}

    p = predicted.strip()
    e = expected.strip()
    if len(p) < 4 or len(e) < 4:
        return 1.0 if p == e else 0.0

    score = 0.0
    for n in [1, 2, 3, 4]:
        ref = _ngrams(e, n)
        hyp = _ngrams(p, n)
        if not ref:
            continue
        overlap = len(ref & hyp)
        score += overlap / max(len(hyp), 1)
    return score / 4.0


def embedding_similarity(predicted: str, expected: str) -> float:
    """Cosine similarity using BGE embeddings."""
    try:
        from rag.embed_query import embed_query
        pv = embed_query(predicted)
        ev = embed_query(expected)
        dot = sum(a * b for a, b in zip(pv, ev))
        norm_p = sum(a * a for a in pv) ** 0.5
        norm_e = sum(b * b for b in ev) ** 0.5
        if norm_p == 0 or norm_e == 0:
            return 0.0
        return dot / (norm_p * norm_e)
    except Exception:
        return 0.0


def run_eval(data_path: str = None) -> dict:
    """Run full evaluation and return results dict."""
    if data_path is None:
        data_path = Path(__file__).parent / "labeled_qa.json"

    with open(data_path, encoding="utf-8") as f:
        data = json.load(f)

    results = []
    for pair in data["pairs"]:
        predicted = _generate_reply(pair["question"])
        expected = pair["expected"]
        results.append({
            "id": pair["id"],
            "category": pair["category"],
            "question": pair["question"],
            "expected": expected,
            "predicted": predicted,
            "exact_match": exact_match_score(predicted, expected),
            "bleu4": round(bleu4_score(predicted, expected), 3),
            "embedding_sim": round(embedding_similarity(predicted, expected), 3),
        })

    avg_em = sum(r["exact_match"] for r in results) / len(results)
    avg_bleu = sum(r["bleu4"] for r in results) / len(results)
    avg_sim = sum(r["embedding_sim"] for r in results) / len(results)

    return {
        "total": len(results),
        "avg_exact_match": round(avg_em, 3),
        "avg_bleu4": round(avg_bleu, 3),
        "avg_embedding_sim": round(avg_sim, 3),
        "details": results,
    }


def _generate_reply(question: str) -> str:
    """Generate a reply using the RAG pipeline (mockable for testing)."""
    try:
        from rag.embed_query import embed_query
        from rag.retriever import search
        from rag.generator import generate_reply
        from qdrant_client import QdrantClient
        import asyncio

        qdrant = QdrantClient(path="data/qdrant")
        qv = embed_query(question)
        results = search(qv, qdrant, top_k=5)
        chunks = [(r.payload or {}).get("text", "") for r in results]
        reply = asyncio.get_event_loop().run_until_complete(
            generate_reply(question, chunks))
        return reply
    except Exception as e:
        return f"[EVAL_ERROR: {e}]"


if __name__ == "__main__":
    result = run_eval()
    print(json.dumps({
        "total": result["total"],
        "avg_exact_match": result["avg_exact_match"],
        "avg_bleu4": result["avg_bleu4"],
        "avg_embedding_sim": result["avg_embedding_sim"],
    }, ensure_ascii=False, indent=2))
