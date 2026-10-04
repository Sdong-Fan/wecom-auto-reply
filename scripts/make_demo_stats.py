# scripts/make_demo_stats.py
"""把**离线评测结果**回放成看板的示例数据（不碰真实日志、不花 token）。

为什么需要它：
* 新用户第一次打开看板是空的 —— 看不到价值，也不知道各块在说什么；
* 三批各 200 条测试消息（共 600 条）我们已经跑过，结果就在 `logs/eval_*.json` 里，
  把它们"摊"成一条条带时间戳的接待记录，就能演示出一版**真实分布的看板**。

产物（结构与程序真实落盘完全一致，所以看板**不用改一行读取逻辑**）：
    data/demo_stats/logs/auto_replies.jsonl         决策记录（含来源/护栏/路径）
    data/demo_stats/data/context/<客户>.jsonl       会话配对（算中位响应用）
    data/demo_stats/data/state/pending_queue.json   待人工积压
    data/demo_stats/data/unanswered.json            常被转人工的问题
    data/demo_stats/说明.json                       这份示例数据是哪来的（可追溯）

用法：
    python scripts/make_demo_stats.py                    # 默认 14 天、600 条
    python scripts/make_demo_stats.py --days 7 --seed 7
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rag.guard import decision_path_of  # noqa: E402  轻量模块，不会拖进 torch

# 三批评测结果（第一批 200 + 第二批 200 + 第三批 200）
DEFAULT_SOURCES = (
    "logs/eval_200a_final.json",
    "logs/eval_200b_final.json",
    "logs/eval_200c_v3_th0.50.json",
)
OUT_DIR = ROOT / "data" / "demo_stats"

# 虚构客户名（**绝不用真实客户名**，示例数据是给别人看的）
CUSTOMERS = ("小陈@微信", "阿伟@微信", "小林@微信", "张女士@微信", "王工@微信",
             "老李@微信", "阿杰@微信", "周先生@微信", "小雅@微信", "刘经理@微信")

# 一天的忙闲曲线（9~22 点，中午与傍晚两个高峰）——让趋势图看得出"人味"
HOUR_WEIGHTS = {9: 3, 10: 5, 11: 6, 12: 3, 13: 4, 14: 6, 15: 7, 16: 8,
                17: 6, 18: 5, 19: 4, 20: 3, 21: 2, 22: 1}


def load_rows(paths: list[str]) -> list[dict]:
    rows: list[dict] = []
    for p in paths:
        f = ROOT / p
        if not f.exists():
            print(f"  [跳过] 找不到 {p}")
            continue
        data = json.loads(f.read_text(encoding="utf-8"))
        for r in data.get("rows", []):
            r["_src"] = f.name
            rows.append(r)
        print(f"  [读取] {p} → {len(data.get('rows', []))} 条")
    return rows


def source_of(row: dict) -> tuple[str, str]:
    """(source, dispatch_level)：把评测口径翻译成运行时日志口径。

    * auto_send  → source=auto（机器人真答，含固定模板婉拒）
    * human_handle → source=hold（转人工时那句占位语确实发给客户了）
    * no_reply   → source=no_reply（判定不该回）
    """
    level = row.get("dispatch_level") or ""
    if level == "auto_send":
        return "auto", "auto_send"
    if level == "no_reply":
        return "no_reply", "no_reply"
    return "hold", "human_handle"


def pick_time(rng: random.Random, days: int) -> datetime:
    """在最近 days 天里按忙闲曲线随机取一个时间点。"""
    day = datetime.now() - timedelta(days=rng.randint(0, days - 1))
    hour = rng.choices(list(HOUR_WEIGHTS), weights=list(HOUR_WEIGHTS.values()))[0]
    return day.replace(hour=hour, minute=rng.randint(0, 59),
                       second=rng.randint(0, 59), microsecond=0)


def build(rows: list[dict], *, days: int, seed: int, repeat: float = 0.22) -> dict:
    rng = random.Random(seed)
    log_lines: list[dict] = []
    context: dict[str, list] = {}
    unanswered: dict[str, dict] = {}
    pending: dict[str, dict] = {}

    # ★ 真实店里同一个问题会被反复问（"有没有货""能不能便宜点"）。
    #   而评测集 600 条互不重复，直接回放会让"我该补什么"全是次数 1，
    #   看不出这份清单的价值。所以抽一批"高频问题"，让后面的记录有概率重复问它
    #   （只改问题的文本，不改判定结果 —— 记录总数仍是 600，见 说明.json）。
    popular = [r.get("question") for r in rows
               if (r.get("dispatch_level") == "human_handle")
               and r.get("question")][:40]
    rng.shuffle(popular)
    popular = popular[:12]

    for idx, row in enumerate(rows):
        src, level = source_of(row)
        ts = pick_time(rng, days)
        customer = rng.choice(CUSTOMERS)
        question = row.get("question") or ""
        if popular and rng.random() < repeat:
            question = rng.choice(popular)
        reply = ""
        if level == "auto_send":
            reply = row.get("reply") or ""
        elif level == "human_handle":
            reply = "稍等一下哈，这个我帮您确认一下具体情况，确认好马上回复您～"
        path = decision_path_of(row.get("guard_decision") or "",
                                row.get("guard_reason") or "",
                                level, row.get("reason") or "")
        log_lines.append({
            "timestamp": ts.isoformat(),
            "customer_name": customer,
            "customer_message": question[:500],
            "ai_reply": reply[:500],
            "confidence": float(row.get("retrieval_score") or 0),
            "dispatch_level": level,
            "guard_decision": row.get("guard_decision") or "",
            "guard_reason": row.get("guard_reason") or "",
            "decision_path": path,
            "source": src,
            "generated": True,
            "sent": True,
            "escalated": level == "human_handle",
            "_demo_from": row.get("_src", ""),
            "_demo_id": row.get("id", ""),
        })
        # 会话配对：客户问 → 客服答（响应时长 0.8~6 秒，跟真实量级接近）
        if level in ("auto_send", "human_handle"):
            gap = rng.uniform(0.8, 6.0)
            context.setdefault(customer, []).append({
                "customer": customer, "message": question,
                "asked_at": ts, "replied_at": ts + timedelta(seconds=gap)})
        # 常被转人工的问题（"我该补什么"）
        if level == "human_handle" and question:
            item = unanswered.setdefault(question, {
                "question": question, "count": 0,
                "reason": row.get("guard_reason") or row.get("reason") or "",
                "name": customer, "first_ts": ts.timestamp(), "last_ts": ts.timestamp()})
            item["count"] += 1
            item["last_ts"] = max(item["last_ts"], ts.timestamp())

    log_lines.sort(key=lambda r: r["timestamp"])
    # 待人工：取最后 8 条转人工的，模拟"当前积压"
    holds = [r for r in log_lines if r["source"] == "hold"][-8:]
    for i, r in enumerate(holds):
        key = f"{r['customer_name']}::demo{i}"
        pending[key] = {"timestamp": datetime.fromisoformat(r["timestamp"]).timestamp(),
                        "customer_name": r["customer_name"],
                        "customer_message": r["customer_message"],
                        "ai_reply": r["ai_reply"], "guard_decision": r["guard_decision"],
                        "status": "pending", "key": key,
                        "person": r["customer_name"], "seq": i}
    return {"log_lines": log_lines, "context": context,
            "unanswered": unanswered, "pending": pending}


def write(out: Path, built: dict, rows: list[dict], days: int) -> None:
    (out / "logs").mkdir(parents=True, exist_ok=True)
    (out / "data" / "context").mkdir(parents=True, exist_ok=True)
    (out / "data" / "state").mkdir(parents=True, exist_ok=True)

    with (out / "logs" / "auto_replies.jsonl").open("w", encoding="utf-8") as f:
        for r in built["log_lines"]:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    for customer, pairs in built["context"].items():
        safe = customer.replace("/", "_").replace("\\", "_")
        with (out / "data" / "context" / f"{safe}.jsonl").open("w", encoding="utf-8") as f:
            for p in pairs:
                f.write(json.dumps({"role": "customer", "text": p["message"],
                                    "timestamp": p["asked_at"].isoformat()},
                                   ensure_ascii=False) + "\n")
                f.write(json.dumps({"role": "assistant", "text": "（回复）",
                                    "timestamp": p["replied_at"].isoformat()},
                                   ensure_ascii=False) + "\n")

    (out / "data" / "unanswered.json").write_text(
        json.dumps({"items": built["unanswered"]}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    (out / "data" / "state" / "pending_queue.json").write_text(
        json.dumps({"items": built["pending"], "cleaned_counts": {}}, ensure_ascii=False),
        encoding="utf-8")
    (out / "说明.json").write_text(json.dumps({
        "这是什么": "运营看板的示例数据：把三批离线评测（各 200 条）的结果回放成接待记录",
        "不是真实客户数据": True,
        "来源文件": sorted({r.get("_src", "") for r in rows}),
        "条数": len(built["log_lines"]),
        "覆盖天数": days,
        "生成时间": datetime.now().isoformat(timespec="seconds"),
        "客户名": "全部为虚构（小陈/阿伟/…），不含任何真实客户信息",
        "怎么来的": "scripts/make_demo_stats.py",
    }, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="生成看板示例数据（回放离线评测结果）")
    ap.add_argument("--files", nargs="*", default=list(DEFAULT_SOURCES))
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default=str(OUT_DIR))
    a = ap.parse_args()

    print("读取评测结果：")
    rows = load_rows(a.files)
    if not rows:
        print("没有可用的评测结果，退出")
        return
    built = build(rows, days=a.days, seed=a.seed)
    out = Path(a.out)
    write(out, built, rows, a.days)

    n = len(built["log_lines"])
    auto = sum(1 for r in built["log_lines"] if r["source"] == "auto")
    hold = sum(1 for r in built["log_lines"] if r["source"] == "hold")
    noreply = sum(1 for r in built["log_lines"] if r["source"] == "no_reply")
    print(f"\n已生成 {n} 条示例记录 → {out}")
    print(f"  auto(真答)={auto}  hold(转人工)={hold}  no_reply={noreply}")
    print(f"  会话配对={sum(len(v) for v in built['context'].values())} "
          f"转人工问题={len(built['unanswered'])} 待人工={len(built['pending'])}")


if __name__ == "__main__":
    main()
