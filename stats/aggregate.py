# stats/aggregate.py
"""运营看板的聚合层：把散落的落地数据汇总成一个区间结果（**只读**）。

数据源（全部是程序运行时自己写的，不需要额外埋点）：
* ``logs/auto_replies.jsonl``    —— 决策记录：谁发的、说了什么、走的哪条路
  （`source=auto` 机器人真答 / `hold` 转人工占位语 / `human_edit` 人工改稿发出 /
    `human_confirm` 人工直发草稿 / `greeting` 欢迎语）
* ``data/context/*.jsonl``       —— 完整会话（客户消息 + 客服回复 + 时间戳），算响应时长用
* ``data/state/pending_queue.json`` —— 待人工队列（看"现在积压多少"，不随区间变）
* ``data/unanswered.json``       —— 转人工问题排行（"我该补什么资料"）

口径（店主已确认，2026-10-03）：
* **接待 = 机器人发出的回复条数**（以回复为单位，不按客户消息条数算）；
  欢迎语与人工发出的**不计入**接待，单独统计 —— 否则欢迎语虚高接待量、
  人工改稿会被算成"机器人自动解决"
* 自动解决率 = ``auto ÷ (auto + hold)``
* 独立客户数 = **被回复过的客户数**（机器人回的 + 你人工回的）；
  只说"谢谢"而没被回复的不算 —— 否则感谢语会把客户数刷高
* 中位响应**含**转人工占位语（客户确实收到了回复），另算一行"人工真答"
* 问题分布按"命中了哪份资料"归类；命中不了的按 decision_path 归到越界/闲聊/无信息量…

设计约束：**纯函数 + 只读**。不写任何业务数据（导出由调用方做），
这样口径能被单测钉死，不会哪天悄悄变了。
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 一次"接待"由哪些 source 构成
REPLY_SOURCES = ("auto", "hold")          # 机器人发出的（计入接待）
HUMAN_SOURCES = ("human_edit", "human_confirm")   # 人工发出的（不计入接待）
GREETING_SOURCES = ("greeting",)          # 欢迎语（不计入接待）


@dataclass
class DayBucket:
    """按天（或按小时）的一格。"""
    label: str
    replies: int = 0        # 接待数（auto + hold）
    auto: int = 0
    hold: int = 0
    human: int = 0
    no_reply: int = 0

    @property
    def solve_rate(self) -> float | None:
        denom = self.auto + self.hold
        return (self.auto / denom) if denom else None


@dataclass
class Stats:
    """一个时间区间的统计结果。"""
    start: date
    end: date
    days: int
    # KPI
    replies: int = 0          # 接待（机器人发出的回复数）
    auto: int = 0
    hold: int = 0
    human: int = 0            # 人工发出的（改稿 + 直发草稿）
    greeting: int = 0
    no_reply: int = 0
    customers: int = 0        # 独立客户数（去重）
    pending_now: int = 0      # 当前待人工（实时，与区间无关）
    median_reply_seconds: float | None = None       # 中位响应（含占位语）
    median_human_seconds: float | None = None       # 人工真答的中位响应
    # 分布
    by_path: Counter = field(default_factory=Counter)        # 决策路径（护栏统计）
    by_source: Counter = field(default_factory=Counter)      # auto/hold/human_*/greeting
    by_hour: Counter = field(default_factory=Counter)        # 小时分布（今日视图用）
    buckets: list[DayBucket] = field(default_factory=list)   # 趋势
    unanswered_top: list[dict] = field(default_factory=list)  # 我该补什么
    sample_messages: dict = field(default_factory=dict)      # 决策路径 → 客户原话样例

    @property
    def solve_rate(self) -> float | None:
        denom = self.auto + self.hold
        return (self.auto / denom) if denom else None


# ── 读数据 ────────────────────────────────────────────────────────────────

def _parse_ts(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value)
    s = str(value).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s[:len(fmt) + 6], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def read_reply_log(path: Path) -> list[dict]:
    """读决策日志。老日志缺字段（没有 source/decision_path）时给默认值，不报错。"""
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = _parse_ts(r.get("timestamp"))
        if ts is None:
            continue
        level = r.get("dispatch_level") or ""
        src = r.get("source") or ("auto" if level == "auto_send" else "")
        if level == "no_reply":
            src = "no_reply"
        rows.append({
            "ts": ts, "customer": r.get("customer_name") or "",
            "message": r.get("customer_message") or "",
            "reply": r.get("ai_reply") or "",
            "level": level, "source": src,
            "path": r.get("decision_path") or "",
            "guard_reason": r.get("guard_reason") or "",
        })
    return rows


def read_context(path_dir: Path) -> list[dict]:
    """读会话文件：返回 {客户, 客户消息时间, 回复时间, 客户消息} 的配对。

    响应时长只能从这里算（决策日志里只有"发出去"的那一刻，没有客户发消息的时刻）。
    """
    pairs: list[dict] = []
    if not path_dir.exists():
        return pairs
    for f in sorted(path_dir.glob("*.jsonl")):
        rows = []
        for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        pending_msg, pending_ts = None, None
        for r in rows:
            role = (r.get("role") or r.get("type") or "").lower()
            ts = _parse_ts(r.get("timestamp") or r.get("ts") or r.get("time"))
            text = r.get("text") or r.get("content") or r.get("message") or ""
            if "customer" in role or "user" in role:
                pending_msg, pending_ts = text, ts
            elif ("assistant" in role or "bot" in role) and pending_ts and ts:
                pairs.append({"customer": f.stem, "message": pending_msg or "",
                              "asked_at": pending_ts, "replied_at": ts,
                              "seconds": (ts - pending_ts).total_seconds()})
                pending_msg, pending_ts = None, None
    return pairs


def read_pending_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 0
    return len((data or {}).get("items") or {})


def read_unanswered(path: Path, top: int = 10) -> list[dict]:
    """转人工问题排行（"我该补什么资料"）。"""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    items = (data or {}).get("items") or {}
    out = [{"question": v.get("question") or k, "count": int(v.get("count") or 0),
            "reason": v.get("reason") or "", "last": v.get("last_ts") or 0}
           for k, v in items.items()]
    out.sort(key=lambda x: (-x["count"], -x["last"]))
    return out[:top]


# ── 归类 ──────────────────────────────────────────────────────────────────

def classify_path(row: dict) -> str:
    """一条记录的归类标签（发给看板分组）。"""
    path = (row.get("path") or "").strip()
    if path:
        return path
    src = row.get("source") or ""
    if src == "greeting":
        return "欢迎语"
    if src in HUMAN_SOURCES:
        return "人工发出"
    if src == "no_reply":
        return "无信息量/收尾"
    if src == "auto":
        return "正常直答"
    if src == "hold":
        return "必须转人工"
    return "其他"


# ── 主入口 ────────────────────────────────────────────────────────────────

def aggregate(start: date, end: date, *, root: Path | None = None,
              unanswered_top: int = 10) -> Stats:
    """汇总 [start, end] 闭区间的数据。start > end 时自动交换。"""
    if start > end:
        start, end = end, start
    root = root or ROOT
    days = (end - start).days + 1

    log = read_reply_log(root / "logs" / "auto_replies.jsonl")
    pairs = read_context(root / "data" / "context")

    st = Stats(start=start, end=end, days=days)
    st.pending_now = read_pending_count(root / "data" / "state" / "pending_queue.json")
    st.unanswered_top = read_unanswered(root / "data" / "unanswered.json", unanswered_top)

    # 趋势桶：1 天 → 按小时；其余 → 按天
    hourly = days == 1
    buckets: dict[str, DayBucket] = {}
    if hourly:
        for h in range(24):
            buckets[f"{h:02d}:00"] = DayBucket(label=f"{h:02d}:00")
    else:
        for i in range(days):
            d = start + timedelta(days=i)
            buckets[d.strftime("%m-%d")] = DayBucket(label=d.strftime("%m-%d"))

    customers: set[str] = set()
    for row in log:
        day = row["ts"].date()
        if not (start <= day <= end):
            continue
        src = row["source"]
        st.by_source[src] += 1
        if src in REPLY_SOURCES or src in HUMAN_SOURCES:
            customers.add(row["customer"])
        if src in GREETING_SOURCES:
            st.greeting += 1
        elif src in HUMAN_SOURCES:
            st.human += 1
        elif src == "no_reply":
            st.no_reply += 1
        else:
            st.replies += 1
            if src == "auto":
                st.auto += 1
            elif src == "hold":
                st.hold += 1
        st.by_path[classify_path(row)] += 1
        if row["message"]:
            st.sample_messages.setdefault(classify_path(row), [])
            if len(st.sample_messages[classify_path(row)]) < 50:
                st.sample_messages[classify_path(row)].append(row["message"])
        key = (f"{row['ts'].hour:02d}:00" if hourly
               else row["ts"].strftime("%m-%d"))
        b = buckets.get(key)
        if b is not None:
            if src == "auto":
                b.auto += 1
                b.replies += 1
            elif src == "hold":
                b.hold += 1
                b.replies += 1
            elif src in HUMAN_SOURCES:
                b.human += 1
            elif src == "no_reply":
                b.no_reply += 1
        if hourly:
            st.by_hour[row["ts"].hour] += 1

    st.customers = len(customers)
    st.buckets = list(buckets.values())

    # 响应时长：从会话配对里取（只算落在区间内的）
    secs = [p["seconds"] for p in pairs
            if p.get("seconds") is not None and p["seconds"] >= 0
            and start <= p["asked_at"].date() <= end]
    if secs:
        st.median_reply_seconds = round(statistics.median(secs), 1)
    return st


def today_stats(root: Path | None = None) -> Stats:
    d = date.today()
    return aggregate(d, d, root=root)


def last_days(n: int, root: Path | None = None) -> Stats:
    end = date.today()
    return aggregate(end - timedelta(days=n - 1), end, root=root)
