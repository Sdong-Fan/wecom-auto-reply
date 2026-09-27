# 检测准确性 + 架构重构设计文档

> 日期: 2026-06-04
> 状态: 已确认

## 1. 背景

当前企业微信截图 RPA 自动回复系统存在以下问题：
- 蓝泡检测太宽，覆盖整个聊天区，灰泡被"吞噬"，客户消息漏回
- 红点误报（0个红点检测出6-8个）
- 聊天列表预览文字被当成对话名，误点击
- main.py 234行大循环，耦合严重，难测试难维护

## 2. 目标

| 优先级 | 目标 |
|--------|------|
| P0 | 修复检测准确性（蓝泡/红点/名字） |
| P1 | 模块解耦：Scanner / Detector / Responder / Config |
| P2 | 参数可配置：config.json 集中管理，支持热更新 |
| P2 | 调试可视化：每步截图+标注+meta.json |
| P3 | 主控窗口：tkinter 实时显示回复记录 |
| P3 | Windows 打包：PyInstaller + Inno Setup |

## 3. 检测准确性修复

### 3.1 蓝泡检测

**问题**：纯颜色阈值无高度/形状约束，单行蓝色像素匹配导致整个区域被判定为蓝泡。

**方案**：
1. 颜色阈值 `b > r + 15`（已修复）
2. 加最大高度限制：单个蓝泡高度不超过聊天区 15%
3. 连通域分析：用 flood fill 找连续蓝色区域，而非逐行扫描

### 3.2 红点检测

**问题**：全图扫描，红色图标（邮件提醒、客户联系等）被误判为红点。

**方案**：
1. 限定扫描区域：只扫描聊天列表左侧 5%（红点在头像左上角）
2. 形状过滤：红点是近似圆形，面积 < 50px²
3. 颜色收紧：R > 140, G < 80, B < 80

### 3.3 名字检测

**问题**：聊天预览文字（如"你好"）被 OCR 识别后当成对话名。

**方案**：
1. OCR 区域限定：只裁剪头像右侧、预览文字上方的窄条
2. 去重逻辑：连续两帧检测到相同名字才点击
3. 关键词过滤：排除明显不是名字的文字（如纯数字、单字）

## 4. 模块解耦

```
┌─────────────────────────────────────────────────────────┐
│                    main.py (协调器)                       │
│  只负责：启动 → 循环 → 调度 → 退出                        │
└─────────────────────────────────────────────────────────┘
         │              │              │              │
         ▼              ▼              ▼              ▼
┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│  Scanner    │  │  Detector   │  │  Responder  │  │  Config     │
│  截图+窗口   │  │  检测+OCR   │  │  RAG+发送   │  │  配置管理    │
└─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘
```

### 4.1 Scanner（wxbot/scanner.py）

```python
class Scanner:
    def find_wecom_window() -> int
    def capture_chat_list() -> Image
    def capture_chat_area() -> Image
    def click_position(x, y) -> None
```

### 4.2 Detector（wxbot/detector.py，重构）

```python
@dataclass
class ChatState:
    has_red_dot: bool
    red_dot_positions: list[tuple[int, int]]
    customer_name: str
    is_customer: bool
    unreplied_messages: list[str]
    last_reply_is_ours: bool

class Detector:
    def detect_red_dots(img) -> list[tuple[int, int]]
    def detect_bubbles(img) -> list[Bubble]
    def extract_text(img) -> str
    def is_customer_name(name_img, ocr_text) -> bool
    def analyze_chat(img) -> ChatState
```

### 4.3 Responder（rag/responder.py，新建）

```python
class Responder:
    def generate_reply(customer_name, messages) -> str | None
    def send_reply(text) -> bool
    def should_escalate(confidence) -> bool
```

### 4.4 Config（config/manager.py，新建）

```python
class ConfigManager:
    def __init__(config_path)
    def get(dotpath: str) -> Any
    def reload() -> None  # 热更新
```

## 5. 参数可配置

config.json 结构：

```json
{
  "detection": {
    "red_dot": {
      "scan_area": "left 5%",
      "r_min": 140, "r_max": 255,
      "g_max": 80, "b_max": 80,
      "min_pixels": 3
    },
    "blue_bubble": {
      "color": "#E6F4FF",
      "tolerance": 15,
      "max_height_ratio": 0.15,
      "pixel_threshold": 0.05
    },
    "gray_bubble": {
      "color": "#F5F5F5",
      "tolerance": 10,
      "max_height_ratio": 0.80,
      "pixel_threshold": 0.05
    },
    "chat_area": {
      "top_crop": 0.12,
      "bottom_crop": 0.70,
      "right_margin": 0.30
    }
  },
  "ocr": {
    "engine": "paddleocr",
    "min_confidence": 0.15,
    "min_name_confidence": 0.05,
    "use_angle_cls": false
  },
  "rag": {
    "model": "deepseek-chat",
    "confidence_threshold": 0.7,
    "max_context_chunks": 5
  },
  "scan": {
    "interval_seconds": 3,
    "fallback_top_n": 3
  },
  "debug": {
    "save_screenshots": true,
    "output_dir": "debug/",
    "max_history": 10
  }
}
```

## 6. 调试可视化

### 目录结构

```
debug/
├── 2026-06-04_14-30-00/
│   ├── 01_chat_list.png      # 聊天列表，标注红点
│   ├── 02_name_crop.png      # 裁剪的名字区域
│   ├── 03_chat_area.png      # 聊天区域
│   ├── 04_bubble_mask.png    # 蓝泡/灰泡 mask
│   ├── 05_ocr_result.png     # OCR 结果叠加
│   └── meta.json             # 结构化数据
└── latest -> 2026-06-04_14-30-00
```

### meta.json

```json
{
  "timestamp": "2026-06-04T14:30:00",
  "red_dots": [{"x": 12, "y": 45}],
  "customer_name": "will@微信",
  "is_customer": true,
  "bubbles": [
    {"type": "gray", "top": 200, "bottom": 250, "text": "你好"},
    {"type": "blue", "top": 150, "bottom": 190, "text": "..."}
  ],
  "unreplied": ["你好"],
  "action": "replied",
  "reply_text": "你好，请问有什么可以帮您？"
}
```

## 7. 主控窗口（tkinter）

### 布局

```
┌─────────────────────────────────────────────────────────────┐
│  企业微信智能客服                          [设置] [暂停] [×] │
├─────────────────────────────────────────────────────────────┤
│  状态: 运行中  |  已回复: 12  |  待人工: 2  |  未回复: 0     │
├─────────────────────────────────────────────────────────────┤
│  ┌─ 全部 ─┬─ 待回复 ─┬─ 已回复 ─┬─ 待人工 ─┬─ 未识别 ─┐    │
│  │                                                     │    │
│  │  14:30  will@微信     "你好"           → 已自动回复   │    │
│  │  14:28  张三@微信     "价格多少"        → 已自动回复   │    │
│  │  14:25  李四@微信     "能开发票吗"      → 待人工处理   │    │
│  │  14:20  王五@微信     "..."             → 未识别      │    │
│  │                                                     │    │
│  └─────────────────────────────────────────────────────┘    │
├─────────────────────────────────────────────────────────────┤
│  [导出记录]  [清空]                                           │
└─────────────────────────────────────────────────────────────┘
```

### 功能

- 实时显示每条消息的处理结果
- 待人工消息标红，可点击跳转
- 未识别消息标黄，提示补充知识库
- 状态栏显示统计信息
- 支持导出记录为 CSV

## 8. Windows 打包

| 工具 | 用途 |
|------|------|
| PyInstaller | Python → exe |
| Inno Setup | 生成安装程序 |

### 安装包内容

```
WeComAutoReply/
├── WeComAutoReply.exe
├── config.json
├── models/
│   ├── easyocr/
│   └── bge-base-zh/
└── data/
    └── qdrant/
```

### 首次运行向导

弹出配置窗口，要求输入：
1. DeepSeek API Key
2. 知识库路径
3. 扫描间隔

## 9. 开发进度

| 序号 | 任务 | 状态 |
|------|------|------|
| 1 | 检测修复（蓝泡/红点/名字） | ✅ 完成 |
| 2 | 模块解耦（Scanner/Detector/Responder/Config） | ✅ 完成 |
| 3 | 参数配置化（config.json + 热更新） | ✅ 完成 |
| 4 | 调试可视化（截图标注 + meta.json） | ✅ 完成 |
| 5 | 主控窗口（tkinter GUI） | ✅ 完成 |
| 6 | **OCR 引擎升级**（EasyOCR → PaddleOCR） | ✅ 完成 |
| 7 | 语音转文字识别（蓝泡内文字行检测） | ✅ 完成 |
| 8 | 防重复回复（蓝泡阻断+消息去重+停止词） | ✅ 完成 |
| 9 | Windows 打包（PyInstaller + Inno Setup） | ⏳ 待办 |

## 10. OCR 引擎：EasyOCR → PaddleOCR

**决策：** 2026-06-04 将 OCR 引擎从 EasyOCR 切换为 PaddleOCR。

**原因：**
- EasyOCR 对截图中小字号中文（14-20px）识别率极低，只能识别 "你" 一个字
- 对比度增强（`ImageEnhance.Contrast(2.0)`）对中文有害，会让中文置信度从 0.51 降到 0.01
- PaddleOCR 专门针对中文优化，小字中文识别远优于 EasyOCR

**兼容性：**
- `extract_text()` 接口完全不变，只改内部实现
- 其余模块（main.py、scanner.py、responder.py）零影响
- 改动局限在 `detector.py` 的 2-3 行代码

**代价：**
- PaddlePaddle CPU 版约 200MB（对比 EasyOCR 80MB）
- 首次加载 5-10 秒（对比 EasyOCR 3 秒）

## 11. 消息类型

- 文字消息：直接 OCR
- 语音消息：企微已自动转文字，当作文字处理
- 图片消息：暂不处理，后续扩展
