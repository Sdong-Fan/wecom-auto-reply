"""TDD: Eval report generator."""

from eval.reporter import format_report, _format_section_summary


def make_sample_result(qa_id, accuracy, completeness, safety,
                        has_hallucination=False, guard_decision="pass",
                        category="产品功能"):
    """Helper to create a sample eval result."""
    return {
        "qa_id": qa_id,
        "question": f"测试问题 {qa_id}",
        "reply": "测试回复",
        "expected_points": ["要点1"],
        "forbidden_points": [],
        "chunks": ["知识片段1"],
        "category": category,
        "scores": {
            "accuracy": accuracy,
            "completeness": completeness,
            "safety": safety,
            "has_hallucination": has_hallucination,
            "hallucination_detail": "编造了XXX" if has_hallucination else "",
            "overall_comment": "OK",
            "total": accuracy + completeness + safety,
            "parse_error": False,
        },
        "guard_decision": guard_decision,
        "retrieval_score": 0.85,
    }


def test_format_report_basic():
    """format_report generates a non-empty string with key sections."""
    results = [
        make_sample_result("qa_01", 5, 5, 5),
        make_sample_result("qa_02", 3, 3, 3),
    ]
    report = format_report(results)
    assert "Eval Report" in report
    assert "总览" in report
    assert "qa_01" in report
    assert "qa_02" in report


def test_format_report_counts_pass_fail():
    """format_report correctly counts pass/fail based on total >= 9 threshold."""
    results = [
        make_sample_result("qa_01", 5, 5, 5),   # total=15, pass
        make_sample_result("qa_02", 4, 4, 4),   # total=12, pass
        make_sample_result("qa_03", 2, 2, 2),   # total=6, fail
        make_sample_result("qa_04", 3, 3, 3),   # total=9, pass (threshold)
    ]
    report = format_report(results)
    assert "3/4" in report or "75%" in report


def test_format_report_guard_mismatch():
    """format_report flags guard mismatch: good reply blocked or bad reply passed."""
    results = [
        make_sample_result("qa_01", 5, 5, 5, guard_decision="block"),
        make_sample_result("qa_02", 1, 1, 1, guard_decision="pass"),
    ]
    report = format_report(results)
    assert "guard" in report.lower()


def test_format_report_hallucination_count():
    """format_report counts hallucinations."""
    results = [
        make_sample_result("qa_01", 5, 5, 5, has_hallucination=False),
        make_sample_result("qa_02", 4, 4, 4, has_hallucination=True),
        make_sample_result("qa_03", 3, 3, 3, has_hallucination=True),
    ]
    report = format_report(results)
    assert "1/3" in report or "33%" in report or "幻觉" in report


def test_format_section_summary():
    """_format_section_summary groups by category."""
    results = [
        make_sample_result("qa_01", 5, 5, 5, category="价格相关"),
        make_sample_result("qa_02", 4, 4, 4, category="价格相关"),
        make_sample_result("qa_03", 1, 1, 1, category="售后政策"),
    ]
    summary = _format_section_summary(results)
    assert "价格相关" in summary
    assert "售后政策" in summary
