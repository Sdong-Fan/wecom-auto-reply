# tests/test_eval_messages_200.py
"""200 条测试消息集的自检：条数、编号唯一、期望值合法、导出格式正确。

这些检查不调模型、不碰数据，纯静态 —— 保证这套"尺子"本身没写坏。
"""

import csv
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.eval_messages_200 import (  # noqa: E402
    EXPECTED_VALUES,
    GROUPS,
    MESSAGES,
    counts,
    export,
)


def test_total_is_200():
    assert len(MESSAGES) == 200


def test_ids_unique_and_ordered():
    ids = [m[0] for m in MESSAGES]
    assert len(set(ids)) == len(ids), "编号必须唯一"
    assert ids == sorted(ids, key=lambda x: (x[0], int(x[1:]))), "编号应按大类+序号排列"


def test_expected_values_legal():
    bad = [m[0] for m in MESSAGES if m[3] not in EXPECTED_VALUES]
    assert not bad, f"期望行为只能是 {EXPECTED_VALUES}，异常：{bad}"


def test_all_fields_non_empty():
    for qid, cat, msg, exp, ref in MESSAGES:
        assert qid and cat and ref, f"{qid} 有空字段"
        assert msg.strip(), f"{qid} 消息为空"
        assert msg == msg.strip(), f"{qid} 消息首尾有空白"


def test_no_newlines_in_messages():
    """消息要能一行一条地手工发出去，不能带换行。"""
    bad = [m[0] for m in MESSAGES if "\n" in m[2] or "\r" in m[2]]
    assert not bad, f"消息里不该有换行：{bad}"


def test_every_group_and_type_present():
    groups = Counter(m[0][0] for m in MESSAGES)
    assert set(groups) == set(GROUPS), "每个大类都要有题"
    assert all(n >= 10 for n in groups.values()), f"大类题量太少的：{groups}"

    types = Counter(m[1] for m in MESSAGES)
    assert len(types) >= 35, f"类型不够丰富：{len(types)}"
    assert min(types.values()) >= 2, f"这些类型只有 1 条：{[t for t, n in types.items() if n == 1]}"


def test_guardrail_cases_exist():
    """安全类题量必须够 —— 这是这个项目最核心的护栏。"""
    exp = Counter(m[3] for m in MESSAGES)
    assert exp["escalate"] >= 30, "应转人工的题太少"
    assert exp["no_reply"] >= 8, "不该回的题太少"
    assert exp["auto"] >= 100, "应自动答的题太少"
    assert exp["auto"] + exp["escalate"] + exp["no_reply"] == 200


def test_counts_matches_data():
    stat = counts()
    assert stat["total"] == 200
    assert sum(stat["by_group"].values()) == 200
    assert sum(stat["by_expected"].values()) == 200


def test_export_writes_200_rows(tmp_path):
    csv_path, txt_path = export(tmp_path)
    rows = list(csv.reader(csv_path.read_text(encoding="utf-8-sig").splitlines()))
    assert rows[0][0] == "编号"
    assert len(rows) == 201, "表头 + 200 条"

    lines = txt_path.read_text(encoding="utf-8").rstrip("\n").split("\n")
    assert len(lines) == 200
    assert lines[0] == MESSAGES[0][2]
    assert lines[-1] == MESSAGES[-1][2]
