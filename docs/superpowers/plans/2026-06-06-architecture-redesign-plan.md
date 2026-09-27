# Implementation Plan: 企业微信智能自动回复系统 — 架构重设计

Generated: 2026-06-06 | Source: Approved design doc + CEO plan + brainstorming spec
Timeline: 4-5 weeks

---

## Week 1: Detector Split + Foundations

### T1 (P1) — Split detector.py into 6 modules
- **Files:** `wxbot/detector/` (new package: `__init__.py`, `red_dot.py`, `bubble.py`, `name_region.py`, `customer_id.py`, `dedup.py`, `ocr_engine.py`)
- **Approach:** Extract each detection concern from `wxbot/detector.py` (672 lines) into its own module < 150 lines. Create `DetectorResult` dataclass in `__init__.py`. Update imports in `main.py` and `wxbot/scanner.py`. Keep existing behavior exactly — no logic changes.
- **Verify:** Run existing `pytest tests/`. All tests must pass. Run `main.py` against recorded screenshots.

### T2 (P1) — Structured JSON logging framework
- **Files:** `wxbot/detector/__init__.py`, `rag/guard.py`, `rag/responder.py`
- **Approach:** Add a `log_decision()` helper that writes JSON lines to `logs/decisions.jsonl` — timestamp, module, decision, confidence, metadata. Apply to all new detector modules and guard. Existing `logging` calls preserved alongside new JSON logger.
- **Verify:** Run system, verify `logs/decisions.jsonl` has entries from detector and guard.

### T3 (P1) — Layout change detection
- **Files:** `wxbot/scanner.py`, `main.py`
- **Approach:** Capture column 2/3 dimensions at startup. After each screenshot, compare current dimensions to baseline. Deviation > 10% → pause scan + GUI notification.
- **Verify:** Resize WeChat window manually, verify pause + notification.

### T4 (P2) — Prompt injection defense
- **Files:** `rag/generator.py`
- **Approach:** Add `_sanitize_message()` function. Filter patterns: "你是一个", "请忽略", "忽略上面的", "新的指令", "Ignore previous". ~30 lines.
- **Verify:** Unit test with known injection patterns, verify they're stripped.

### T5 (P2) — Eval harness skeleton
- **Files:** `eval/eval_harness.py`, `eval/labeled_qa.json`
- **Approach:** Create eval framework with 3 metrics (exact match, BLEU-4, BGE cosine). Start with 5 hand-annotated QA pairs from existing chat data. Framework ready for Week 4 expansion to 20 pairs.
- **Verify:** `python eval/eval_harness.py` runs and outputs scores.

---

## Week 2: Guard Redesign + Three-Tier Dispatch

### T6 (P1) — GuardResult dataclass + three-state return
- **Files:** `rag/guard.py`
- **Approach:** Add `GuardResult` dataclass. Replace `check_reply()` bool return with `check_reply_and_classify()` returning GuardResult. Define trigger words for each state: block (uncertainty: "可能", "不太确定", "建议咨询"; hallucinated numbers), low_risk (hedge: "具体价格请咨询", "详情请联系", "以实际为准"), pass (none of the above).
- **Verify:** Run guard tests with labeled cases for all 3 states. All must classify correctly.

### T7 (P1) — Parameterized thresholds from config.json
- **Files:** `rag/guard.py`, `config.json`
- **Approach:** Read `rag.high_confidence_threshold` and `rag.low_confidence_threshold` from config.json at init. Add defaults (0.7, 0.4). Replace hardcoded module constants. Hot-reload supported via existing ConfigManager.
- **Verify:** Change thresholds in config.json, verify guard uses new values after reload.

### T8 (P1) — Three-tier dispatch logic in responder.py
- **Files:** `rag/responder.py`
- **Approach:** Replace existing `check_retrieval()`/`check_reply()` dual-layer with single `classify_and_dispatch()` returning `DispatchDecision(level, result, metadata)`. Integrate GuardResult and cosine thresholds. Three paths: level_1 (auto-send), level_2 (human-confirm), level_3 (human-handle).
- **Verify:** Integration test with known high/medium/low cosine scores, verify correct dispatch path.

### T9 (P2) — Auto-reply logging
- **Files:** `rag/responder.py`, `logs/auto_replies.jsonl`
- **Approach:** Log every auto-sent reply with timestamp, customer_name, customer_message, ai_reply, confidence, decision_path, guard_result.
- **Verify:** Trigger auto-send, verify log entry written.

---

## Week 3: Human Fallback UI

### T10 (P1) — Human fallback queue module
- **Files:** `rag/human_fallback.py`
- **Approach:** Implement `PendingQueue` class: per-customer newest-only dedup, 24h TTL, expiry detection (check if customer sent new messages after queue entry). `push()`, `pop()`, `get_expired()`, `to_dict()`, `from_dict()`. JSON persistence to `data/state/pending_queue.json`. Load on startup, filter TTL.
- **Verify:** Unit test queue push/pop/expire/restore. Verify 24h TTL filtering.

### T11 (P1) — Non-blocking confirmation popup
- **Files:** `notify/fallback_ui.py`
- **Approach:** tkinter Toplevel with topmost=True. Layout: timer bar + customer message + editable Text widget (prefilled with AI reply) + [编辑][直接发送][拒绝] buttons. 30s `after()` countdown. Result communicated back via `asyncio.Queue`. Thread safety: try/except (TclError, RuntimeError) wrapper on after() callbacks.
- **Verify:** Trigger human-confirm path, verify popup appears, test each button + timeout path.

### T12 (P1) — Pending queue tab in GUI
- **Files:** `gui/main_window.py`
- **Approach:** Add "待处理" tab to existing notebook. treeview columns: time, customer, message summary, AI suggestion, confidence, status, actions (send/edit/ignore buttons). Per-customer row with cleanup count. Stats bar at bottom. Empty state: "无待处理消息 ✓".
- **Verify:** Add items to queue, verify tab displays correctly. Test send/edit/ignore actions.

### T13 (P2) — Session persistence
- **Files:** `rag/human_fallback.py`, `data/context/`
- **Approach:** Persist conversation context as `data/context/{customer_name}.jsonl`. One JSONL line per conversation turn. Load on startup, resume context for active conversations.
- **Verify:** Restart system, verify pending queue and context restored.

### T14 (P2) — Watchdog script
- **Files:** `scripts/watchdog.py`
- **Approach:** `main.py` writes heartbeat to `data/heartbeat.txt` every 5s. `watchdog.py` checks every 60s. No heartbeat → kill main.py process → restart main.py. ~30 lines Python.
- **Verify:** Kill main.py, verify watchdog restarts within 60s.

### T15 (P2) — Thread safety wrapper
- **Files:** `notify/fallback_ui.py`
- **Approach:** try/except (TclError, RuntimeError) around after() callbacks and popup creation. On failure: log error, toast notification as fallback, add message to pending queue.
- **Verify:** Trigger concurrent popup operations, verify graceful fallback.

---

## Week 4: Context, Eval, Integration

### T16 (P1) — Multi-turn conversation context
- **Files:** `rag/generator.py`, `rag/human_fallback.py`
- **Approach:** Build sliding window of last 3 conversation turns per customer (indexed by customer_name). Store in `data/context/{name}.jsonl`. Inject as `{conversation_history}` variable in generator.py prompt template.
- **Verify:** Send 5-turn conversation, verify only last 3 turns in prompt. Test with new customer (empty context).

### T17 (P2) — Expand eval harness to 20 QA pairs
- **Files:** `eval/labeled_qa.json`
- **Approach:** Expand from 5 to 20 hand-annotated QA pairs covering all 3 dispatch levels. Include edge cases: multi-turn context, numbers, negations, ambiguous questions.
- **Verify:** `pytest eval/eval_harness.py` runs with 20 pairs, outputs per-pair and aggregate scores.

### T18 (P1) — Integration tests with recorded screenshots
- **Files:** `tests/test_integration.py`
- **Approach:** Record screenshot sequences for known scenarios (new message, multi-message, layout shift, empty chat). Test full pipeline: detect → OCR → embed → search → dispatch.
- **Verify:** All integration tests pass. CI-ready.

### T19 (P2) — Error path testing
- **Files:** `tests/test_error_paths.py`
- **Approach:** Test each error scenario from error handling table: DeepSeek timeout, PaddleOCR crash, Qdrant down, queue IOError, LLM malformed JSON.
- **Verify:** Each error path tested, verify correct degradation behavior.

---

## Week 5: Deployment + Buffer

### T20 (P1) — One-click deploy script
- **Files:** `scripts/deploy.bat`, `scripts/setup.bat`
- **Approach:** `setup.bat`: create venv, pip install -r requirements.txt, download PaddleOCR model, prompt for DeepSeek API key. `deploy.bat`: activate venv, start Qdrant Docker, start main.py + watchdog.
- **Verify:** Fresh Windows machine, run setup.bat, verify system starts.

### T21 (P1) — On-site deployment
- **Approach:** Deploy to friend's company machine. Configure screen resolution, WeChat window position. Run first-time knowledge pipeline on their chat exports. Verify red dot detection, OCR, auto-reply end-to-end. First 3 days: all replies human-confirmed (forced Level 2 mode).
- **Verify:** 1 week stable operation, 0 crashes.

### T22 (P2) — Buffer tasks (as needed)
- OCR regression fixes (PaddleOCR version-specific)
- RPA timing tuning (click delays, scroll timing)
- Threshold tuning based on real data
- Performance optimization (screenshot capture speed)

---

## Task Summary

| Priority | Count | Description |
|----------|-------|-------------|
| P1 | 13 | Core: detector split, guard redesign, dispatch, UI, context, deployment |
| P2 | 9 | Supporting: logging, security, watchdog, testing, eval |

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| PaddleOCR regression after split | Medium | High | Keep original detector.py as fallback; test against recorded screenshots |
| tkinter/async thread conflicts | Medium | High | Thread safety wrapper (T15); watchdog auto-restarts (T14) |
| WeChat UI update breaks layout | Medium | Medium | Layout detection (T3); off-screen recovery (existing) |
| DeepSeek API cost higher than estimated | Low | Low | Cost estimate ~¥15/month with multi-turn; monitor daily |
| Timeline slip | Medium | Medium | Week 5 buffer; prioritize P1 over P2 |

## Dependencies

```
T1(detector) ──→ T6(guard) ──→ T8(dispatch) ──→ T10(queue) ──→ T16(context)
                                    │
T2(logging) ────────────────────────┤
T3(layout) ─────────────────────────┤
T4(prompt injection) ───────────────┤
                                    │
                    ┌───────────────┘
                    ↓
              T11(popup) ──→ T12(queue tab) ──→ T14(watchdog)
                    │
              T15(thread safety)

T5(eval skeleton) ──→ T17(eval expand) ──→ T18(integration)
                                               │
                                         T20(deploy)
```

## Verification Checklist

- [ ] All existing tests pass after each task
- [ ] New unit tests for each new module
- [ ] Integration tests cover full pipeline
- [ ] Error paths tested (8 scenarios)
- [ ] Manual smoke test: real WeChat + real customer message → reply sent
- [ ] 1-week stable operation at friend's company
