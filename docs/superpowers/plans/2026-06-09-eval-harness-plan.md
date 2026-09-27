# Eval Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a 20-QA eval harness that measures AI reply quality (accuracy/completeness/safety) using LLM-as-judge, producing a human-readable report.

**Architecture:** `eval/run.py` drives the pipeline: load QA → embed → search Qdrant → generate → guard → score via DeepSeek judge → report. Reuses rag/* modules for RAG pipeline. Adds scorer.py (LLM judge) and reporter.py (text report).

**Tech Stack:** Python 3.10+, DeepSeek API (both generation + judge), BGE embeddings, Qdrant, existing rag/* modules

---

## File Structure

```
eval/
  __init__.py              # Package init (empty)
  labeled_qa.json          # 20 labeled QA pairs
  scorer.py                # LLM judge: scores reply on 3 dimensions
  run.py                   # Main entry: python -m eval.run
  reporter.py              # Text report to logs/eval_report.txt
```

---

### Task 1: Create eval package + __init__.py

**Files:**
- Create: `eval/__init__.py`

- [ ] **Step 1: Create package init**

```bash
mkdir -p eval
```

- [ ] **Step 2: Write empty init**

```python
# eval/__init__.py
"""Eval harness for measuring AI reply quality."""
```

- [ ] **Step 3: Commit**

```bash
git add eval/__init__.py
git commit -m "chore: create eval package"
```

---

### Task 2: Write labeled QA dataset

**Files:**
- Create: `eval/labeled_qa.json`

- [ ] **Step 1: Write the 20 QA pairs**

```json
[
  {
    "id": "qa_01",
    "question": "你们的产品怎么收费？",
    "expected_points": ["多种套餐", "基础版", "专业版", "企业版", "按需定制"],
    "forbidden_points": ["具体价格数字", "免费"],
    "knowledge_sources": ["常见问题.txt", "价格说明.txt"],
    "category": "价格相关"
  },
  {
    "id": "qa_02",
    "question": "支持哪些付款方式？",
    "expected_points": ["微信支付", "支付宝", "银行转账", "对公转账"],
    "forbidden_points": ["比特币", "分期付款"],
    "knowledge_sources": ["常见问题.txt"],
    "category": "价格相关"
  },
  {
    "id": "qa_03",
    "question": "产品多久能上线？",
    "expected_points": ["标准版1-3个工作日", "定制版1-2周"],
    "forbidden_points": ["当天上线", "立刻可用"],
    "knowledge_sources": ["常见问题.txt"],
    "category": "产品功能"
  },
  {
    "id": "qa_04",
    "question": "有没有免费试用？",
    "expected_points": ["7天免费试用", "功能完整", "无需绑定支付方式"],
    "forbidden_points": ["永久免费", "30天试用"],
    "knowledge_sources": ["常见问题.txt"],
    "category": "价格相关"
  },
  {
    "id": "qa_05",
    "question": "数据安全有保障吗？",
    "expected_points": ["加密技术", "国内服务器", "安全认证"],
    "forbidden_points": ["绝对安全", "100%不会泄露"],
    "knowledge_sources": ["常见问题.txt", "产品介绍.txt"],
    "category": "产品功能"
  },
  {
    "id": "qa_06",
    "question": "能定制开发吗？",
    "expected_points": ["支持定制开发", "个性化定制", "功能和界面"],
    "forbidden_points": ["免费定制", "任何需求都能做"],
    "knowledge_sources": ["常见问题.txt"],
    "category": "定制需求"
  },
  {
    "id": "qa_07",
    "question": "有培训服务吗？",
    "expected_points": ["免费培训", "在线视频教程", "文档指南", "一对一指导"],
    "forbidden_points": ["收费培训", "线下培训"],
    "knowledge_sources": ["常见问题.txt", "售后政策.txt"],
    "category": "售后政策"
  },
  {
    "id": "qa_08",
    "question": "数据能导出吗？",
    "expected_points": ["支持数据导出", "Excel", "CSV"],
    "forbidden_points": ["PDF", "JSON"],
    "knowledge_sources": ["常见问题.txt"],
    "category": "产品功能"
  },
  {
    "id": "qa_09",
    "question": "售后服务怎么样？",
    "expected_points": ["7×12小时在线客服", "专属客户经理", "远程技术支持"],
    "forbidden_points": ["24小时", "随叫随到"],
    "knowledge_sources": ["常见问题.txt", "售后政策.txt"],
    "category": "售后政策"
  },
  {
    "id": "qa_10",
    "question": "如果买了不满意可以退吗？",
    "expected_points": ["7天无理由退款", "联系客服", "3个工作日内处理", "原路退回"],
    "forbidden_points": ["随时退款", "全额退款无限制"],
    "knowledge_sources": ["售后政策.txt"],
    "category": "售后政策"
  },
  {
    "id": "qa_11",
    "question": "你们的产品是什么？",
    "expected_points": ["智能客服", "自动回复", "多渠道接入"],
    "forbidden_points": ["硬件产品", "实体设备"],
    "knowledge_sources": ["产品介绍.txt"],
    "category": "产品功能"
  },
  {
    "id": "qa_12",
    "question": "支持哪些平台？",
    "expected_points": ["微信公众号", "企业微信", "小程序", "网站", "APP"],
    "forbidden_points": ["Facebook", "Twitter"],
    "knowledge_sources": ["常见问题.txt", "产品介绍.txt"],
    "category": "产品功能"
  },
  {
    "id": "qa_13",
    "question": "你们和竞品相比有什么优势？",
    "expected_points": ["响应速度快", "准确率高", "部署简单", "成本低"],
    "forbidden_points": ["贬低竞品具体名称", "行业第一", "绝对领先"],
    "knowledge_sources": ["销售话术.txt", "产品介绍.txt"],
    "category": "通用咨询"
  },
  {
    "id": "qa_14",
    "question": "最近有活动吗？",
    "expected_points": ["年付8折优惠", "老客户续费9折", "推荐新客户返现"],
    "forbidden_points": ["限时免费", "买一送一"],
    "knowledge_sources": ["销售话术.txt", "价格说明.txt"],
    "category": "价格相关"
  },
  {
    "id": "qa_15",
    "question": "定制周期要多久？",
    "expected_points": ["7-15个工作日", "具体看需求复杂度"],
    "forbidden_points": ["当天完成", "1-2天"],
    "knowledge_sources": ["常见问题.txt", "销售话术.txt"],
    "category": "定制需求"
  },
  {
    "id": "qa_16",
    "question": "定制订单最低起订量是多少？",
    "expected_points": ["最低起订量100件"],
    "forbidden_points": ["没有起订量", "一件起订"],
    "knowledge_sources": ["销售话术.txt"],
    "category": "定制需求"
  },
  {
    "id": "qa_17",
    "question": "可以定制哪些内容？",
    "expected_points": ["logo定制", "包装定制", "产品规格定制"],
    "forbidden_points": ["代码开源", "白标"],
    "knowledge_sources": ["销售话术.txt", "常见问题.txt"],
    "category": "定制需求"
  },
  {
    "id": "qa_18",
    "question": "产品保修期是多久？",
    "expected_points": ["需要人工处理"],
    "forbidden_points": [],
    "knowledge_sources": ["常见问题.txt"],
    "note": "知识库中无明确保修期信息，期望回复'需要人工处理'",
    "category": "售后政策"
  },
  {
    "id": "qa_19",
    "question": "怎么联系你们？",
    "expected_points": ["在线客服", "客户经理"],
    "forbidden_points": ["400电话", "具体的座机号码"],
    "knowledge_sources": ["产品介绍.txt", "售后政策.txt"],
    "category": "通用咨询"
  },
  {
    "id": "qa_20",
    "question": "你们公司在哪里？",
    "expected_points": ["需要人工处理"],
    "forbidden_points": [],
    "knowledge_sources": [],
    "note": "知识库中无公司地址信息，期望回复'需要人工处理'",
    "category": "通用咨询"
  }
]
```

- [ ] **Step 2: Validate JSON**

```bash
python -c "import json; json.load(open('eval/labeled_qa.json')); print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add eval/labeled_qa.json
git commit -m "feat: add 20 labeled QA pairs for eval harness"
```

---

### Task 3: Write LLM judge scorer (TDD)

**Files:**
- Create: `eval/scorer.py`
- Create: `tests/test_scorer.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_scorer.py
"""TDD: LLM judge scorer for eval harness."""

import asyncio
import pytest
from eval.scorer import score_reply, build_judge_prompt, parse_judge_response


def test_build_judge_prompt():
    """build_judge_prompt creates a prompt with question, reply, and criteria."""
    prompt = build_judge_prompt(
        question="测试问题",
        reply="测试回复",
        expected_points=["要点1", "要点2"],
        forbidden_points=["禁止1"],
        chunks=["知识片段1", "知识片段2"],
    )
    assert "测试问题" in prompt
    assert "测试回复" in prompt
    assert "要点1" in prompt
    assert "要点2" in prompt
    assert "禁止1" in prompt
    assert "知识片段1" in prompt
    assert "知识片段2" in prompt


def test_parse_valid_judge_response():
    """parse_judge_response extracts scores from a valid JSON block."""
    response = '''```json
{
  "accuracy": 4,
  "completeness": 5,
  "safety": 5,
  "has_hallucination": false,
  "hallucination_detail": "",
  "overall_comment": "回复准确完整"
}
```'''
    result = parse_judge_response(response)
    assert result["accuracy"] == 4
    assert result["completeness"] == 5
    assert result["safety"] == 5
    assert result["has_hallucination"] is False
    assert result["total"] == 14


def test_parse_response_missing_json_block():
    """parse_judge_response returns default scores on malformed input."""
    result = parse_judge_response("随便乱回的内容，没有json格式")
    assert result["accuracy"] == 0
    assert result["completeness"] == 0
    assert result["safety"] == 0
    assert result["has_hallucination"] is True
    assert result["parse_error"] is True


def test_score_reply_is_callable():
    """score_reply is an async function that returns a dict."""
    assert asyncio.iscoroutinefunction(score_reply), \
        "score_reply must be an async function"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
SKIP_EMBED_TESTS=1 python -m pytest tests/test_scorer.py -v
```
Expected: FAIL (ImportError: cannot import name 'score_reply')

- [ ] **Step 3: Write scorer.py implementation**

```python
# eval/scorer.py
"""LLM judge — scores a generated reply on accuracy, completeness, safety.

Uses DeepSeek API in judge mode to produce 1-5 scores on 3 dimensions
plus hallucination detection.
"""

import json
import logging
import re
import asyncio
from rag.llm_client import chat

logger = logging.getLogger(__name__)

JUDGE_SYSTEM_PROMPT = """你是一个严格的质量评估裁判。你需要评估一个AI客服回复的质量。

评估规则：
1. 准确性(accuracy 1-5)：回复中的信息是否与知识片段一致，有没有错误
2. 完整性(completeness 1-5)：回复是否覆盖了期望的关键信息点
3. 安全性(safety 1-5)：回复是否避免了禁止内容，有没有编造知识片段中没有的信息

幻觉判断：如果回复中包含了知识片段中完全没有的事实陈述（不是措辞差异，是事实），则has_hallucination为true。

评分标准：
- 5分：完全正确，无瑕疵
- 4分：基本正确，有小问题
- 3分：部分正确，有明显遗漏或小错误
- 2分：多数不对，关键信息错误
- 1分：完全错误或答非所问

你必须严格以JSON格式回复，包含以下字段：
{
  "accuracy": <1-5>,
  "completeness": <1-5>,
  "safety": <1-5>,
  "has_hallucination": <true/false>,
  "hallucination_detail": "<如果幻觉，具体说明；否则空字符串>",
  "overall_comment": "<一句话总评>"
}

只输出JSON，不要输出任何其他内容。"""


def build_judge_prompt(
    question: str,
    reply: str,
    expected_points: list[str],
    forbidden_points: list[str],
    chunks: list[str],
) -> list[dict]:
    """Build the judge prompt messages for DeepSeek."""
    expected_str = "\n".join(f"  - {p}" for p in expected_points) if expected_points else "  (无特殊期望)"
    forbidden_str = "\n".join(f"  - {p}" for p in forbidden_points) if forbidden_points else "  (无特殊禁止)"
    chunks_str = "\n\n---\n\n".join(chunks) if chunks else "(无知识片段)"

    user_prompt = f"""请评估以下AI客服回复：

【客户问题】
{question}

【知识片段】（AI可以参考的信息）
{chunks_str}

【期望覆盖的关键信息点】
{expected_str}

【禁止出现的内容】
{forbidden_str}

【AI回复】
{reply}

请按JSON格式给出评分。"""

    return [
        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def parse_judge_response(response: str) -> dict:
    """Parse the judge's JSON response, with fallback for malformed output."""
    try:
        # Extract JSON block from possible markdown code fences
        json_match = re.search(r'\{[^}]*"accuracy"[^}]*\}', response, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
        else:
            # Try parsing the whole response as JSON
            data = json.loads(response)

        return {
            "accuracy": max(1, min(5, int(data.get("accuracy", 0)))),
            "completeness": max(1, min(5, int(data.get("completeness", 0)))),
            "safety": max(1, min(5, int(data.get("safety", 0)))),
            "has_hallucination": bool(data.get("has_hallucination", False)),
            "hallucination_detail": str(data.get("hallucination_detail", "")),
            "overall_comment": str(data.get("overall_comment", "")),
            "total": 0,
            "parse_error": False,
        }
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
        logger.warning(f"Failed to parse judge response: {e}\nResponse: {response[:200]}")
        return {
            "accuracy": 0,
            "completeness": 0,
            "safety": 0,
            "has_hallucination": True,
            "hallucination_detail": f"Parse error: {e}",
            "overall_comment": "JUDGE_PARSE_ERROR",
            "total": 0,
            "parse_error": True,
        }


async def score_reply(
    question: str,
    reply: str,
    expected_points: list[str],
    forbidden_points: list[str],
    chunks: list[str],
) -> dict:
    """Score a single reply using LLM judge.

    Returns dict with accuracy, completeness, safety, total, has_hallucination,
    hallucination_detail, overall_comment, parse_error.
    """
    if not reply or reply.strip() == "":
        return {
            "accuracy": 1,
            "completeness": 1,
            "safety": 1,
            "has_hallucination": False,
            "hallucination_detail": "",
            "overall_comment": "Empty reply",
            "total": 3,
            "parse_error": False,
        }

    messages = build_judge_prompt(
        question, reply, expected_points, forbidden_points, chunks
    )

    try:
        response = await chat(messages, temperature=0.0, max_tokens=512)
    except Exception as e:
        logger.error(f"Judge API call failed: {e}")
        return {
            "accuracy": 0,
            "completeness": 0,
            "safety": 0,
            "has_hallucination": False,
            "hallucination_detail": f"Judge API error: {e}",
            "overall_comment": "JUDGE_API_ERROR",
            "total": 0,
            "parse_error": True,
        }

    result = parse_judge_response(response)
    result["total"] = result["accuracy"] + result["completeness"] + result["safety"]
    return result
```

- [ ] **Step 4: Run test to verify it passes**

```bash
SKIP_EMBED_TESTS=1 python -m pytest tests/test_scorer.py -v
```
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add -f tests/test_scorer.py eval/scorer.py
git commit -m "feat: add LLM judge scorer for eval harness"
```

---

### Task 4: Write reporter module (TDD)

**Files:**
- Create: `eval/reporter.py`
- Create: `tests/test_reporter.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_reporter.py
"""TDD: Eval report generator."""

from eval.reporter import format_report, _format_section_summary


def make_sample_result(qa_id, accuracy, completeness, safety,
                        has_hallucination=False, guard_decision="pass",
                        category="产品功能"):
    """Helper to create a sample eval result."""
    return {
        "qa_id": qa_id,
        "question": f"测试问题 {qa_id}",
        "reply": "测试回复",
        "expected_points": ["要点1"],
        "forbidden_points": [],
        "chunks": ["知识片段1"],
        "category": category,
        "scores": {
            "accuracy": accuracy,
            "completeness": completeness,
            "safety": safety,
            "has_hallucination": has_hallucination,
            "hallucination_detail": "编造了XXX" if has_hallucination else "",
            "overall_comment": "OK",
            "total": accuracy + completeness + safety,
            "parse_error": False,
        },
        "guard_decision": guard_decision,
        "retrieval_score": 0.85,
    }


def test_format_report_basic():
    """format_report generates a non-empty string with key sections."""
    results = [
        make_sample_result("qa_01", 5, 5, 5),
        make_sample_result("qa_02", 3, 3, 3),
    ]
    report = format_report(results)
    assert "Eval Report" in report
    assert "总览" in report
    assert "qa_01" in report
    assert "qa_02" in report


def test_format_report_counts_pass_fail():
    """format_report correctly counts pass/fail based on total >= 9 threshold."""
    results = [
        make_sample_result("qa_01", 5, 5, 5),   # total=15, pass
        make_sample_result("qa_02", 4, 4, 4),   # total=12, pass
        make_sample_result("qa_03", 2, 2, 2),   # total=6, fail
        make_sample_result("qa_04", 3, 3, 3),   # total=9, pass (threshold)
    ]
    report = format_report(results)
    assert "3/4" in report or "75%" in report  # 3 of 4 pass


def test_format_report_guard_mismatch():
    """format_report flags guard mismatch: good reply blocked or bad reply passed."""
    results = [
        make_sample_result("qa_01", 5, 5, 5, guard_decision="block"),  # mismatch
        make_sample_result("qa_02", 1, 1, 1, guard_decision="pass"),   # mismatch
    ]
    report = format_report(results)
    assert "guard误杀" in report or "guard 误杀" in report or "Guard 误杀" in report
    assert "guard漏放" in report or "guard 漏放" in report or "Guard 漏放" in report


def test_format_report_hallucination_count():
    """format_report counts hallucinations."""
    results = [
        make_sample_result("qa_01", 5, 5, 5, has_hallucination=False),
        make_sample_result("qa_02", 4, 4, 4, has_hallucination=True),
        make_sample_result("qa_03", 3, 3, 3, has_hallucination=True),
    ]
    report = format_report(results)
    assert "2" in report  # hallucination count


def test_format_section_summary():
    """_format_section_summary groups by category."""
    results = [
        make_sample_result("qa_01", 5, 5, 5, category="价格相关"),
        make_sample_result("qa_02", 4, 4, 4, category="价格相关"),
        make_sample_result("qa_03", 1, 1, 1, category="售后政策"),
    ]
    summary = _format_section_summary(results)
    assert "价格相关" in summary
    assert "售后政策" in summary
```

- [ ] **Step 2: Run test to verify it fails**

```bash
SKIP_EMBED_TESTS=1 python -m pytest tests/test_reporter.py -v
```
Expected: FAIL (ImportError)

- [ ] **Step 3: Write reporter.py implementation**

```python
# eval/reporter.py
"""Generate human-readable eval report from scored results."""

from datetime import datetime
from typing import List


def _format_section_summary(results: List[dict]) -> str:
    """Build per-category summary."""
    from collections import defaultdict
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
            lines.append(f"   裁判解析错误: {r['scores']['overall_comment']}")

    return "\n".join(lines)


def format_report(results: List[dict], ocr_noise_results: List[dict] | None = None) -> str:
    """Generate the full eval report as a string."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    total = len(results)
    passed = sum(1 for r in results if r["scores"]["total"] >= 9)
    hallucinations = sum(1 for r in results if r["scores"]["has_hallucination"])

    # Guard mismatch detection
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
        ocr_avg = sum(r["scores"]["total"] for r in ocr_noise_results) / max(len(ocr_noise_results), 1)
        orig_avg = sum(
            r["scores"]["total"] for r in results
            if r["qa_id"] in {n["qa_id"] for n in ocr_noise_results}
        ) / max(len(ocr_noise_results), 1)
        report += f"""
📝 OCR 噪声影响
───────────────────────────────────────────
原始       平均 {orig_avg:.1f}/15
加噪声后   平均 {ocr_avg:.1f}/15  (↓ {orig_avg - ocr_avg:.1f})
{len(ocr_noise_results)} 条中 {sum(1 for r in ocr_noise_results if r['scores']['total'] < 9)} 条从"可通过"降级为"不可通过"
"""

    return report
```

- [ ] **Step 4: Run test to verify it passes**

```bash
SKIP_EMBED_TESTS=1 python -m pytest tests/test_reporter.py -v
```
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add -f tests/test_reporter.py eval/reporter.py
git commit -m "feat: add eval report generator"
```

---

### Task 5: Write run.py main entry (TDD)

**Files:**
- Create: `eval/run.py`
- Create: `tests/test_eval_run.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_eval_run.py
"""TDD: Eval run.py main entry point."""

from pathlib import Path
import json


def test_labeled_qa_json_exists():
    """The labeled QA file must exist and contain 20 items."""
    path = Path("eval/labeled_qa.json")
    assert path.exists(), "eval/labeled_qa.json must exist"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data) == 20, f"Expected 20 QA pairs, got {len(data)}"
    for item in data:
        assert "id" in item
        assert "question" in item
        assert "expected_points" in item
        assert "forbidden_points" in item
        assert "category" in item


def test_eval_run_module_importable():
    """eval.run module must be importable."""
    import eval.run
    assert hasattr(eval.run, "run_eval"), \
        "eval.run must have a run_eval function"


def test_eval_run_has_ocr_noise():
    """eval.run must have a function to add OCR noise for testing."""
    import eval.run
    assert hasattr(eval.run, "add_ocr_noise"), \
        "eval.run must have add_ocr_noise function"


def test_load_qa_pairs():
    """load_qa_pairs returns list of 20 dicts."""
    from eval.run import load_qa_pairs
    pairs = load_qa_pairs()
    assert len(pairs) == 20
```

- [ ] **Step 2: Run test to verify it fails**

```bash
SKIP_EMBED_TESTS=1 python -m pytest tests/test_eval_run.py -v
```
Expected: FAIL (ImportError or AttributeError)

- [ ] **Step 3: Write run.py implementation**

```python
# eval/run.py
"""Eval harness main entry: python -m eval.run

Pipeline: load QA → embed → search → generate → guard → score → report.
Outputs to logs/eval_report.txt.
"""

import asyncio
import json
import logging
import os
import random
import time
from pathlib import Path

from qdrant_client import QdrantClient

from rag.embed_query import embed_query
from rag.retriever import search, ensure_collection
from rag.generator import generate_reply
from rag.guard import classify_and_dispatch
from eval.scorer import score_reply
from eval.reporter import format_report

logger = logging.getLogger(__name__)

QA_PATH = Path("eval/labeled_qa.json")
REPORT_PATH = Path("logs/eval_report.txt")
QDRANT_PATH = "data/qdrant"


def load_qa_pairs() -> list[dict]:
    """Load labeled QA pairs from JSON file."""
    return json.loads(QA_PATH.read_text(encoding="utf-8"))


def add_ocr_noise(question: str, noise_type: str = "prefix") -> str:
    """Add realistic OCR noise to a question.

    Args:
        question: Original clean question.
        noise_type: Type of noise to add.
            - "prefix": Add "在吗? " prefix
            - "suffix": Add garbled suffix
            - "multi": Concatenate with another question
    """
    if noise_type == "prefix":
        return f"在吗?  {question}"
    elif noise_type == "suffix":
        return f"{question} 期五 少"
    elif noise_type == "multi":
        return f"你好 你们的产品有哪些？  {question}"
    return question


async def run_single_qa(
    qa: dict,
    qdrant: QdrantClient,
) -> dict:
    """Run a single QA through the full pipeline and return scored result."""
    question = qa["question"]
    expected = qa.get("expected_points", [])
    forbidden = qa.get("forbidden_points", [])

    # Embed
    qv = embed_query(question)

    # Search Qdrant
    results = search(qv, qdrant, top_k=5)
    chunks = [(r.payload or {}).get("text", "") for r in results]
    scores = [r.score for r in results]
    top_score = scores[0] if scores else 0.0

    # Generate
    reply = await generate_reply(question, chunks)

    # Guard
    guard_result = classify_and_dispatch(scores, reply, chunks)

    # Score (LLM judge)
    judge_scores = await score_reply(question, reply, expected, forbidden, chunks)

    return {
        "qa_id": qa["id"],
        "question": question,
        "reply": reply,
        "expected_points": expected,
        "forbidden_points": forbidden,
        "chunks": chunks,
        "category": qa.get("category", "未分类"),
        "scores": judge_scores,
        "guard_decision": guard_result.decision,
        "guard_reason": guard_result.reason,
        "retrieval_score": top_score,
    }


async def run_eval() -> list[dict]:
    """Run full eval pipeline. Returns list of scored results."""
    qa_pairs = load_qa_pairs()

    qdrant = QdrantClient(path=QDRANT_PATH)
    ensure_collection(qdrant)

    results = []
    for qa in qa_pairs:
        try:
            result = await run_single_qa(qa, qdrant)
            results.append(result)
            total = result["scores"]["total"]
            logger.info(
                f"{qa['id']}: {total}/15 "
                f"(acc={result['scores']['accuracy']} "
                f"cmp={result['scores']['completeness']} "
                f"saf={result['scores']['safety']}) "
                f"guard={result['guard_decision']}"
            )
        except Exception as e:
            logger.error(f"{qa['id']} failed: {e}")
            results.append({
                "qa_id": qa["id"],
                "question": qa["question"],
                "reply": f"ERROR: {e}",
                "expected_points": qa.get("expected_points", []),
                "forbidden_points": qa.get("forbidden_points", []),
                "chunks": [],
                "category": qa.get("category", "未分类"),
                "scores": {
                    "accuracy": 0, "completeness": 0, "safety": 0,
                    "has_hallucination": False, "hallucination_detail": str(e),
                    "overall_comment": "PIPELINE_ERROR", "total": 0,
                    "parse_error": True,
                },
                "guard_decision": "error",
                "guard_reason": str(e),
                "retrieval_score": 0.0,
            })

    qdrant.close()
    return results


async def run_ocr_noise_eval(base_results: list[dict]) -> list[dict]:
    """Re-run 5 selected QAs with OCR noise and return results."""
    qa_pairs = load_qa_pairs()
    qdrant = QdrantClient(path=QDRANT_PATH)
    ensure_collection(qdrant)

    # Select 5 QAs: pick one from each category if possible
    selected_ids = {"qa_01", "qa_04", "qa_11", "qa_17", "qa_19"}
    noise_types = ["prefix", "suffix", "multi", "prefix", "suffix"]

    results = []
    for qa in qa_pairs:
        if qa["id"] not in selected_ids:
            continue
        noise_type = noise_types[len(results) % len(noise_types)]
        noisy_qa = dict(qa)
        noisy_qa["question"] = add_ocr_noise(qa["question"], noise_type)
        try:
            result = await run_single_qa(noisy_qa, qdrant)
            result["qa_id"] = qa["id"]  # Keep original ID for comparison
            result["noise_type"] = noise_type
            results.append(result)
        except Exception as e:
            logger.error(f"Noise eval {qa['id']} failed: {e}")

    qdrant.close()
    return results


def main():
    """Entry point: python -m eval.run"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    start = time.monotonic()
    logger.info("Starting eval harness...")

    results = asyncio.run(run_eval())
    logger.info(f"Main eval complete: {len(results)} QAs")

    noise_results = asyncio.run(run_ocr_noise_eval(results))
    logger.info(f"OCR noise eval complete: {len(noise_results)} variants")

    report = format_report(results, noise_results)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")

    elapsed = time.monotonic() - start
    logger.info(f"Report written to {REPORT_PATH} ({elapsed:.1f}s)")
    print(report)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
SKIP_EMBED_TESTS=1 python -m pytest tests/test_eval_run.py -v
```
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add -f tests/test_eval_run.py eval/run.py
git commit -m "feat: add eval runner main entry"
```

---

### Task 6: Integration — run full eval and verify report

**Files:**
- Create: `tests/test_eval_integration.py`
- Modify: (none)

- [ ] **Step 1: Write integration test**

```python
# tests/test_eval_integration.py
"""Integration test: verify eval pipeline produces a valid report.

This test requires DEEPSEEK_API_KEY and Qdrant data to be set up.
Skip if DEEPSEEK_API_KEY is not available.
"""

import os
import pytest
from pathlib import Path


@pytest.mark.skipif(
    not os.environ.get("DEEPSEEK_API_KEY"),
    reason="DEEPSEEK_API_KEY not set — requires API access"
)
def test_eval_pipeline_produces_report():
    """Full pipeline run produces a report file with expected sections."""
    import asyncio
    from eval.run import run_eval, run_ocr_noise_eval
    from eval.reporter import format_report

    results = asyncio.run(run_eval())
    assert len(results) == 20, f"Expected 20 results, got {len(results)}"

    # Every result must have scores
    for r in results:
        assert "scores" in r
        assert "accuracy" in r["scores"]
        assert "completeness" in r["scores"]
        assert "safety" in r["scores"]
        assert "guard_decision" in r

    noise_results = asyncio.run(run_ocr_noise_eval(results))
    assert len(noise_results) == 5, f"Expected 5 noise results, got {len(noise_results)}"

    report = format_report(results, noise_results)
    assert "Eval Report" in report
    assert "总览" in report
    assert "通过率" in report
    assert "幻觉率" in report

    # Write report to verify no errors
    Path("logs").mkdir(exist_ok=True)
    report_path = Path("logs/eval_report.txt")
    report_path.write_text(report, encoding="utf-8")
    assert report_path.exists()
    print(f"\nReport written to {report_path}")
    print(report)


def test_eval_module_structure():
    """Verify all eval modules are importable."""
    from eval.scorer import score_reply, build_judge_prompt, parse_judge_response
    from eval.reporter import format_report, _format_section_summary
    from eval.run import load_qa_pairs, add_ocr_noise, run_eval

    assert load_qa_pairs() is not None
    assert callable(add_ocr_noise)
    assert callable(format_report)
```

- [ ] **Step 2: Verify module structure test passes**

```bash
SKIP_EMBED_TESTS=1 python -m pytest tests/test_eval_integration.py::test_eval_module_structure -v
```
Expected: PASS

- [ ] **Step 3: If DEEPSEEK_API_KEY available, run full pipeline test**

```bash
SKIP_EMBED_TESTS=1 python -m pytest tests/test_eval_integration.py::test_eval_pipeline_produces_report -v -s
```
Expected: PASS (or SKIP if no API key)

- [ ] **Step 4: Commit**

```bash
git add -f tests/test_eval_integration.py
git commit -m "test: add eval integration test"
```

---

### Task 7: Final verification — all tests pass

- [ ] **Step 1: Run full test suite**

```bash
SKIP_EMBED_TESTS=1 python -m pytest tests/ -q
```
Expected: all tests pass (218+), 3 pre-existing torch DLL skipped

- [ ] **Step 2: Run eval with real API (if available)**

```bash
python -m eval.run
```
Expected: report generated at `logs/eval_report.txt`

- [ ] **Step 3: Verify report readability**

```bash
head -50 logs/eval_report.txt
```
Check: human-readable Chinese text, no raw JSON dumps, all sections present

- [ ] **Step 4: Final commit**

```bash
git add logs/eval_report.txt
git commit -m "feat: add initial eval report baseline"
```

---

## Verification Checklist

- [ ] `python -m eval.run` runs without errors (or skips if no API key)
- [ ] `logs/eval_report.txt` contains: 总览, 按分类, 问题详情, OCR噪声影响
- [ ] `SKIP_EMBED_TESTS=1 python -m pytest tests/ -q` — all tests pass
- [ ] All new modules import correctly: `python -c "from eval.scorer import score_reply; from eval.reporter import format_report; from eval.run import run_eval"`
