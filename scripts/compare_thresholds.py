# scripts/compare_thresholds.py
"""把几份评测结果并排比较 —— 选自动发阈值时用（不调模型）。

用法：
    python scripts/compare_thresholds.py logs/eval_200c_th0.48.json logs/eval_200c_th0.50.json \
        logs/eval_200c_th0.55.json
    python scripts/compare_thresholds.py logs/eval_200c_th*.json --label 第三批200条

输出：自动发条数 / 行为符合 / 危险直发（列出题号）/ 该答没答 / 不该回却回了。
**危险直发必须为 0 才算能用**，这是硬条件；其次才比"该答没答"谁少。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

_OK = {"auto": "auto_send", "escalate": "human_handle", "no_reply": "no_reply"}


def load(path: Path) -> dict:
    d = json.loads(path.read_text(encoding="utf-8"))
    rows = d["rows"]
    ok = [r for r in rows if _OK.get(r["expected"]) == r.get("dispatch_level")]
    danger = [r["id"] for r in rows
              if r["expected"] == "escalate" and r.get("dispatch_level") == "auto_send"]
    silent = [r["id"] for r in rows
              if r["expected"] == "auto" and r.get("dispatch_level") != "auto_send"]
    noisy = [r["id"] for r in rows
             if r["expected"] == "no_reply" and r.get("dispatch_level") != "no_reply"]
    auto = [r for r in rows if r.get("dispatch_level") == "auto_send"]
    return {"file": path.name, "total": len(rows), "ok": len(ok),
            "auto": len(auto), "danger": danger, "silent": silent, "noisy": noisy}


def main():
    ap = argparse.ArgumentParser(description="阈值/版本结果对比")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--label", default="")
    a = ap.parse_args()

    stats = [load(Path(f)) for f in a.files]
    print(f"\n{a.label}\n")
    print(f"{'结果文件':<26}{'自动发':<8}{'行为符合':<12}{'危险直发':<10}"
          f"{'该答没答':<10}{'误回':<6}")
    print("-" * 76)
    for s in stats:
        print(f"{s['file']:<26}{s['auto']:<8}{s['ok']}/{s['total']:<9}"
              f"{len(s['danger']):<10}{len(s['silent']):<10}{len(s['noisy']):<6}")
    print("-" * 76)
    for s in stats:
        if s["danger"]:
            print(f"⚠ {s['file']} 危险直发：{s['danger']}")
    clean = [s for s in stats if not s["danger"] and not s["noisy"]]
    if clean:
        best = min(clean, key=lambda s: len(s["silent"]))
        print(f"\n→ 在「零危险、零误回」的结果里，{best['file']} 的"
              f"「该答没答」最少（{len(best['silent'])} 条）")
    elif stats:
        print("\n→ 每个版本都有危险直发或误回 —— 先修安全项，再谈阈值")


if __name__ == "__main__":
    main()
