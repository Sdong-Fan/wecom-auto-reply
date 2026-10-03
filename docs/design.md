# 企业微信智能客服 — 设计文档

> 2026-06-03 初稿 | 2026-06-05 更新（PaddleOCR/模块解耦/防重复回复）| 2026-06-10 更新（事件循环/发送队列/打包/eval）

## 方案：截图 RPA + RAG 知识库

参考 [wecom-cs-mano](https://github.com/JZQiang/wecom-cs-mano) 方案，适配 Windows。

**核心思路**：不依赖企微API，通过截图+像素检测+OCR实现消息收发。

## 流程

```
tkinter mainloop + root.after() 定时器:
  scan_tick (每3秒, 后台线程, Lock非阻塞):
    1. Scanner   → 截图col2(群聊列表) → PIL红点检测
    2. Detector  → 名字OCR → @微信判定客户 → is_clickable冷却检查
    3. Scanner   → 点击进入对话 → 滚到底部
    4. Detector  → 气泡扫描(蓝泡/灰泡/文字行) → 提取未回复气泡
    5. Detector  → PaddleOCR提取文字(置信度0.15)
    6. Detector  → 客户判定(蓝泡/绿badge/@微信/关键词)
    7. Guard     → 停止词检测 → 消息去重(MD5, 300s) → 冷却检查
       └─ 检索分数<0.5 → pass降级为low_risk（防漏放）
    8. Responder → RAG检索(Qdrant) → DeepSeek生成 → 质量门控 → 入发送队列
  _process_send_queue_tick (每500ms, 主线程):
    9. 出队 → ctypes剪贴板 → Ctrl+V → Enter → 切换窗口
```

## 模块

| 模块 | 文件 | 职责 |
|------|------|------|
| 协调器 | `main.py` | tkinter mainloop + 定时器调度 |
| **Scanner** | `wxbot/scanner.py` | 窗口管理+截图+点击（统一接口） |
| **Detector** | `wxbot/detector/` | 检测器包（6个独立模块，委托模式） |
| Detector: red_dot | `wxbot/detector/red_dot.py` | 红点检测 |
| Detector: bubble | `wxbot/detector/bubble.py` | 蓝泡/灰泡/文字行检测 |
| Detector: name_region | `wxbot/detector/name_region.py` | 名字区域+绿badge |
| Detector: customer_id | `wxbot/detector/customer_id.py` | 客户判定（@微信/绿badge/蓝泡） |
| Detector: ocr_engine | `wxbot/detector/ocr_engine.py` | PaddleOCR封装 |
| Detector: dedup | `wxbot/detector/dedup.py` | 去重（图片/消息/冷却） |
| **Responder** | `rag/responder.py` | 三级分流+发送+结构化日志 |
| **ConfigManager** | `config/manager.py` | 配置加载+热更新 |
| Sender | `wxbot/sender.py` | 剪贴板(ctypes) + 粘贴发送 |
| DebugVisualizer | `debug/visualizer.py` | 截图标注+meta.json |
| MainWindow | `gui/main_window.py` | tkinter主控窗口（消息Tab+待处理Tab） |
| WindowManager | `wxbot/window_mgr.py` | win32gui窗口定位（legacy） |
| Capture | `wxbot/capture.py` | mss截图（legacy） |
| RAG Retriever | `rag/retriever.py` | Qdrant向量检索 |
| RAG Generator | `rag/generator.py` | DeepSeek回复生成（多轮上下文+注入防御） |
| RAG Guard | `rag/guard.py` | 质量门控（三态: pass/block/low_risk） |
| **PendingQueue** | `rag/human_fallback.py` | 待处理队列（去重+24h TTL+持久化） |
| **ContextStore** | `rag/human_fallback.py` | 客户对话历史（JSONL，滑动窗口） |
| Embed Query | `rag/embed_query.py` | BGE本地嵌入 |
| Toast | `notify/toast.py` | Windows通知 |
| **Fallback UI** | `notify/fallback_ui.py` | 人工确认弹窗（30s倒计时+可编辑） |
| **Build** | `scripts/build.py` | PyInstaller一键构建 → `dist/WeComBot/` |
| **Eval** | `eval/` | 20 QA对 + LLM裁判3维评分（准确性/完整性/安全性） |
| **Chat Importer** | `pipeline/chat_importer.py` | 聊天记录解析 → QAPair（标准导出 + PC端复制转发格式） |
| **Video Transcribe** | `pipeline/video_transcribe.py` | faster-whisper 视频转录 |
| **Chunker** | `pipeline/chunker.py` | 文本分块（500字 + 100字重叠） |
| **Embedder** | `pipeline/embedder.py` | BGE 向量化 → Qdrant |

## 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 消息获取 | 截图RPA | 零资质、零费用、不依赖API |
| OCR引擎 | **PaddleOCR** (PP-OCRv4) | EasyOCR识别中文小字仅1字→PaddleOCR完整识别3行 |
| 防重复回复 | 蓝泡阻断+消息MD5去重+停止词 | 三层防护：气泡层+内容层+语义层 |
| 语音转文字 | 左半区深色文字检测 | 非灰泡非蓝泡背景的文字也能识别 |
| 客户判定 | 蓝泡+绿badge+OCR关键字 | 三重验证 |
| 知识库 | Qdrant本地文件模式 | 无需Docker |
| Embedding | BGE-small-zh (本地) | 免费离线 |
| LLM | DeepSeek API | 已有密钥 |
| 配置管理 | config.json (100+参数) | 所有阈值/比例/延迟可调，支持热更新 |
| 分流决策 | 三级: auto_send/human_confirm/human_handle | cosine阈值+guard三态分类，平衡效率与安全 |
| 人工确认 | tkinter弹窗(30s倒计时)+待处理队列 | 非阻塞，per-customer去重，可编辑发送 |
| 多轮上下文 | 滑动窗口(最近3轮)+JSONL持久化 | 客户连问时保持对话连贯 |
| 注入防御 | 正则过滤系统指令模式 | 防客户消息覆盖system prompt |
| 事件循环 | tkinter mainloop + root.after + 后台线程 | asyncio.sleep阻塞UI→改为mainloop+定时器，UI始终响应 |
| 统一发送路径 | send_queue + _do_send + 窗口切换 | 生成与发送解耦，所有发送走队列，每tick 1条防GUI阻塞 |
| 剪贴板 | ctypes Win32 API | 替代PowerShell子进程，无超时、无shell注入、瞬时完成 |
| Guard检索兜底 | 检索分数<0.5 → pass降级low_risk | 防止知识库不覆盖时AI编造回复（漏放1→0） |
| PyInstaller打包 | one-folder模式，BGE模型预捆绑 | ~3GB包体，启动.bat设TCL/TK环境变量，BGE从本地bge_model/加载 |
| 质量门控 | GuardResult三态(pass/block/low_risk) | 不确定→人工确认，确定差→拦截，确定好→自动 |
| 越权承诺护栏 | 回复含"有货/给你留着/两小时后就能到/保证" → 强制转人工 | 200 条测试集抓出 3 条**凭空承诺**（库存/时效/留货）：这类句子没有编造数字、也没编型号，数字校验与型号校验都拦不住，但客户会据此跑一趟门店 |
| 权限类强制转人工 | 投诉/退款/议价/比价/订单变更/重复追问 → 不看检索分，先转人工 | 这三类不是"资料库有没有"的问题，而是**权限问题**：机器人无权受理投诉、无权改价。实测"我要投诉"被闲聊通道当寒暄接走，投诉没进人工队列 |
| 无信息量不回 | 纯符号/纯数字/纯表情 → no_reply | 客户乱敲键盘时机器人回了调侃话（"哈哈这是啥，密码吗"），观感差，而且这类消息还能借闲聊通道绕开转人工 |
| 分类默认值 | 拿不准 → business（不再默认闲聊） | 原代码文档写着"判不准就说 business"，实现却默认 smalltalk，于是"支持分期吗""就它了"这类看不懂的话被送进闲聊通道，用店员口吻把"晚点回你""给你留着"直接发给了客户 |
| 打包折扣白名单 | "多台/打包/一起租 + 便宜些" → 不当议价拦 | 资料库里答得出来（机身+2 镜头打包 9 折），拦下等于把有答案的问题白转人工 |
| 看门狗 | ~~heartbeat文件+60s轮询+自动重启~~ 已删除 | 2026-06-07确认看门狗是崩溃循环根因，main.py自身稳定 |
| 聊天导入 | 自动格式检测 + 角色推断 | 标准导出(YYYY-MM-DD 角色-姓名) + PC端复制(姓名 MM/DD) 两种格式自动识别 |

## 已知限制

- 仅支持1对1私聊（群聊不触发回复）
- 企微窗口必须在前台可见
- 高DPI需要SetProcessDpiAwareness
- 企微版本更新可能导致像素参数失效
- PaddlePaddle/PyTorch DLL加载顺序敏感（torch必须优先导入）
- OCR累积文本可能导致旧消息被重复送入LLM（当前用模糊去重缓解）
- PyInstaller包体~2.9GB（PyTorch+PaddleOCR+BGE），首次启动BGE下载~2-3分钟
