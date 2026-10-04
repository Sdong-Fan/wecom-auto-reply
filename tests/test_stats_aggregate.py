# tests/test_stats_aggregate.py
"""看板聚合层的口径测试（固定样本 → 断言每个数字）。

为什么要这么细：看板上的数字如果口径漂了，店主会按错的数字做决定；
而且"接待数到底怎么算"这件事是店主亲自拍的（以回复为单位），必须钉死。
"""

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from stats import aggregate as agg


# ── 固定样本 ──────────────────────────────────────────────────────────────

def _write_fixture(root: Path, rows: list[dict], pairs: list[dict] | None = None,
                   pending: int = 0, unanswered: dict | None = None):
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "data" / "state").mkdir(parents=True, exist_ok=True)
    (root / "data" / "context").mkdir(parents=True, exist_ok=True)
    with (root / "logs" / "auto_replies.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (root / "data" / "state" / "pending_queue.json").write_text(
        json.dumps({"items": {f"k{i}": {"customer_message": "x"} for i in range(pending)}}),
        encoding="utf-8")
    if unanswered:
        (root / "data" / "unanswered.json").write_text(
            json.dumps(unanswered, ensure_ascii=False), encoding="utf-8")
    if pairs:
        by_customer: dict[str, list] = {}
        for p in pairs:
            by_customer.setdefault(p["customer"], []).append(p)
        for cust, ps in by_customer.items():
            with (root / "data" / "context" / f"{cust}.jsonl").open("w", encoding="utf-8") as f:
                for p in ps:
                    f.write(json.dumps({"role": "customer", "text": p["message"],
                                        "timestamp": p["asked_at"].isoformat()},
                                       ensure_ascii=False) + "\n")
                    f.write(json.dumps({"role": "assistant", "text": "回复",
                                        "timestamp": p["replied_at"].isoformat()},
                                       ensure_ascii=False) + "\n")


TODAY = date.today()


def _ts(day_offset: int, hour: int = 10, minute: int = 0) -> str:
    d = TODAY - timedelta(days=day_offset)
    return datetime(d.year, d.month, d.day, hour, minute).isoformat()


BASE_ROWS = [
    # 今天：机器人真答 2 条（不同客户）
    {"timestamp": _ts(0, 9), "customer_name": "甲", "customer_message": "A7M4多少钱",
     "ai_reply": "90", "dispatch_level": "auto_send", "source": "auto",
     "decision_path": "正常直答"},
    {"timestamp": _ts(0, 11), "customer_name": "乙", "customer_message": "押金多少",
     "ai_reply": "3 成", "dispatch_level": "auto_send", "source": "auto",
     "decision_path": "正常直答"},
    # 今天：转人工 1 条（占位语也发了 → 算接待，但不算自动解决）
    {"timestamp": _ts(0, 12), "customer_name": "丙", "customer_message": "能不能便宜点",
     "ai_reply": "", "dispatch_level": "human_handle", "source": "hold",
     "decision_path": "议价加码"},
    # 今天：不该回 1 条（不计接待）
    {"timestamp": _ts(0, 13), "customer_name": "丁", "customer_message": "谢谢",
     "ai_reply": "", "dispatch_level": "no_reply", "source": "no_reply",
     "decision_path": "无信息量/收尾"},
    # 今天：人工改稿发出 1 条（不计接待）
    {"timestamp": _ts(0, 14), "customer_name": "戊", "customer_message": "有货吗",
     "ai_reply": "我帮您问", "dispatch_level": "auto_send", "source": "human_edit",
     "decision_path": "人工改稿后发出"},
    # 昨天：机器人真答 3 条
    {"timestamp": _ts(1, 10), "customer_name": "甲", "customer_message": "运费多少",
     "ai_reply": "15", "dispatch_level": "auto_send", "source": "auto",
     "decision_path": "正常直答"},
    {"timestamp": _ts(1, 11), "customer_name": "己", "customer_message": "能开票吗",
     "ai_reply": "可以", "dispatch_level": "auto_send", "source": "auto",
     "decision_path": "正常直答"},
    {"timestamp": _ts(1, 15), "customer_name": "庚", "customer_message": "越界",
     "ai_reply": "婉拒", "dispatch_level": "auto_send", "source": "auto",
     "decision_path": "越界婉拒"},
    # 8 天前（在"近 7 天"之外，检验区间过滤）
    {"timestamp": _ts(8, 10), "customer_name": "辛", "customer_message": "很久以前",
     "ai_reply": "x", "dispatch_level": "auto_send", "source": "auto",
     "decision_path": "正常直答"},
]


@pytest.fixture
def root(tmp_path):
    _write_fixture(tmp_path, BASE_ROWS, pending=3, unanswered={
        "items": {"k": {"question": "国庆有货吗", "count": 5, "reason": "库存与档期",
                        "last_ts": 1}}})
    return tmp_path


# ── 口径 ──────────────────────────────────────────────────────────────────

def test_today_kpi(root):
    """今日：接待 = auto(2) + hold(1) = 3；自动解决率 = 2/3。"""
    st = agg.aggregate(TODAY, TODAY, root=root)
    assert st.replies == 3
    assert st.auto == 2
    assert st.hold == 1
    assert st.no_reply == 1        # 不计接待
    assert st.human == 1           # 人工改稿发出的，单独算
    # 独立客户 = "被回复过的客户"（机器人回的 + 你人工回的）：甲/乙/丙/戊 = 4
    # 只发"谢谢"没被回复的（丁）不算 —— 否则感谢语会把客户数刷高
    assert st.customers == 4
    assert round(st.solve_rate, 3) == round(2 / 3, 3)


def test_greeting_not_counted_as_reply(root):
    """欢迎语不能虚高接待量。"""
    rows = BASE_ROWS + [{"timestamp": _ts(0, 8), "customer_name": "壬",
                         "customer_message": "", "ai_reply": "在的",
                         "dispatch_level": "auto_send", "source": "greeting",
                         "decision_path": "欢迎语"}]
    root2 = root / "sub"
    _write_fixture(root2, rows)
    st = agg.aggregate(TODAY, TODAY, root=root2)
    assert st.replies == 3         # 没被欢迎语影响
    assert st.greeting == 1


def test_last_7_days_range_filter(root):
    """近 7 天：8 天前那条不能算进来。"""
    st = agg.last_days(7, root=root)
    assert st.days == 7
    assert st.replies == 3 + 3     # 今天 3 + 昨天 3
    assert st.auto == 5            # 越界婉拒也算"机器人真答"（source=auto）
    assert "很久以前" not in json.dumps(st.sample_messages, ensure_ascii=False)


def test_start_after_end_is_swapped(root):
    st = agg.aggregate(TODAY, TODAY - timedelta(days=2), root=root)
    assert st.start <= st.end
    assert st.days == 3


def test_trend_buckets_daily_vs_hourly(root):
    """1 天 → 24 个小时格；多天 → 每天一格。"""
    one = agg.aggregate(TODAY, TODAY, root=root)
    assert len(one.buckets) == 24
    assert one.by_hour[9] == 1
    week = agg.last_days(7, root=root)
    assert len(week.buckets) == 7
    labels = [b.label for b in week.buckets]
    assert labels[-1] == TODAY.strftime("%m-%d")


def test_guardrail_paths_and_samples(root):
    """护栏统计与"客户原话样例"要能对上（看板点开看得到原话）。"""
    st = agg.aggregate(TODAY, TODAY, root=root)
    assert st.by_path["议价加码"] == 1
    assert st.sample_messages["议价加码"] == ["能不能便宜点"]
    assert st.by_path["正常直答"] == 2


def test_pending_is_realtime_not_range_bound(root):
    """待人工是"现在积压多少"，不随区间变。"""
    a = agg.aggregate(TODAY - timedelta(days=1), TODAY - timedelta(days=1), root=root)
    b = agg.aggregate(TODAY, TODAY, root=root)
    assert a.pending_now == b.pending_now == 3


def test_unanswered_top_is_sorted(root):
    st = agg.aggregate(TODAY, TODAY, root=root)
    assert st.unanswered_top[0]["question"] == "国庆有货吗"
    assert st.unanswered_top[0]["count"] == 5


def test_median_reply_seconds(root):
    """中位响应从会话配对算（含转人工占位语）。"""
    pairs = [
        {"customer": "甲", "message": "问1",
         "asked_at": datetime(TODAY.year, TODAY.month, TODAY.day, 9, 0, 0),
         "replied_at": datetime(TODAY.year, TODAY.month, TODAY.day, 9, 0, 2)},
        {"customer": "乙", "message": "问2",
         "asked_at": datetime(TODAY.year, TODAY.month, TODAY.day, 9, 5, 0),
         "replied_at": datetime(TODAY.year, TODAY.month, TODAY.day, 9, 5, 4)},
        {"customer": "丙", "message": "问3",
         "asked_at": datetime(TODAY.year, TODAY.month, TODAY.day, 9, 9, 0),
         "replied_at": datetime(TODAY.year, TODAY.month, TODAY.day, 9, 9, 30)},
    ]
    root2 = root / "ctx"
    _write_fixture(root2, BASE_ROWS, pairs=pairs)
    st = agg.aggregate(TODAY, TODAY, root=root2)
    assert st.median_reply_seconds == 4.0     # median(2,4,30)


# ── 兼容与健壮性 ───────────────────────────────────────────────────────────

def test_old_log_without_new_fields_still_works(root):
    """老日志没有 source/decision_path：不能崩，也不能把人工的算成自动。"""
    root2 = root / "old"
    _write_fixture(root2, [
        {"timestamp": _ts(0, 10), "customer_name": "老甲", "customer_message": "问",
         "ai_reply": "答", "dispatch_level": "auto_send"},   # 老记录 = 有 auto_send
        {"timestamp": _ts(0, 11), "customer_name": "老乙", "customer_message": "谢谢",
         "ai_reply": "", "dispatch_level": "no_reply"},
    ])
    st = agg.aggregate(TODAY, TODAY, root=root2)
    assert st.auto == 1
    assert st.no_reply == 1
    assert st.replies == 1


def test_empty_root_returns_zeros(tmp_path):
    st = agg.aggregate(TODAY, TODAY, root=tmp_path / "nothing")
    assert st.replies == 0 and st.auto == 0 and st.hold == 0
    assert st.solve_rate is None          # 0 分母 → None（看板显示 "—"）
    assert st.customers == 0
    assert len(st.buckets) == 24


def test_broken_json_lines_are_skipped(tmp_path):
    (tmp_path / "logs").mkdir(parents=True)
    (tmp_path / "logs" / "auto_replies.jsonl").write_text(
        '{"timestamp": "2026-10-03T10:00:00", "dispatch_level": "auto_send", "source": "auto"}\n'
        'not json at all\n'
        '{"timestamp": "garbage"}\n', encoding="utf-8")
    st = agg.aggregate(date(2026, 10, 3), date(2026, 10, 3), root=tmp_path)
    assert st.replies == 1        # 只认那条合法的
