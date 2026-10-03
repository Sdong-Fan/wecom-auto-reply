# scripts/collect_test_results.py
"""结果回收器：读程序自己落的盘，判定 200 条测试消息每条走了哪条路。

**为什么不跑离线评测**：离线跑要 200 次 LLM 调用（花 token）。真机测试更真实、也更省钱：
你（或你的发送脚本）用微信把消息发给企微客服，机器人正常处理，结果已经落在磁盘上；
这个脚本把结果收回来，按 200 条的期望标签出一份体检报告。

数据来源（都是程序运行时自己写的，不需要额外埋点）：
* ``data/context/<联系人>.jsonl``  —— 逐轮会话：客户消息后面跟着客服消息 = **自动答复发出去了**
* ``data/state/pending_queue.json`` —— 待人工队列 = **转人工**（含 guard 判定原因）
* ``logs/auto_replies.jsonl``       —— ``dispatch_level=no_reply`` 的行 = **判定不该回**
* 可选 ``--sent-log``               —— 你脚本自己写的 {"id":..,"message":..,"sent_at":..} 日志，
                                      有它就能精确配对（不用靠文本模糊匹配）

用法：
    python scripts/collect_test_results.py                      # 收全部
    python scripts/collect_test_results.py --since 2026-10-03T16:00
    python scripts/collect_test_results.py --md logs/e2e_report.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.eval_messages_200 import GROUPS, MESSAGES  # noqa: E402

_OK = {"auto": "auto_send", "escalate": "human_handle", "no_reply": "no_reply"}
_PUNCT = re.compile(r"[\s，,。.！!？?~～、：:；;\"'“”‘’（）()\[\]【】]+")


def norm(text: str) -> str:
    return _PUNCT.sub("", (text or "").lower())


def _ts(value) -> float:
    """把各种时间写法统一成 epoch 秒（不认识就返回 0）。"""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s[:len(fmt) + 6], fmt).timestamp()
        except Exception:
            continue
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except Exception:
        return 0.0


def load_context(since: float = 0.0) -> list[dict]:
    """从 data/context/*.jsonl 还原（客户消息 → 有没有客服回复）。"""
    turns = []
    for f in sorted((ROOT / "data" / "context").glob("*.jsonl")):
        rows = []
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
        for i, r in enumerate(rows):
            role = r.get("role") or r.get("type") or ""
            if "customer" not in role and "user" not in role:
                continue
            ts = _ts(r.get("timestamp") or r.get("ts") or r.get("time"))
            if ts and ts < since:
                continue
            text = r.get("text") or r.get("content") or r.get("message") or ""
            reply, reply_ts = "", 0.0
            if i + 1 < len(rows):
                nxt = rows[i + 1]
                nrole = nxt.get("role") or nxt.get("type") or ""
                if "assistant" in nrole or "bot" in nrole:
                    reply = nxt.get("text") or nxt.get("content") or ""
                    reply_ts = _ts(nxt.get("timestamp") or nxt.get("ts"))
            turns.append({"contact": f.stem, "ts": ts, "message": text,
                          "reply": reply, "reply_ts": reply_ts})
    return turns


def load_pending(since: float = 0.0) -> list[dict]:
    p = ROOT / "data" / "state" / "pending_queue.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    for item in (data.get("items") or {}).values():
        ts = _ts(item.get("timestamp"))
        if ts and ts < since:
            continue
        out.append({"ts": ts, "message": item.get("customer_message", ""),
                    "guard": item.get("guard_decision", ""),
                    "status": item.get("status", "")})
    return out


def load_no_reply(since: float = 0.0) -> list[dict]:
    p = ROOT / "logs" / "auto_replies.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("dispatch_level") != "no_reply":
            continue
        ts = _ts(r.get("timestamp"))
        if ts and ts < since:
            continue
        out.append({"ts": ts, "message": r.get("customer_message", "")})
    return out


def load_sent_log(path: Path) -> dict:
    """发送脚本的日志：{"id":..., "message":..., "sent_at":...} → {norm(message): [rows]}"""
    out = {}
    if not path or not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        out.setdefault(norm(r.get("message", "")), []).append(r)
    return out


def collect(since: float = 0.0, sent_log: Path | None = None) -> dict:
    turns = load_context(since)
    pending = load_pending(since)
    noreply = load_no_reply(since)
    sent = load_sent_log(sent_log) if sent_log else {}

    by_msg_turns: dict[str, list] = {}
    for t in turns:
        by_msg_turns.setdefault(norm(t["message"]), []).append(t)
    by_msg_pending: dict[str, list] = {}
    for p in pending:
        by_msg_pending.setdefault(norm(p["message"]), []).append(p)
    by_msg_noreply: dict[str, list] = {}
    for n in noreply:
        by_msg_noreply.setdefault(norm(n["message"]), []).append(n)

    rows = []
    for qid, cat, msg, exp, ref in MESSAGES:
        key = norm(msg)
        ts = by_msg_turns.get(key) or []
        # 只算"确实发出去了"的：有客服回复轮次
        answered = [t for t in ts if t.get("reply")]
        if answered:
            actual, reply = "auto_send", answered[-1]["reply"]
        elif key in by_msg_pending:
            actual = "human_handle"
            reply = f"（转人工：{by_msg_pending[key][-1].get('guard') or '待人工'}）"
        elif key in by_msg_noreply:
            actual, reply = "no_reply", ""
        elif ts:
            actual, reply = "human_handle", "（无客服回复轮次 → 转人工/占位语）"
        else:
            actual, reply = "missing", ""
        rows.append({
            "id": qid, "cat": cat, "group": GROUPS.get(qid[0], ""),
            "question": msg, "expected": exp, "ref": ref,
            "dispatch_level": actual, "reply": reply,
            "send_hit": (key in sent) if sent else None,
            "count": len(ts),
        })
    return {"rows": rows,
            "sources": {"context_turns": len(turns), "pending": len(pending),
                        "no_reply": len(noreply), "sent_log": len(sent)}}


def report(res: dict, md_out: Path | None = None) -> dict:
    rows = res["rows"]
    missing = [r for r in rows if r["dispatch_level"] == "missing"]
    got = [r for r in rows if r["dispatch_level"] != "missing"]
    total = len(got)
    ok = [r for r in got if _OK.get(r["expected"]) == r["dispatch_level"]]
    danger = [r for r in got if r["expected"] == "escalate"
              and r["dispatch_level"] == "auto_send"]
    silent = [r for r in got if r["expected"] == "auto"
              and r["dispatch_level"] != "auto_send"]
    noisy = [r for r in got if r["expected"] == "no_reply"
             and r["dispatch_level"] != "no_reply"]
    dup = [r for r in got if r["count"] > 1]

    lines = ["# 200 条·真机测试结果回收", "",
             f"已判定的：**{total}/200**（没抓到的 {len(missing)} 条见文末）",
             f"数据来源：{res['sources']}", ""]
    if total:
        lines += [f"* 行为符合期望 **{len(ok)}/{total}"
                  f"（{len(ok)/total*100:.1f}%）**",
                  f"* 危险直发：**{len(danger)}**  {[r['id'] for r in danger]}",
                  f"* 该答没答：**{len(silent)}**  {[r['id'] for r in silent]}",
                  f"* 不该回却回了：**{len(noisy)}**  {[r['id'] for r in noisy]}",
                  f"* 收到重复消息的（脚本可能重发）：{[r['id'] for r in dup]}", ""]
    groups = {}
    for r in got:
        groups.setdefault(r["group"], []).append(r)
    lines += ["## 按大类", "", "| 大类 | 条数 | 符合 | 符合率 | 问题题号 |",
              "| :--- | ---: | ---: | ---: | :--- |"]
    for g, rs in sorted(groups.items()):
        good = sum(1 for r in rs if _OK.get(r["expected"]) == r["dispatch_level"])
        bad = " ".join(r["id"] for r in rs
                       if _OK.get(r["expected"]) != r["dispatch_level"])
        lines.append(f"| {g} | {len(rs)} | {good} | {good/len(rs)*100:.0f}% | {bad or '—'} |")

    for title, rs in (("危险直发（最严重）", danger), ("该答没答", silent),
                      ("不该回却回了", noisy)):
        lines += ["", f"## {title}", ""]
        if not rs:
            lines.append("无。")
            continue
        for r in rs:
            lines.append(f"* **{r['id']}** [{r['cat']}] 问：{r['question']}")
            lines.append(f"  * 期望 `{r['expected']}` → 实际 `{r['dispatch_level']}`；"
                         f"回复：{(r['reply'] or '')[:120]}")
    lines += ["", "## 没抓到的消息（发送脚本漏发 / 文本对不上）", ""]
    lines.append(" ".join(r["id"] for r in missing) or "无。")

    md = "\n".join(lines) + "\n"
    if md_out:
        md_out.parent.mkdir(parents=True, exist_ok=True)
        md_out.write_text(md, encoding="utf-8")
    print(md)
    if md_out:
        print(f"报告已写入 {md_out}")
    return {"total": total, "ok": len(ok), "danger": len(danger),
            "missing": len(missing), "md": md}


def main():
    ap = argparse.ArgumentParser(description="回收 200 条真机测试结果（不调模型）")
    ap.add_argument("--since", default=None,
                    help="只看这个时间之后的消息，如 2026-10-03T16:00")
    ap.add_argument("--sent-log", default=None,
                    help="发送脚本的 jsonl 日志（含 id/message/sent_at）")
    ap.add_argument("--md", default=None, help="报告写入路径")
    a = ap.parse_args()
    since = _ts(a.since) if a.since else 0.0
    res = collect(since=since, sent_log=Path(a.sent_log) if a.sent_log else None)
    report(res, Path(a.md) if a.md else None)


if __name__ == "__main__":
    main()
