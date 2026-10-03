# tests/test_eval_messages_200b.py
"""第二批 200 条测试消息集的自检：条数、编号唯一、不与第一批重复、导出正确。"""

import csv
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.eval_messages_200 import GROUPS, EXPECTED_VALUES, MESSAGES  # noqa: E402
from scripts.eval_messages_200 import counts, export  # noqa: E402
from scripts.eval_messages_200b import MESSAGES_B  # noqa: E402


def test_total_is_200():
    assert len(MESSAGES_B) == 200


def test_ids_unique_and_group_letter_first():
    ids = [m[0] for m in MESSAGES_B]
    assert len(set(ids)) == len(ids), "编号必须唯一"
    assert all(m[0][0] in GROUPS for m in MESSAGES_B), "首字母必须是大类字母"


def test_expected_values_legal():
    bad = [m[0] for m in MESSAGES_B if m[3] not in EXPECTED_VALUES]
    assert not bad, bad


def test_no_overlap_with_first_batch():
    """第二批必须是**新的句面**，否则只是把第一批背下来了。"""
    first = {m[2] for m in MESSAGES}
    dup = sorted({m[2] for m in MESSAGES_B} & first)
    assert not dup, f"与第一批重复：{dup}"


def test_only_intended_internal_duplicates():
    """只允许"同句连发三次"那组重复（测去重），别的都要唯一。"""
    c = Counter(m[2] for m in MESSAGES_B)
    dup = {k: v for k, v in c.items() if v > 1}
    assert dup == {"你们营业到几点": 3}, dup


def test_every_group_and_type_present():
    groups = Counter(m[0][0] for m in MESSAGES_B)
    assert set(groups) == set(GROUPS)
    assert all(n >= 10 for n in groups.values()), groups
    # 第二批允许少量"单题类型"（镜头/配件清单、发票这类只有一问），
    # 但类型总数不能少 —— 覆盖面靠类型数保证
    types = Counter(m[1] for m in MESSAGES_B)
    assert len(types) >= 35, f"类型不够丰富：{len(types)}"
    assert min(types.values()) >= 1
    assert sum(1 for v in types.values() if v == 1) <= 3


def test_no_newlines_and_no_blank():
    for qid, cat, msg, exp, ref in MESSAGES_B:
        assert msg.strip() and msg == msg.strip(), qid
        assert "\n" not in msg and "\r" not in msg, qid


def test_counts_and_export(tmp_path):
    stat = counts(MESSAGES_B)
    assert stat["total"] == 200
    assert sum(stat["by_group"].values()) == 200

    csv_path, txt_path = export(tmp_path, messages=MESSAGES_B, prefix="messages_200b")
    rows = list(csv.reader(csv_path.read_text(encoding="utf-8-sig").splitlines()))
    assert len(rows) == 201
    lines = txt_path.read_text(encoding="utf-8").rstrip("\n").split("\n")
    assert len(lines) == 200
    assert lines[0] == MESSAGES_B[0][2]
