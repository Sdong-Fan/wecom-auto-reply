# tests/test_demo_stats.py
"""看板「示例数据」：把离线评测结果回放成接待记录。

为什么要测：
* 这份数据是给**店主第一次打开看板**时看的（不然是一片空白，看不出这个功能讲什么），
  它必须能算出正常口径，否则演示出来就是一堆 0；
* 它**绝不能**混进真实日志（`logs/auto_replies.jsonl`），也不能用真实客户名。
"""

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from scripts.make_demo_stats import CUSTOMERS, build, source_of, write
from stats import aggregate as agg


def _rows():
    """两条评测结果：一条该自动答、一条该转人工。"""
    return [
        {"id": "A01", "question": "索尼A7M4一天多少钱", "expected": "auto",
         "dispatch_level": "auto_send", "guard_decision": "pass",
         "guard_reason": "all_checks_passed", "retrieval_score": 0.9,
         "reason": "", "reply": "90 元/天", "_src": "fake.json"},
        {"id": "C01", "question": "能不能便宜点", "expected": "escalate",
         "dispatch_level": "human_handle", "guard_decision": "must_escalate",
         "guard_reason": "议价加码", "retrieval_score": 0.7,
         "reason": "议价加码", "reply": "", "_src": "fake.json"},
    ]


def test_source_mapping():
    """评测口径 → 运行时日志口径。"""
    assert source_of({"dispatch_level": "auto_send"}) == ("auto", "auto_send")
    assert source_of({"dispatch_level": "human_handle"}) == ("hold", "human_handle")
    assert source_of({"dispatch_level": "no_reply"}) == ("no_reply", "no_reply")


def test_build_produces_consistent_records():
    built = build(_rows(), days=7, seed=1)
    lines = built["log_lines"]
    assert len(lines) == 2
    by_src = {r["source"]: r for r in lines}
    assert set(by_src) == {"auto", "hold"}
    # 时间戳要能排序、要落在最近 7 天里
    ts = [r["timestamp"] for r in lines]
    assert ts == sorted(ts)
    # 每条都要有决策路径（看板按它分组）
    assert all(r["decision_path"] for r in lines)
    assert by_src["hold"]["decision_path"] == "议价加码"
    assert by_src["auto"]["decision_path"] == "正常直答"
    # 转人工的问题要进"我该补什么"
    assert "能不能便宜点" in built["unanswered"]


def test_written_files_are_aggregatable(tmp_path):
    """写出来的目录结构必须能被聚合层直接读（看板不用改一行读取逻辑）。"""
    rows = _rows()
    built = build(rows, days=3, seed=2)
    write(tmp_path, built, rows, 3)

    assert (tmp_path / "logs" / "auto_replies.jsonl").exists()
    assert (tmp_path / "data" / "unanswered.json").exists()
    assert (tmp_path / "说明.json").exists()

    st = agg.aggregate(date.today() - timedelta(days=2), date.today(), root=tmp_path)
    assert st.replies == 2          # auto + hold 都算接待
    assert st.auto == 1 and st.hold == 1
    assert st.solve_rate == 0.5
    assert st.pending_now >= 0
    assert st.median_reply_seconds is not None   # 会话配对生成了响应时长


def test_never_touches_real_logs(tmp_path, monkeypatch):
    """示例数据只能写进指定目录 —— 绝不能污染真实日志。"""
    built = build(_rows(), days=3, seed=3)
    write(tmp_path, built, _rows(), 3)
    real = Path("logs/auto_replies.jsonl")
    before = real.read_text(encoding="utf-8") if real.exists() else ""
    # 只可能写到 tmp_path：真实日志的内容不该包含示例数据的标记
    assert "_demo_from" not in before


def test_only_fictional_customer_names(tmp_path):
    """示例数据是给别人看的：客户名必须全是虚构的。"""
    rows = _rows() * 30
    built = build(rows, days=5, seed=4)
    write(tmp_path, built, rows, 5)
    raw = (tmp_path / "logs" / "auto_replies.jsonl").read_text(encoding="utf-8")
    names = {json.loads(l)["customer_name"] for l in raw.splitlines() if l.strip()}
    assert names and names <= set(CUSTOMERS), names
