# eval/reporter.py
"""Generate human-readable eval report from scored results."""

from datetime import datetime
from typing import List
from collections import defaultdict


def _format_section_summary(results: List[dict]) -> str:
    """Build per-category summary."""
    cats = defaultdict(lambda: {"total": 0, "passed": 0, "hallucinations": 0})

    for r in results:
        cat = r.get("category", "未分类")
        cats[cat]["total"] += 1
        total_score = r["scores"]["total"]
        if total_score >= 9:
            cats[cat]["passed"] += 1
        if r["scores"]["has_hallucination"]:
            cats[cat]["hallucinations"] += 1

    lines = []
    for cat in sorted(cats.keys()):
        d = cats[cat]
        bar_len = max(1, int(d["passed"] / max(d["total"], 1) * 10))
        bar = "█" * bar_len + "░" * (10 - bar_len)
        lines.append(
            f"{cat:<10} {bar}  {d['passed']}/{d['total']} 通过  "
            f"{d['hallucinations']} 幻觉"
        )
    return "\n".join(lines)


def _format_detail(results: List[dict]) -> str:
    """Build per-QA detail lines."""
    lines = []
    for r in results:
        total = r["scores"]["total"]
        if total >= 12:
            icon = "✅"
        elif total >= 9:
            icon = "🔄"
        else:
            icon = "🚫"

        guard = r.get("guard_decision", "?")
        halluc = " 幻觉" if r["scores"]["has_hallucination"] else ""

        lines.append(
            f"{icon} {r['qa_id']} \"{r['question'][:50]}\" — {total}/15 {guard}{halluc}"
        )

        if r["scores"]["has_hallucination"] and r["scores"]["hallucination_detail"]:
            lines.append(f"   幻觉: {r['scores']['hallucination_detail'][:120]}")

        if guard == "block" and total >= 12:
            lines.append(f"   guard误杀: 回复评分{total}但被拦截")
        elif guard == "pass" and total < 9 and total > 0:
            lines.append(f"   guard漏放: 回复评分{total}但通过检查")

        if r["scores"]["parse_error"]:
            detail = r["scores"].get("hallucination_detail", "")
            lines.append(f"   管道错误: {detail[:150]}")

    return "\n".join(lines)


def format_report(results: List[dict],
                   ocr_noise_results: List[dict] | None = None) -> str:
    """Generate the full eval report as a string."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    total = len(results)
    passed = sum(1 for r in results if r["scores"]["total"] >= 9)
    hallucinations = sum(1 for r in results if r["scores"]["has_hallucination"])

    guard_miskill = sum(
        1 for r in results
        if r.get("guard_decision") == "block" and r["scores"]["total"] >= 12
    )
    guard_miss = sum(
        1 for r in results
        if r.get("guard_decision") == "pass" and r["scores"]["total"] < 9
        and r["scores"]["total"] > 0
    )

    avg_score = sum(r["scores"]["total"] for r in results) / max(total, 1)

    report = f"""═══════════════════════════════════════════
Eval Report — {now}
总 QA 数: {total} | 知识库文件: 5
═══════════════════════════════════════════

📊 总览
───────────────────────────────────────────
通过率 (总分≥9/15):    {passed}/{total}  ({passed*100//total}%)
平均评分:               {avg_score:.1f}/15
幻觉率 (编造事实):      {hallucinations}/{total}  ({hallucinations*100//total}%)
Guard 误杀 (好回复被拦): {guard_miskill}
Guard 漏放 (差回复通过): {guard_miss}

📂 按分类
───────────────────────────────────────────
{_format_section_summary(results)}

🔍 问题详情
───────────────────────────────────────────
{_format_detail(results)}
"""

    if ocr_noise_results:
        ocr_avg = (sum(r["scores"]["total"] for r in ocr_noise_results)
                   / max(len(ocr_noise_results), 1))
        noise_ids = {n["qa_id"] for n in ocr_noise_results}
        orig_matches = [r for r in results if r["qa_id"] in noise_ids]
        orig_avg = (sum(r["scores"]["total"] for r in orig_matches)
                    / max(len(orig_matches), 1))
        downgraded = sum(
            1 for r in ocr_noise_results
            if r["scores"]["total"] < 9
        )
        report += f"""
📝 OCR 噪声影响
───────────────────────────────────────────
原始       平均 {orig_avg:.1f}/15
加噪声后   平均 {ocr_avg:.1f}/15  (↓ {orig_avg - ocr_avg:.1f})
{len(ocr_noise_results)} 条中 {downgraded} 条降级为"不可通过"
"""

    return report
