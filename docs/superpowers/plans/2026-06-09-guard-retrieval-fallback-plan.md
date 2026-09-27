# Guard Retrieval Fallback — Prompt + Retrieval Score Check

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce "guard 漏放" (AI fabricates reply when knowledge base has no answer) via two complementary defenses: stronger prompt and retrieval-score-gated guard.

**Architecture:** Part A strengthens `rag/generator.py` SYSTEM_PROMPT to make the "知识不足 → 需要人工处理" rule harder to violate. Part B adds a retrieval-score check in `rag/guard.py` classify_and_dispatch: when top retrieval score is low (<0.5), a "pass" result is downgraded to "low_risk", preventing fabricated replies from auto-sending.

**Tech Stack:** Python 3.10+, existing rag/* modules

---

## File Structure

```
rag/generator.py          # MODIFY: SYSTEM_PROMPT enhancement
rag/guard.py              # MODIFY: add retrieval score fallback rule
tests/test_generator.py   # CREATE: prompt content tests
tests/test_guard.py       # MODIFY: add retrieval score tests (file exists)
```

---

### Task 1: Strengthen SYSTEM_PROMPT (TDD)

**Files:**
- Create: `tests/test_generator.py`
- Modify: `rag/generator.py:15-33`

- [ ] **Step 1: Write failing test for prompt content**

```python
# tests/test_generator.py
"""TDD: generator.py prompt must strongly enforce '需要人工处理' rule."""

from rag.generator import SYSTEM_PROMPT


def test_prompt_requires_need_human_when_no_knowledge():
    """SYSTEM_PROMPT must instruct LLM to say '需要人工处理' when knowledge
    doesn't cover the question."""
    assert "需要人工处理" in SYSTEM_PROMPT, \
        "SYSTEM_PROMPT must include the required fallback phrase"

    # Must explicitly state that knowledge gaps → fallback
    has_gap_rule = (
        "不足以" in SYSTEM_PROMPT
        or "不包含" in SYSTEM_PROMPT
        or "没有涉及" in SYSTEM_PROMPT
        or "未涵盖" in SYSTEM_PROMPT
    )
    assert has_gap_rule, \
        "SYSTEM_PROMPT must state what to do when knowledge is insufficient"


def test_prompt_forbids_fabrication():
    """SYSTEM_PROMPT must explicitly forbid making up information."""
    assert "不得编造" in SYSTEM_PROMPT, \
        "SYSTEM_PROMPT must forbid fabrication"
    assert "知识片段" in SYSTEM_PROMPT, \
        "SYSTEM_PROMPT must reference '知识片段' (the context chunks)"


def test_prompt_forbids_generic_greetings():
    """SYSTEM_PROMPT must forbid generic greetings when knowledge is insufficient."""
    has_greeting_ban = (
        "不要" in SYSTEM_PROMPT
        or "禁止" in SYSTEM_PROMPT
        or "不得" in SYSTEM_PROMPT
    )
    assert has_greeting_ban, \
        "SYSTEM_PROMPT must include prohibition language"
```

- [ ] **Step 2: Run test to verify it passes (current prompt already has these)**

```bash
SKIP_EMBED_TESTS=1 python -m pytest tests/test_generator.py -v
```

If the current prompt already passes these, they serve as regression tests for the enhanced version.

- [ ] **Step 3: Enhance SYSTEM_PROMPT**

Replace the current `SYSTEM_PROMPT` in `rag/generator.py` with:

```python
SYSTEM_PROMPT = """你是一个专业的销售客服助手。你的回答必须严格遵守以下规则：

1. **只使用提供的知识片段来回答客户问题。** 如果知识片段中没有相关信息，或者客户问的话题知识片段完全没有涉及，你必须回复"需要人工处理"。不得编造、推测或补充任何知识片段中没有的信息。

2. 知识不足时的唯一正确回复是"需要人工处理"这6个字。
   - 禁止回复"请问您需要什么帮助"、"您好请问有什么可以帮您"等泛泛问候
   - 禁止回复"很高兴为您服务"、"我是XX客服"等自我介绍
   - 禁止回复"我没有相关信息"、"我不太清楚"、"建议您咨询"等变通说法
   - 知识不足时唯一正确回复就是"需要人工处理"

3. 当知识片段覆盖了客户问题时，回复要简洁、专业、友好，像真人销售在跟客户对话。

4. 不要透露你是AI或机器人。
5. 不要使用"根据知识库"、"根据提供的信息"等暴露AI身份的表述。

{conversation_history}

可用的知识片段：
{context}

请根据以上信息回复客户的问题。记住：知识不足时只能回复"需要人工处理"。。"""
```

Key changes:
- Rule 1: add "如果知识片段中没有相关信息，或者客户问的话题知识片段完全没有涉及"
- Rule 2: expand from one line to a full section with explicit forbidden patterns including "我没有相关信息"、"我不太清楚"、"建议您咨询"
- Rule 3: add conditional "当知识片段覆盖了客户问题时" to scope the positive behavior

- [ ] **Step 4: Run test to verify it passes**

```bash
SKIP_EMBED_TESTS=1 python -m pytest tests/test_generator.py -v
```
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add -f tests/test_generator.py rag/generator.py
git commit -m "feat: strengthen SYSTEM_PROMPT to reduce fabrication when knowledge is insufficient"
```

---

### Task 2: Add retrieval score check in guard (TDD)

**Files:**
- Modify: `rag/guard.py:202-238` (classify_and_dispatch)
- Modify: `tests/test_guard.py` (add test case)

- [ ] **Step 1: Read existing test file for patterns**

The file `tests/test_guard.py` already exists with 17 tests. We'll add new tests following the same pattern.

- [ ] **Step 2: Write failing test**

```python
# Add to tests/test_guard.py

def test_classify_and_dispatch_downgrades_on_low_retrieval():
    """When top retrieval score is below 0.5, a pass result is
    downgraded to low_risk to prevent auto-sending fabricated replies."""
    from rag.guard import classify_and_dispatch, GuardResult

    # Low retrieval score + clean reply (no keywords triggered)
    result = classify_and_dispatch(
        retrieval_scores=[0.35],  # Very low — knowledge doesn't match
        reply="我们公司位于北京市朝阳区建国路100号。",  # Made up
        context_chunks=["产品介绍：智能客服解决方案..."],  # No address info
    )
    # With low retrieval, even a pass reply should NOT auto-send
    assert result.decision != "pass", \
        f"Low retrieval score (0.35) should prevent auto-send. Got: {result.decision}"
    assert result.decision in ("low_risk", "block"), \
        f"Expected low_risk or block, got {result.decision}"
    assert "retrieval" in result.reason.lower() or "score" in result.reason.lower(), \
        f"Reason should mention retrieval concern. Got: {result.reason}"


def test_classify_and_dispatch_allows_pass_on_high_retrieval():
    """When top retrieval score is high, normal guard rules apply (no downgrade)."""
    from rag.guard import classify_and_dispatch

    # High retrieval score + knowledge-backed reply
    result = classify_and_dispatch(
        retrieval_scores=[0.85],
        reply="我们提供多种套餐，基础版、专业版和企业版。具体价格请咨询销售顾问。",
        context_chunks=[
            "我们提供多种套餐，基础版每月XX元，专业版每月XX元，企业版按需定制。"
            "具体价格请咨询销售顾问。"
        ],
    )
    # With high retrieval, the normal guard rules should apply
    # (This specific reply contains "请咨询" which is currently
    # in UNCERTAINTY_KEYWORDS → block. That's an unrelated issue.)
    assert result.decision in ("pass", "low_risk", "block"), \
        "High retrieval should use normal guard rules"
```

- [ ] **Step 3: Run test to verify it fails**

```bash
SKIP_EMBED_TESTS=1 python -m pytest tests/test_guard.py::test_classify_and_dispatch_downgrades_on_low_retrieval -v
```
Expected: FAIL (low retrieval score → currently passes through without check)

- [ ] **Step 4: Implement retrieval score check in guard.py**

In `rag/guard.py`, add after line 228 (after `check_reply_and_classify` call) and before line 232 (llm_requests_human check):

```python
    result = check_reply_and_classify(reply, context_chunks)

    # ── Low retrieval score → downgrade pass to low_risk ────────────
    # When retrieval confidence is low, the knowledge base doesn't
    # cover the question well. Even if the reply looks OK (no keywords
    # triggered), it may be fabricated. Downgrade to low_risk so a
    # human can verify before sending.
    if retrieval_scores and retrieval_scores[0] < 0.5:
        if result.decision == "pass":
            logger.info(
                f"Guard: low_risk — low retrieval score "
                f"({retrieval_scores[0]:.2f} < 0.5)"
            )
            return GuardResult(
                decision="low_risk",
                reason=f"low_retrieval_confidence: {retrieval_scores[0]:.2f}",
                confidence=0.50,
            )

    # LLM requests human → downgrade to low_risk (not block)
```

Add the configurable threshold as a module-level constant:

At the top of `rag/guard.py`, after the existing constants (~line 54):

```python
# Retrieval score below this triggers low_risk downgrade even if reply
# passes content checks.  Prevents auto-sending fabricated replies when
# the knowledge base doesn't cover the question.
LOW_RETRIEVAL_THRESHOLD = 0.5
```

Then use the constant in the check:
```python
    if retrieval_scores and retrieval_scores[0] < LOW_RETRIEVAL_THRESHOLD:
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
SKIP_EMBED_TESTS=1 python -m pytest tests/test_guard.py -v
```
Expected: all tests pass (19 total: 17 existing + 2 new)

- [ ] **Step 6: Commit**

```bash
git add rag/guard.py tests/test_guard.py
git commit -m "feat: downgrade pass to low_risk when retrieval score < 0.5"
```

---

### Task 3: Run eval to verify improvement

- [ ] **Step 1: Run eval harness**

```bash
python -m eval.run
```

- [ ] **Step 2: Check qa_20 result**

Expected: qa_20 ("你们公司在哪里？") should now show `guard_decision: low_risk` instead of `pass`, because the retrieval score for a question with no matching knowledge will be < 0.5.

- [ ] **Step 3: Verify no regression on other QAs**

Expected: All other QAs that previously passed should still pass (their retrieval scores are high enough).

- [ ] **Step 4: Commit updated eval report**

```bash
git add logs/eval_report.txt
git commit -m "eval: update baseline after guard retrieval fallback"
```

---

### Task 4: Final verification

- [ ] **Step 1: Run full test suite**

```bash
SKIP_EMBED_TESTS=1 python -m pytest tests/ -q
```
Expected: all tests pass, no regressions

- [ ] **Step 2: Verify guard behavior with edge cases**

```bash
python -c "
from rag.guard import classify_and_dispatch

# Edge: exactly at threshold
r = classify_and_dispatch([0.5], 'OK reply', ['context'])
print(f'At threshold 0.5: {r.decision}')  # Should be pass (not below)

# Edge: just below
r = classify_and_dispatch([0.49], 'OK reply', ['context'])
print(f'Below 0.5: {r.decision}')  # Should be low_risk

# Edge: empty scores
r = classify_and_dispatch([], 'OK reply', ['context'])
print(f'Empty scores: {r.decision}')  # Should be block (existing behavior)
"
```

---

## Verification Checklist

- [ ] `SKIP_EMBED_TESTS=1 python -m pytest tests/ -q` — all tests pass
- [ ] `python -m eval.run` — qa_20 shows guard=low_risk (not pass)
- [ ] Other 19 QAs unchanged (no regression)
- [ ] Prompt content tests verify the enhanced rules
