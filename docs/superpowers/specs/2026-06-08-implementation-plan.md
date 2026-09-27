# 实施计划：非阻塞 UI + 后台自动化 + 消息去重

基于设计文档：`docs/superpowers/specs/2026-06-08-architecture-refactor-design.md`

## 实施顺序

按依赖关系排列，每步可独立测试。

---

### Step 1：消息去重 — 客户名+消息内容 fuzzy 匹配

**文件：** `wxbot/detector/_message_detector.py`

**变更：**
- `is_message_seen(text)` → `is_message_seen(customer_name, text)`
- `mark_message_seen(text)` → `mark_message_seen(customer_name, text)`
- 去重 key 改为 `f"{customer_name}:{text}"`
- 模糊匹配加客户名检查

**测试：** `tests/test_message_dedup.py`（新建）
- 同客户同消息 → 跳过
- 同客户不同消息 → 处理
- 不同客户同消息 → 处理
- 子串匹配（OCR 微差）→ 跳过
- 新消息合并 → 处理

**验证：** `pytest tests/test_message_dedup.py -v`

---

### Step 2：更新调用方 — 传入 customer_name

**文件：** `main.py`

**变更：**
- 所有 `is_message_seen(text)` 改为 `is_message_seen(customer_name, text)`
- 所有 `mark_message_seen(text)` 改为 `mark_message_seen(customer_name, text)`

**测试：** 现有回归测试不破坏

**验证：** `pytest tests/ --tb=line -q`

---

### Step 3：移除红点 is_clickable 过滤（已完成）

**状态：** 已在 c8fd3b8 中完成。红点不再被冷却机制过滤。

---

### Step 4：操作队列 — 线程安全的发送机制

**文件：** `main.py`（新增 `queue.Queue`）

**变更：**
- 创建 `send_queue = queue.Queue()`
- 后台线程 auto_send → `send_queue.put()`
- 用户手动发送 → `send_queue.put()`
- 主线程每 500ms 处理队列

**测试：** `tests/test_send_queue.py`（新建）
- 队列先进先出
- 并发 put 不丢数据
- 空队列 get_nowait 抛 QueueEmpty

**验证：** `pytest tests/test_send_queue.py -v`

---

### Step 5：前台切换逻辑

**文件：** `wxbot/scanner.py`（新增 `_switch_to_wecom_and_back`）

**变更：**
- 用 `pyautogui.getActiveWindow()` 保存当前窗口
- 用 `pyautogui.getWindowsWithTitle('企业微信')` 找企业微信
- `window.activate()` 切前台
- 操作完切回
- 异常处理：窗口未找到/切失败 → 跳过 + 日志

**测试：** `tests/test_foreground_switch.py`（新建）
- 企业微信未找到 → 跳过不崩溃
- 切前台失败 → 跳过不崩溃
- 操作失败 → 记录错误

**验证：** `pytest tests/test_foreground_switch.py -v`

---

### Step 6：clipboard + keyboard 发送

**文件：** `wxbot/scanner.py`（新增 `send_message_via_keyboard`）

**变更：**
- `subprocess.run(["powershell", ...])` 复制到剪贴板
- `pyautogui.hotkey('ctrl', 'v')` 粘贴
- `pyautogui.press('enter')` 发送
- 带 `CREATE_NO_WINDOW` 防闪终端

**测试：** `tests/test_keyboard_send.py`（新建）
- 剪贴板内容正确
- PowerShell 调用带 CREATE_NO_WINDOW

**验证：** `pytest tests/test_keyboard_send.py -v`

---

### Step 7：事件循环重构 — asyncio.sleep → mainloop + after

**文件：** `main.py`（核心重构）

**变更：**
- 移除 `asyncio.run(main())`
- 改为 `root.mainloop()` 驱动
- `root.after(3000, scan_tick)` 调度扫描
- 扫描在 `threading.Thread` 后台执行
- UI 更新通过 `root.after(0, callback)` 回主线程
- 移除 heartbeat 机制（不再需要）

**测试：** `tests/test_event_loop.py`（新建）
- mainloop 启动不阻塞
- after 调度正确触发
- 后台线程不阻塞 UI

**验证：** `pytest tests/test_event_loop.py -v`

---

### Step 8：弹窗改为待处理队列

**文件：** `main.py`, `gui/main_window.py`

**变更：**
- 移除 `show_confirmation_popup` 调用
- human_confirm 消息直接 `pending_queue.push()`
- UI 待处理标签页显示消息
- 底部编辑面板：选中行 → 编辑 → 确认发送/取消
- 角标通知：待处理数量 + 橙色提示

**测试：** `tests/test_pending_queue.py`（新建）
- human_confirm 入队列
- 编辑面板展开/收起
- 确认发送触发操作队列
- 忽略删除队列项

**验证：** `pytest tests/test_pending_queue.py -v`

---

### Step 9：移除 fallback_ui.py 弹窗依赖

**文件：** `main.py`

**变更：**
- 移除 `from notify.fallback_ui import show_confirmation_popup, PopupResult`
- 移除 `popup_result_queue` 相关代码
- 移除弹窗结果处理逻辑

**测试：** 回归测试不破坏

**验证：** `pytest tests/ --tb=line -q`

---

### Step 10：集成测试 + 手动验证

**验证清单：**
1. `python main.py` 启动，UI 正常显示
2. UI 按钮可点击（不被阻塞）
3. 客户发消息 → 自动回复（auto_send）
4. 低置信度消息 → 进待处理队列（不弹窗）
5. 待处理队列：发送/编辑/忽略 操作正常
6. 鼠标不被抢夺（除了短暂切前台）
7. 同一客户同一消息不重复处理
8. 不同客户相同消息正常处理
9. 现有 136 个测试不破坏

---

## 预计工作量

| Step | 内容 | 预计时间 |
|------|------|---------|
| 1-2 | 消息去重 | 30min |
| 4 | 操作队列 | 20min |
| 5-6 | 前台切换 + keyboard 发送 | 40min |
| 7 | 事件循环重构 | 1h |
| 8-9 | 弹窗改队列 + 清理 | 40min |
| 10 | 集成测试 | 30min |
| **总计** | | **~3.5h** |

## 风险

- **Step 7（事件循环重构）** 是最大风险 — 改动量大，需要确保 mainloop 和 after 调度正确
- **Step 5（前台切换）** 依赖 pyautogui 的窗口管理功能，需要实际测试
- **Step 8（编辑面板）** 是新 UI 组件，需要用户确认交互方式
