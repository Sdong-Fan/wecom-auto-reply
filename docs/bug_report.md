# Deep Code Review: personal_wxwork_ZTS

> 审查时间: 2026-06-03 | 更新: 2026-06-05（标记修复状态）

## 修复状态

| 级别 | 已修复 | 未修复 |
|------|--------|--------|
| P0 | 4/4 ✅ | 0 |
| P1 | 4/5 | 1 (输入框位置) |
| P2 | 3/4 | 1 (OCR阈值已调整为0.15) |

### P0: 致命问题 — 全部已修复 ✅

---

## P0: 致命问题

### 1. `window_mgr.py` — `capture_region` 参数传错 ✅ 已修复
### 2. `detector.py` — `can_click` 查询即写入 ✅ 已修复（拆分为 is_clickable + mark_clicked）
### 3. `main.py` — 兜底扫描重复执行 ✅ 已修复
### 4. `config.json` 配置不生效 ✅ 已修复（100+参数全面配置化）

---

## P1: 高优先级

### 5. 发送失败后仍调用 mark_replied ✅ 已修复（Responder.send_reply 检查返回值）
### 6. focus() 在 async 循环中调用 blocking sleep ✅ 已修复（Scanner统一管理）
### 7. 输入框点击位置不可靠 ⚠️ 已改进（config.json 可配置，60px距底部偏移）
### 8. embed_query 失败处理 ✅ 已修复（try/except + notify_escalation）
### 9. guard.py "可能""也许"过滤 ⚠️ 保持原样（需人工review调优）

---

## P2: 中优先级 — 全部已处理

| # | 问题 | 状态 |
|---|------|------|
| 10 | 无 DPI 感知 | ✅ 已添加 SetProcessDpiAwareness(2) |
| 11 | OCR 阈值太低 | ✅ 从0.2调至0.4(默认)/0.15(气泡)/0.05(名字) |
| 12 | col2_seen 去重失效 | ✅ 已修复 |
| 13 | LLM 无超时保护 | ✅ asyncio.wait_for(timeout=30)
