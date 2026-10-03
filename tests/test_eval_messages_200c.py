# tests/test_eval_messages_200c.py
"""第三批 200 条测试消息集的自检（用于验证降低后的自动发阈值）。"""

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.eval_messages_200 import GROUPS, EXPECTED_VALUES, MESSAGES  # noqa: E402
from scripts.eval_messages_200 import counts, export  # noqa: E402
from scripts.eval_messages_200b import MESSAGES_B  # noqa: E402
from scripts.eval_messages_200c import MESSAGES_C  # noqa: E402


def test_total_is_200():
    assert len(MESSAGES_C) == 200


def test_ids_unique():
    ids = [m[0] for m in MESSAGES_C]
    assert len(set(ids)) == len(ids)
    assert all(m[0][0] in GROUPS for m in MESSAGES_C)


def test_expected_values_legal():
    assert not [m[0] for m in MESSAGES_C if m[3] not in EXPECTED_VALUES]


def test_no_overlap_with_other_batches():
    """第三批必须是新句面 —— 否则拿它选阈值等于用训练集调参。"""
    seen = {m[2] for m in MESSAGES} | {m[2] for m in MESSAGES_B}
    dup = sorted({m[2] for m in MESSAGES_C} & seen)
    assert not dup, f"与前两批重复：{dup}"


def test_only_intended_internal_duplicates():
    c = Counter(m[2] for m in MESSAGES_C)
    dup = {k: v for k, v in c.items() if v > 1}
    assert dup == {"你们营业时间是几点": 3}, dup


def test_groups_and_types_rich():
    groups = Counter(m[0][0] for m in MESSAGES_C)
    assert set(groups) == set(GROUPS)
    assert all(n >= 10 for n in groups.values()), groups
    assert len(Counter(m[1] for m in MESSAGES_C)) >= 35


def test_counts_and_export(tmp_path):
    stat = counts(MESSAGES_C)
    assert stat["total"] == 200
    csv_path, txt_path = export(tmp_path, messages=MESSAGES_C, prefix="messages_200c")
    rows = csv_path.read_text(encoding="utf-8-sig").splitlines()
    assert len(rows) == 201
    assert len(txt_path.read_text(encoding="utf-8").rstrip("\n").split("\n")) == 200
