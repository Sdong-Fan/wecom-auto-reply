"""Integration test: verify eval pipeline modules are importable and consistent."""

import os
import pytest
from pathlib import Path


def test_eval_module_structure():
    """Verify all eval modules are importable with expected APIs."""
    from eval.scorer import score_reply, build_judge_prompt, parse_judge_response
    from eval.reporter import format_report, _format_section_summary

    # run.py uses lazy imports for rag.* (torch), safe to import here
    from eval.run import load_qa_pairs, add_ocr_noise

    pairs = load_qa_pairs()
    assert len(pairs) == 20
    assert callable(add_ocr_noise)
    assert callable(format_report)


def test_add_ocr_noise_all_types():
    """All OCR noise types produce different strings containing original."""
    from eval.run import add_ocr_noise
    original = "抖音账号检测在哪里？"

    for noise_type in ("prefix", "suffix", "multi"):
        result = add_ocr_noise(original, noise_type)
        assert original in result, \
            f"Noise type {noise_type}: original question must be in result"
        assert result != original, \
            f"Noise type {noise_type}: result must differ from original"


@pytest.mark.skipif(
    os.environ.get("SKIP_EMBED_TESTS") == "1",
    reason="SKIP_EMBED_TESTS=1 — requires torch/sentence_transformers"
)
@pytest.mark.skipif(
    not os.environ.get("DEEPSEEK_API_KEY"),
    reason="DEEPSEEK_API_KEY not set — requires API access"
)
def test_eval_pipeline_produces_report():
    """Full pipeline run produces report with expected sections."""
    import asyncio
    from eval.run import run_eval, run_ocr_noise_eval
    from eval.reporter import format_report

    results = asyncio.run(run_eval())
    assert len(results) == 20, f"Expected 20 results, got {len(results)}"

    for r in results:
        assert "scores" in r
        assert "accuracy" in r["scores"]
        assert "guard_decision" in r

    noise_results = asyncio.run(run_ocr_noise_eval())
    assert len(noise_results) == 5, \
        f"Expected 5 noise results, got {len(noise_results)}"

    report = format_report(results, noise_results)
    assert "Eval Report" in report
    assert "总览" in report
    assert "通过率" in report
    assert "幻觉率" in report
    assert "OCR 噪声影响" in report

    Path("logs").mkdir(exist_ok=True)
    Path("logs/eval_report.txt").write_text(report, encoding="utf-8")
    print(f"\nReport written to logs/eval_report.txt")
    print(report)
