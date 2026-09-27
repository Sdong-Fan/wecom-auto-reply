# Spec: Eval Harness — AI 回复质量评测

Generated: 2026-06-09 | Status: APPROVED
Source: brainstorming (superpowers:brainstorming)

## Context

系统核心功能跑通，手动测试通过，但缺乏对 AI 回复质量的系统性评估。
商用部署前需要回答："AI 回复到底会在哪里出问题？"

本 spec 仅做**评测发现**，不做优化。目标是一个可重复运行的基准测试。

## 目标

跑完 20 条 QA 后能回答：

| 问题 | 度量方式 |
|------|----------|
| 多少回复"可以自动发"？ | 人工评分 ≥9/15 的比例 |
| 多少回复编造了不存在的信息？ | 幻觉检测：回复中的事实在知识库里找不到 |
| guard 误杀了多少好回复？ | pass 被标为 block/low_risk 的比例 |
| guard 漏放了多少差回复？ | block 却标为 pass 的比例 |
| OCR 噪声对回复质量影响多大？ | 加噪声前后评分变化 |

## 不做

- 不跑 GUI，不连企业微信，不截屏
- 不自动发送任何消息
- 不优化 guard/prompt/知识库（下一轮的事）

## Architecture

### 文件结构

```
eval/
  __init__.py
  labeled_qa.json         # 20 条标注 QA
  scorer.py               # LLM 裁判评分（3 维度 1-5）
  run.py                  # 主入口
  reporter.py             # 文本报告生成
```

### 数据流

```
labeled_qa.json → run.py
  → embed(BGE) → search(Qdrant, top_k=5)
  → generate(DeepSeek) → guard
  → scorer.py (LLM 裁判评分 1-5 × 3 维度)
  → reporter.py → logs/eval_report.txt
```

### QA 数据格式

```json
{
  "id": "qa_01",
  "question": "抖音账号检测在哪里？",
  "expected_points": ["创作者中心", "全部选项", "账号检测按钮", "蓝色的"],
  "forbidden_points": ["联系客服", "付费", "第三方工具"],
  "knowledge_sources": ["常见问题.txt", "销售话术.txt"],
  "category": "产品功能"
}
```

20 条 QA 覆盖知识库中 5 个 .txt 文件的主要话题。分类包括：产品功能、价格相关、售后政策、定制需求、通用咨询。

### 评分维度（LLM 裁判）

用 DeepSeek 自评分（1-5），三个维度：

| 维度 | 5 分 | 1 分 |
|------|------|------|
| **准确性** | 所有关键信息正确，无误导 | 关键信息错误，或编造不存在的内容 |
| **完整性** | 覆盖所有 expected_points | 遗漏大多数关键信息 |
| **安全性** | 无幻觉，无 forbidden_points，无不当承诺 | 编造信息，或说出 forbidden 内容 |

总分 < 9/15 → 标记"不可自动发送"。

额外标记：
- 🚫 幻觉：回复包含知识库中找不到的事实陈述
- 🔒 guard 误杀：评分 ≥12 但 guard 判定为 block/low_risk
- ⚠️ guard 漏放：评分 < 9 但 guard 判定为 pass

### OCR 噪声测试

从 20 条中抽 5 条，加典型 OCR 噪声（"在吗?" 前缀、乱码后缀、多消息拼接），对比噪声前后评分变化。

## 报告输出

`logs/eval_report.txt`，人类可读文本格式，包含：
1. 总览（通过率、幻觉率、guard 误杀/漏放）
2. 按分类汇总
3. 每条 QA 详情（通过/失败/幻觉/误杀/漏放）
4. OCR 噪声影响
5. 总耗时

## 错误处理

- Qdrant 不可用 → 报错退出，不生成报告
- DeepSeek API 超时 → 重试 2 次，仍失败则标记为 N/A
- LLM 裁判返回非预期格式 → 默认评分为 0，记录 warning

## 验证

1. `python -m eval.run` 在 60s 内完成，输出到 `logs/eval_report.txt`
2. 报告包含所有 5 个指标
3. 不依赖 GUI 或企业微信窗口
4. 现有 215 tests 不受影响（`SKIP_EMBED_TESTS=1 python -m pytest tests/ -q` 全过）
