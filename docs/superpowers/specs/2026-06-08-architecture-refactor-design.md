# 架构重构：非阻塞 UI + 后台自动化 + 消息去重

日期：2026-06-08
状态：已审批

## 问题

三个互相关联的问题，共同根因是架构缺陷。

### 问题 1：UI 无法交互

`asyncio.sleep(3)` 阻塞主线程，tkinter 事件每 3 秒才处理一次。弹窗按钮点击无响应，文本框无法编辑，主窗口按钮无法点击。

### 问题 2：鼠标被抢夺

`pyautogui` 在主线程中操作鼠标（`scanner.focus()`、`scanner.click_col2_row()`），直接挪移用户鼠标。用户无法在程序运行时正常使用电脑。

### 问题 3：红点重复检测

处理完红点后，企业微信的红点不会立即消失。每 3 秒扫描都检测到同一红点，重复 OCR。现有的消息去重只按文本 hash 匹配，不区分客户，导致不同客户的相同问题被误去重。

## 设计

### 整体架构

```
┌─────────────────────────────────────────────────────┐
│  tkinter mainloop（主线程，持续响应）                  │
│                                                      │
│  - 处理 UI 事件（按钮点击、文本输入、窗口操作）         │
│  - 显示待处理队列、已回复记录                          │
│  - 用户操作：发送/编辑/忽略                            │
│  - after(3000) 触发扫描调度                           │
└───────────────────────┬─────────────────────────────┘
                        │ 调度
┌───────────────────────▼─────────────────────────────┐
│  后台线程（扫描 + 自动化）                             │
│                                                      │
│  - 扫描企业微信聊天列表                               │
│  - 检测红点 → OCR → RAG → Guard → 分流决策            │
│  - auto_send: clipboard + keyboard 发送（不挪鼠标）   │
│  - human_confirm/human_handle: 写入待处理队列         │
│  - 消息去重：客户名+消息内容 fuzzy 匹配               │
└─────────────────────────────────────────────────────┘
```

### 组件变更

#### 1. 事件循环：asyncio.sleep → mainloop + after

**当前代码（main.py 主循环）：**
```python
while True:
    await asyncio.sleep(CHECK)       # 阻塞 3 秒
    _write_heartbeat()
    if window._should_exit:
        _cleanup()
        break
    window.update()                  # 处理 tkinter 事件
    # ... 扫描逻辑 ...
```

**改为：**
```python
# 主线程：tkinter mainloop
def scan_tick():
    """每 3 秒触发一次扫描（在后台线程执行）"""
    threading.Thread(target=_run_scan, daemon=True).start()
    root.after(CHECK * 1000, scan_tick)

def _run_scan():
    """后台线程：执行扫描逻辑"""
    # 扫描、OCR、RAG、分流决策
    # auto_send → clipboard + keyboard 发送
    # human_confirm/human_handle → 写入待处理队列
    # 通过 root.after(0, callback) 更新 UI

root.after(CHECK * 1000, scan_tick)
root.mainloop()
```

**关键点：**
- `root.mainloop()` 持续处理 tkinter 事件，UI 始终响应
- `root.after(3000, scan_tick)` 每 3 秒调度一次扫描
- 扫描在后台线程执行，不阻塞 UI
- UI 更新通过 `root.after(0, callback)` 回到主线程

#### 2. 自动化操作：pyautogui + 自动切前台

**保留 pyautogui** — 产生真实鼠标/键盘事件，模拟人类行为，封号风险最低。

**前台切换策略：**

| 操作 | 切前台？ | 说明 |
|------|:---:|------|
| OCR 截图 | ❌ | 只需企业微信可见（不遮挡） |
| 检测红点 | ❌ | 纯图像处理 |
| 点击红点进入对话 | ✅ | 短暂切前台，<1秒 |
| auto_send 发送消息 | ✅ | 短暂切前台，<1秒 |
| human_confirm 入队列 | ❌ | 不切前台，写入 UI 队列 |
| 用户操作 UI | ❌ | 用户主动操作 |

**自动切前台流程：**
```python
def _switch_to_wecom_and_back(func):
    """执行操作后自动切回用户窗口"""
    # 1. 保存当前前台窗口和鼠标位置
    prev_window = pyautogui.getActiveWindow()
    prev_pos = pyautogui.position()
    # 2. 找到企业微信窗口
    wecom_windows = pyautogui.getWindowsWithTitle('企业微信')
    if not wecom_windows:
        log.warning("未找到企业微信窗口，跳过操作")
        return
    wecom_window = wecom_windows[0]
    # 3. 切到企业微信
    try:
        wecom_window.activate()
        time.sleep(0.2)
    except Exception as e:
        log.warning(f"切换前台失败: {e}，跳过操作")
        return
    # 4. 执行操作
    try:
        func()
    except Exception as e:
        log.error(f"操作执行失败: {e}")
    # 5. 切回用户窗口
    try:
        if prev_window:
            prev_window.activate()
        pyautogui.moveTo(prev_pos)
    except Exception:
        pass  # 切回失败不致命
```

**异常处理：**
- 企业微信窗口未找到 → 跳过操作，日志警告，不崩溃
- 切前台失败（权限不足）→ 跳过操作，日志警告
- 操作执行失败 → 记录错误，继续执行切回
- 切回失败 → 忽略，不致命

**用户大部分时间可自由操作 UI。** 只有检测到新消息需要点击/发送时才短暂切换（<1秒）。

#### 3. 消息去重：客户名 + 消息内容 fuzzy 匹配

**当前代码（_message_detector.py）：**
```python
def is_message_seen(self, text: str) -> bool:
    h = hashlib.md5(text.encode()).hexdigest()
    if h in self._seen_messages:
        return True
    # 模糊匹配：子串包含
    for seen_h, (ts, seen_text) in self._seen_messages.items():
        if text in seen_text or seen_text in text:
            return True
    return False
```

**改为：**
```python
def is_message_seen(self, customer_name: str, text: str) -> bool:
    """客户名 + 消息内容联合去重"""
    if not text or len(text) < 3:
        return False
    now = time.time()
    # 精确匹配：客户名+消息 hash
    key = f"{customer_name}:{text}"
    h = hashlib.md5(key.encode()).hexdigest()
    if h in self._seen_messages and now - self._seen_messages[h][0] < self._msg_dedup_sec:
        return True
    # 模糊匹配：同一客户的子串包含
    for seen_h, (ts, seen_text) in list(self._seen_messages.items()):
        if now - ts >= self._msg_dedup_sec:
            continue
        if customer_name in seen_text:
            seen_msg = seen_text.split(":", 1)[1] if ":" in seen_text else seen_text
            if text in seen_msg or seen_msg in text:
                return True
    return False

def mark_message_seen(self, customer_name: str, text: str):
    """标记客户名+消息已处理"""
    if text:
        key = f"{customer_name}:{text}"
        h = hashlib.md5(key.encode()).hexdigest()
        self._seen_messages[h] = (time.time(), key)
```

**效果：**

| 场景 | 结果 |
|------|------|
| 客户A "课程多少钱？" → 客户A "课程多少钱" | 跳过（同客户，子串匹配） |
| 客户A "课程多少钱？" → 客户A "能定制吗？" | 处理（不同消息） |
| 客户A "课程多少钱？" → 客户B "课程多少钱？" | 处理（不同客户） |
| 客户A "课程多少钱？" → 客户A "课程多少钱 能定制" | 处理（新消息合并） |

**无冷却延迟** — 新消息在下一轮扫描（3 秒内）立即处理。

#### 4. 弹窗 → 待处理队列

**当前：** human_confirm 弹出 tkinter Toplevel 弹窗（阻塞、抢焦点）

**改为：** human_confirm 消息直接写入待处理队列，不弹窗。用户在 UI 的「待处理」标签页查看和操作。

**待处理队列操作：**
- **发送** — 选中行，点「发送」，后台用 clipboard + keyboard 发送 AI 建议原文
- **编辑** — 选中行，点「编辑」，底部展开编辑面板（预填 AI 建议），修改后点「确认发送」
- **忽略** — 丢弃该消息

**编辑面板设计：**
```
┌─────────────────────────────────────────────────────────────┐
│ [全部] [待处理] [已回复] [待人工]                              │
├──────┬────────┬──────────┬──────────┬──────┬──────┤
│ 时间 │ 客户    │ 客户消息  │ AI建议    │ 置信度│ 操作 │
├──────┼────────┼──────────┼──────────┼──────┼──────┤
│ 12:46│ Will   │ 你们和竞品│ 我们的产  │ 53%  │[发送]│
│      │        │ 比有什么  │ 品响应速度│      │[编辑]│
│      │        │ 优势？    │ 快...     │      │[忽略]│
├──────┴────────┴──────────┴──────────┴──────┴──────┤
│ ┌─────────────────────────────────────────────┐   │
│ │ 编辑区（选中行后展开，预填 AI 建议）          │   │
│ │ 我们的产品响应速度快、准确率高...（可编辑）    │   │
│ └─────────────────────────────────────────────┘   │
│              [确认发送]  [取消]                     │
└─────────────────────────────────────────────────────┘
```

**交互流程：**
1. 选中待处理队列中的一行
2. 点「编辑」→ 底部编辑面板展开，预填 AI 建议文本
3. 用户修改文本
4. 点「确认发送」→ 后台发送，该行从队列移除，编辑面板收起
5. 点「取消」→ 编辑面板收起，不发送
6. 编辑中如果客户新消息到来 → 不影响编辑，新消息进队列排队

### 红点检测流程（新）

```
扫描聊天列表
    ↓
检测红点（所有红点，不过滤）
    ↓
对每个红点：
  1. OCR 提取客户名 + 消息文本
  2. is_message_seen(customer_name, text) → 已处理？→ 跳过
  3. 未处理 → RAG + Guard + 分流
  4. mark_message_seen(customer_name, text)
  5. 执行对应动作（发送/入队列）
```

### 线程安全

后台线程通过 `root.after(0, callback)` 将 UI 更新请求发回主线程。所有 tkinter 操作在主线程执行。

```python
# 后台线程
def _run_scan():
    # ... 扫描逻辑 ...
    if dispatch_level == "human_confirm":
        root.after(0, lambda: pending_queue.push(...))
        root.after(0, lambda: window.refresh_pending_queue(...))
```

### 用户反馈

**auto_send 消息：** 发送成功后，在主窗口「已回复」标签页显示记录。状态栏更新计数（已自动发送 N 条）。

**human_confirm 入队列：** 新消息入队列时，「待处理」标签页数字角标 +1。如果用户当前不在「待处理」标签页，标签页文字变色（橙色）提示有新消息。

**待处理队列为空时：** 显示提示文字"暂无待处理消息，系统正在自动处理低置信度消息"。

**操作反馈：**
- 点「发送」→ 该行消失 + 状态栏"已发送"
- 点「确认发送」→ 编辑面板收起 + 该行消失 + 状态栏"已发送"
- 点「忽略」→ 该行消失 + 状态栏"已忽略"

### 线程安全

后台线程和用户操作可能同时触发发送。用操作队列避免冲突：

```python
# 操作队列（线程安全）
import queue
send_queue = queue.Queue()

# 后台线程：auto_send → 放入队列
send_queue.put(("auto", customer_name, reply_text))

# 用户点击「发送」→ 放入队列
send_queue.put(("manual", customer_name, reply_text))

# 主线程定时处理队列（每 500ms）
def _process_send_queue():
    try:
        while True:
            mode, name, text = send_queue.get_nowait()
            _do_send(name, text)  # clipboard + keyboard 发送
    except queue.Empty:
        pass
    root.after(500, _process_send_queue)
```

**保证同一时刻只有一个发送操作执行。** 用户操作和 auto_send 不会同时操作剪贴板。

### 约束

- 企业微信必须在屏幕可见位置（不被遮挡），OCR 截图才能正常工作
- 用户可以把企业微信放在第二显示器或屏幕角落，在主区域自由工作
- 点击红点和 auto_send 会短暂切换前台（<1秒），之后自动切回用户窗口
- 使用 pyautogui 模拟真实鼠标/键盘事件，封号风险最低

## 不变的部分

- RAG 检索、LLM 生成、Guard 质量检查 — 逻辑不变
- 配置管理（config.json）— 不变
- Qdrant 客户端 — 不变
- 日志记录 — 不变
- 三级分流阈值（0.7 / 0.4）— 不变

## 文件变更

| 文件 | 变更 |
|------|------|
| `main.py` | 主循环从 asyncio.sleep 改为 mainloop + after + 后台线程 |
| `wxbot/scanner.py` | 新增自动切前台逻辑 + clipboard 发送 |
| `wxbot/detector/_message_detector.py` | `is_message_seen` 和 `mark_message_seen` 加 customer_name 参数 |
| `gui/main_window.py` | 待处理队列增加内嵌编辑功能 |
| `notify/fallback_ui.py` | 不再使用（弹窗改为待处理队列） |
| `requirements.txt` | 无新增依赖 |

## 依赖变更

无新增依赖。窗口管理使用 `pyautogui` 内置功能（`getActiveWindow()`、`getWindowsWithTitle()`、`window.activate()`）。

## 测试策略

1. 消息去重测试 — 同客户同消息跳过、不同客户同消息处理、子串匹配
2. 后台线程测试 — 扫描不阻塞 UI、UI 更新回到主线程
3. clipboard + keyboard 发送测试 — 剪贴板内容正确、Enter 发送
4. 待处理队列测试 — 发送/编辑/忽略操作
5. 回归测试 — 现有 136 个测试不破坏
