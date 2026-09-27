# Spec: 企业微信智能自动回复系统 — 架构重设计 + 人工兜底

Generated: 2026-06-06 | Status: DRAFT
Source: /office-hours + /plan-ceo-review + brainstorming

## Context

见设计文档：`~/.gstack/projects/personal_wxwork_ZTS/laozhu-main-design-20260606-195029.md` (APPROVED)
见 CEO Plan：`~/.gstack/projects/personal_wxwork_ZTS/ceo-plans/2026-06-06-architecture-redesign.md` (ACTIVE)

本 spec 聚焦 brainstorming 中细化的 UI 设计 + 整合所有已批准的架构决策。

## Architecture

### Module Decomposition

```
wxbot/detector/
  __init__.py          # DetectorResult dataclass (见接口定义)
  red_dot.py           # 红点检测 (~80行)
  bubble.py            # 气泡检测: 蓝/灰/文本行 (~150行)
  name_region.py       # 名字区域检测 + OCR (~100行)
  customer_id.py       # 客户身份三层验证 (~80行)
  dedup.py             # MD5 + 模糊匹配 + 冷却 (~80行)
  ocr_engine.py        # PaddleOCR 封装 + UI污染过滤 (~60行)

rag/
  responder.py         # embed→search→generate→dispatch
  guard.py             # GuardResult(pass/block/low_risk) + config.json阈值
  human_fallback.py    # 待处理队列 + 过期检测 + context store

notify/
  toast.py             # 桌面通知 (已有, 用于 level 3)
  fallback_ui.py       # 人工确认弹窗 (tkinter Toplevel)

eval/
  eval_harness.py      # 20标注QA + 三维评分
  labeled_qa.json      # 标注数据集

scripts/
  watchdog.py          # 心跳监控 60s, auto-kill+restart
```

### Dataclass Interfaces

```python
@dataclass
class DetectorResult:
    detected: bool
    confidence: float          # 检测置信度 0.0-1.0
    bbox: tuple[int,int,int,int] | None
    text: str
    metadata: dict

@dataclass
class GuardResult:
    decision: Literal["pass", "block", "low_risk"]
    reason: str                # "uncertainty_keyword" | "hallucinated_number" | "hedge_phrase"
    confidence: float          # guard 自身置信度
```

### Three-Tier Dispatch

| Level | Trigger | Action | User Experience |
|-------|---------|--------|----------------|
| Auto-send | top-1 cosine > 0.7 AND guard=pass | Send immediately, log to auto_replies.jsonl | Silent |
| Human-confirm | 0.4 ≤ cosine ≤ 0.7 OR guard=low_risk | Popup with 30s timeout | Toplevel popup (topmost) |
| Human-handle | cosine < 0.4 OR guard=block OR LLM error | Toast notification | Toast + GUI red highlight |

Cosine thresholds read from config.json: `rag.high_confidence_threshold` (0.7), `rag.low_confidence_threshold` (0.4).

## UI Design (from brainstorming)

### Human Confirmation Popup

Layout: **纵向堆叠 + 内联编辑** (方案A)

```
┌─────────────────────────────────────────┐
│ ⏱ 还剩 28 秒 | 客户: 张姐 | 置信度: 62%   │
├─────────────────────────────────────────┤
│ 客户消息                                  │
│ ┌─────────────────────────────────────┐ │
│ │ "这个课程多少钱？学完能做什么？"       │ │
│ └─────────────────────────────────────┘ │
│ AI 建议回复 (可直接编辑)                   │
│ ┌─────────────────────────────────────┐ │
│ │ "您好！课程原价2980，活动价1980..."   │ │
│ └─────────────────────────────────────┘ │
│                                          │
│ [编辑] [直接发送] [拒绝]                   │
└─────────────────────────────────────────┘
```

**交互细节:**
- 点击 "直接发送" → clipboard paste → Enter 发送
- 点击 "编辑" → Text widget 获得焦点, 销售修改文字 → 点击 "发送"
- 点击 "拒绝" → 进入待处理队列
- 30 秒超时 → 自动进入待处理队列, 保留 Text widget 中的编辑内容
- 非阻塞: 弹窗用 tkinter `after()` 回调, 不阻塞 main.py async 循环
- 弹窗结果通过 `asyncio.Queue` 传回主循环
- 线程安全: after() 回调 + 弹窗创建路径 try/except (TclError, RuntimeError) → 失败时 toast 替代

**状态覆盖:**
- Loading: 显示 "正在生成回复..." (AI 调用中)
- Error: AI 生成失败 → 自动降级为 Level 3 (toast)
- Empty: OCR 为空 → 不弹窗, 直接走 Level 3

### Pending Queue Tab

Layout: **紧凑表格 + 每客户最新一条 + 逐客户清理计数** (方案A改良版)

```
┌─ 待处理 (2) ────────────────────────────────────────────┐
│ 时间   │ 客户 │ 最新消息      │ AI建议       │ 置信度 │ 操作       │
│ 14:35  │ 张姐 │ 有没有试听课  │ 有的！我们.. │ 55%    │ [发送][编辑][忽略] │
│        │      │ 📦 已清理 2 条更早消息                    │
│ 14:28  │ 李哥 │ 什么时候开课  │ 下一期6月..  │ 48%    │ [发送][编辑][忽略] │
│        │      │ 📦 已清理 1 条更早消息                    │
├──────────────────────────────────────────────────────┤
│ 共 2 条待处理 | 已自动发送 27 | 标记错误 1              │
└──────────────────────────────────────────────────────┘
```

**队列规则:**
- 每个客户只保留最新一条待处理消息
- 更早的同客户待处理消息自动清理, 清理数显示在客户行内
- 清理定义: 我们已回复该客户后, 客户又连续发的未自动匹配的消息 → 前面的过期
- 点击 "发送" 时做过期检测: 如果客户在待处理消息之后又发了新消息 → 不发送, 标记已清理
- 点击 "忽略" → 从队列移除
- TTL: 超过 24h 的条目自动清除
- 持久化: `data/state/pending_queue.json`, 启动恢复 + TTL 过滤

**状态覆盖:**
- Empty: 显示 "无待处理消息 ✓"
- 重启恢复: JSONL 加载 + 24h TTL 过滤
- 统计栏: 待处理数 / 已自动发送数 / 标记错误数

### Data Flow

```
screenshot → red_dot → click → bubble → OCR
  → embed(BGE) → search(Qdrant, top_k=5)
    ├─ top-1 cosine > 0.7 → generate(DeepSeek) → guard
    │   ├─ pass → auto-send → log → mark_seen
    │   ├─ low_risk → popup(human confirm)
    │   └─ block → toast + pending_queue
    ├─ 0.4 ≤ cosine ≤ 0.7 → generate → popup(human confirm)
    │   ├─ send → clipboard → Enter → log
    │   ├─ edit → Text widget → modify → send → log
    │   └─ timeout/reject → pending_queue
    └─ cosine < 0.4 → toast + pending_queue (manual only)
```

## New Features (from CEO review + brainstorming)

| Feature | Description | Effort |
|---------|-------------|--------|
| Multi-turn context | 滑动窗口 3 轮, customer_name 索引, 注入 generator.py prompt | S |
| Eval harness | 20 标注 QA + exact match/BLEU-4/BGE cosine 三维评分 | S |
| Session persistence | pending_queue + context JSONL, 24h TTL, 启动恢复 | S |
| Prompt injection defense | 过滤 customer msg 中的 system-prompt 模式 (~30 行) | S |
| Watchdog | 心跳文件 60s, auto-kill+restart (~30 行) | S |
| Layout change detection | 列 2/3 宽度校验, 变化 >10% → 暂停+通知 | S |

## Error Handling

| Failure | Rescue | User Sees |
|---------|--------|-----------|
| DeepSeek timeout (30s) | Retry 2x with backoff → Level 3 | Toast: "自动回复失败: {customer}" |
| DeepSeek non-200 | Retry 2x → Level 3 | Toast |
| DeepSeek JSON malformed | Retry 1x with stricter prompt → Level 3 | Silent retry → escalate |
| PaddleOCR RuntimeError | Skip bubble, log warning | Silent (next cycle captures) |
| Qdrant ConnectionError | Global degrade to manual mode | "知识库不可用，切换至人工模式" |
| Layout column shift >10% | Pause scan, notify | "企业微信窗口布局变化，请点击恢复" |
| tkinter TclError/RuntimeError | Catch + log + toast fallback | "通知弹窗失败，消息已加入待处理" |
| Queue file IOError (disk full) | Catch + toast + memory-only queue | "队列写入失败，请检查磁盘空间" |

## Error Flow Diagram

```
LLM API call
  ├─ success → parse response JSON
  │   ├─ valid → continue dispatch
  │   └─ JSONDecodeError → retry 1x (stricter prompt) → fail → escalate Level 3
  ├─ timeout (30s) → retry 2x (exponential backoff) → fail → escalate Level 3
  └─ HTTPError (429/500) → retry 2x → fail → escalate Level 3
```

## Testing

- **Unit:** Each detector module (mock screenshot → verify DetectorResult)
- **Guard:** Labeled cases for pass/block/low_risk discrimination
- **Integration:** Recorded screenshot sequence → verify full detection+reply flow
- **Eval harness:** `pytest eval/` → per-QA scores → check regressions
- **Watchdog:** Force-kill main.py → verify restart within 60s

## NOT in Scope
- Knowledge management UI, monitoring dashboard, SaaS
- Multi-window concurrency, Away-mode
- Training video transcription (Phase 2)
- Confidence calibration (deferred to post-deployment)
- Batch actions in pending queue (all per-item, no bulk send)

## Timeline (4-5 weeks)

| Week | Focus | Deliverables |
|------|-------|-------------|
| 1 | Detector split | 6 modules + DetectorResult + structured logging + layout detection + prompt injection filter |
| 2 | Guard + dispatch | GuardResult + config.json thresholds + three-level dispatch logic |
| 3 | Human fallback UI | Non-blocking popup + pending queue tab + session persistence + watchdog + thread safety |
| 4 | Context + eval + integration | Multi-turn sliding window + eval harness + recorded screenshot tests |
| 5 | Buffer | Deploy to friend's company, timing tuning, OCR regression fixes |
