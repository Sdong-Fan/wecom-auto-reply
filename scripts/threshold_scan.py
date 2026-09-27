# scripts/threshold_scan.py
"""阈值扫描：同一套 46 题，把"自动发门槛"换几个值各跑一遍，用数据选平衡点。

**为什么要它**：门槛调低 → 更多问题自动答（省人力），但"该转人工的也发出去了"
的风险变大；调高 → 安全但白转人工多。
这个取舍不该拍脑袋，让数据说话。

用法：
    python scripts/threshold_scan.py                    # 默认 0.50/0.55/0.60/0.65
    python scripts/threshold_scan.py --values 0.55 0.65
    python scripts/threshold_scan.py --limit 20         # 少跑几题（快）

输出：`logs/threshold_scan.json` + 一张对比表（危险直发必须为 0 才算能用）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.eval_set import run          # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="阈值扫描")
    ap.add_argument("--values", nargs="*", type=float,
                    default=[0.50, 0.55, 0.60, 0.65])
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()

    results = []
    for th in a.values:
        print(f"\n{'=' * 60}\n门槛 = {th}（自动发要求检索分 > {th}）\n{'=' * 60}")
        r = run(limit=a.limit, out=ROOT / "logs" / f"eval_th{th}.json",
                high=th, quiet=True)
        s = r["summary"]
        results.append({"threshold": th, **s, "rows": r["rows"]})
        print(f"  自动发出 {s['auto_sent']}/{s['total']} | 行为符合 {s['behaviour_ok']}"
              f"/{s['total']} | 危险直发 {len(s['dangerous_send'])} "
              f"{s['dangerous_send']} | 该答没答 {len(s['answered_nothing'])} "
              f"{s['answered_nothing']}")

    print(f"\n{'=' * 78}")
    print(f"{'门槛':<8}{'自动发':<8}{'行为符合':<10}{'危险直发':<10}{'该答没答':<10}结论")
    print("-" * 78)
    best = None
    for x in results:
        danger = len(x["dangerous_send"])
        ok = x["behaviour_ok"]
        verdict = "危险，不能用" if danger else ("推荐" if not best else "")
        if not danger and (best is None or ok > best["behaviour_ok"]):
            best = x
            verdict = "推荐"
        print(f"{x['threshold']:<8}{x['auto_sent']:<8}{ok:<10}{danger:<10}"
              f"{len(x['answered_nothing']):<10}{verdict}")
    print("-" * 78)
    if best:
        print(f"→ 数据推荐门槛 {best['threshold']}：危险直发 {len(best['dangerous_send'])}，"
              f"行为符合 {best['behaviour_ok']}/{best['total']}，"
              f"自动发出 {best['auto_sent']}/{best['total']}")
        print(f"  另外记住：危险直发那几道题的 id 要单独看一眼，"
              f"它们比总分重要（安全 > 效率）")
    else:
        print("→ 每个门槛都有危险直发 —— 先修危险项，再谈门槛")

    dest = ROOT / "logs" / "threshold_scan.json"
    dest.write_text(json.dumps(
        [{k: v for k, v in x.items() if k != "rows"} for x in results],
        ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n明细已写入 {dest}")


if __name__ == "__main__":
    main()
