"""TDD: Eval run.py main entry point."""

from pathlib import Path
import json


def test_labeled_qa_json_exists():
    """The labeled QA file must exist and contain 20 items."""
    path = Path("eval/labeled_qa.json")
    assert path.exists(), "eval/labeled_qa.json must exist"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data) == 20, f"Expected 20 QA pairs, got {len(data)}"
    for item in data:
        assert "id" in item, f"Missing id in {item}"
        assert "question" in item
        assert "expected_points" in item
        assert "forbidden_points" in item
        assert "category" in item


def test_eval_run_module_importable():
    """eval.run module must be importable with key functions."""
    import eval.run
    assert hasattr(eval.run, "run_eval"), \
        "eval.run must have a run_eval function"
    assert hasattr(eval.run, "load_qa_pairs"), \
        "eval.run must have load_qa_pairs"


def test_load_qa_pairs():
    """load_qa_pairs returns list of 20 dicts."""
    from eval.run import load_qa_pairs
    pairs = load_qa_pairs()
    assert len(pairs) == 20


def test_add_ocr_noise():
    """add_ocr_noise adds realistic OCR noise to questions."""
    from eval.run import add_ocr_noise

    result = add_ocr_noise("测试问题", "prefix")
    assert "在吗?" in result
    assert "测试问题" in result

    result = add_ocr_noise("测试问题", "suffix")
    assert "测试问题" in result

    result = add_ocr_noise("测试问题", "multi")
    assert "测试问题" in result
