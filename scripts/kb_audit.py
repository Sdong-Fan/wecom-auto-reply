# -*- coding: utf-8 -*-
"""资料库体检：问法本身就被规则拦下的有多少条？

为什么需要这个：客户反馈"资料库里的内容照着改一两个字就被转人工"。
但"转人工"有两种完全不同的原因 ——
  ① 资料里没有 / 检索分低   → 该补资料（内容问题）
  ② 规则层在检索之前就返回了 → 补多少资料都没用（管道问题）
这个脚本把 ② 单独数出来，顺带回答"资料库内容到底有没有用"。

用法：
    python scripts/kb_audit.py            # 体检（默认读 data/qdrant）
    python scripts/kb_audit.py --path X   # 指定别的 qdrant 目录

输出：被拦下的问题清单 + 原因分布。0 条 = 每条资料都有机会被检索到。
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def load_questions(qdrant_dir: Path) -> list[str]:
    """从 qdrant 里取出所有 "客户问题: X\\n销售回答: Y" 的 X，去重保序。"""
    from qdrant_client import QdrantClient

    # ★ 复制一份再读：运行中的主程序会锁住 data/qdrant
    tmp = Path(tempfile.mkdtemp()) / "qd"
    shutil.copytree(qdrant_dir, tmp, ignore=shutil.ignore_patterns(".lock"))
    client = QdrantClient(path=str(tmp))
    try:
        points, _ = client.scroll(collection_name="knowledge_base", limit=5000,
                                 with_payload=True)
        qs: list[str] = []
        for p in points:
            text = (p.payload or {}).get("text", "")
            if text.startswith("客户问题: "):
                q = text.split("\n", 1)[0][len("客户问题: "):].strip()
                if q and q not in qs:
                    qs.append(q)
        return qs
    finally:
        client.close()


def audit(questions: list[str]) -> list[tuple[str, list[str]]]:
    from rag.guard import must_escalate, out_of_scope
    from rag.judge import is_low_information, should_reply

    blocked = []
    for q in questions:
        reasons = []
        if is_low_information(q):
            reasons.append("低信息量")
        ok, why = should_reply(q)
        if not ok:
            reasons.append("不必回(%s)" % why)
        scope = out_of_scope(q)
        if scope:
            reasons.append("越界(%s)" % scope)
        esc = must_escalate(q)
        if esc:
            reasons.append("必须转人工(%s)" % esc)
        if reasons:
            blocked.append((q, reasons))
    return blocked


def main() -> int:
    ap = argparse.ArgumentParser(description="资料库规则层体检")
    ap.add_argument("--path", default=str(ROOT / "data" / "qdrant"),
                    help="qdrant 数据目录（默认 data/qdrant）")
    args = ap.parse_args()

    qdrant_dir = Path(args.path)
    if not qdrant_dir.exists():
        print("找不到 qdrant 目录：%s" % qdrant_dir)
        return 2

    qs = load_questions(qdrant_dir)
    blocked = audit(qs)

    print("资料库唯一问题 %d 条" % len(qs))
    print("=== 问法本身就被规则拦下（检索之前就返回）：%d 条 ===" % len(blocked))
    for q, reasons in blocked:
        print("  %-28s <- %s" % (q[:26], " / ".join(reasons)))

    dist = Counter(r.split("(")[0] for _, rs in blocked for r in rs)
    print("\n原因分布：%s" % dict(dist))
    if blocked:
        print("\n这些问法补多少资料都没用 —— 要么改规则，要么这问题本来就该转人工。")
    else:
        print("\n每条资料的问法都能走到检索。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
