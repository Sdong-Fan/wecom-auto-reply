项目: personal_wxwork_ZTS

用户约束（不可违反）

- 只有用户说"清理"时才清除客户聊天历史 (data/context/)。
- 优先内联执行，不要派发子 agent — deepseek-v4-pro 子 agent 会 API 400 报错。

常用命令

```bash
# 测试: ~248 个 (3 个 torch DLL 失败, 1 个 API 跳过)
SKIP_EMBED_TESTS=1 python -m pytest tests/ -q    # 日常开发用
python -m pytest tests/test_video_qa.py -v       # embedding 测试 (需隔离)

# 评估
python scripts/eval_set.py     # 46 题评测集（尺子）；200 条体检见 scripts/eval_messages_200*.py

# 打包 (详见 .claude/skills/packaging.md)
python scripts/build.py                           # PyInstaller → dist/WeComBot/

# 运行
python main.py
```

环境变量

- DEEPSEEK_API_KEY 在 .env 中 (从 .env.example 复制)
- HF_HUB_OFFLINE=1 在 main.py 中设置 (避免 BGE 模型网络验证阻塞)
- TCL_LIBRARY/TK_LIBRARY 在 启动.bat 中设置 (PyInstaller tkinter 需要)

架构红线

- 事件循环: tkinter mainloop + root.after()，禁止 asyncio.sleep()。UI 必须保持响应。
- 发送路径: responder 生成回复 → send_queue.put() → _process_send_queue_tick → _do_send() → _switch_to_wecom_and_back()。禁止直接发送。
- Guard: GuardResult 三态 (pass/block/low_risk)。low_risk + 高分仍可 auto_send。
- Guard 检索兜底: 检索分数 < 0.5 时，pass 降级为 low_risk (LOW_RETRIEVAL_THRESHOLD)。
- 剪贴板: ctypes Win32 API (_set_clipboard in scanner.py)，禁止 PowerShell 子进程。
- 所有 Win32 函数必须设置 restype + argtypes (否则 64 位指针截断)。
- BAT 文件: 必须 CRLF 行尾。LF 会导致 CMD 中文乱码。
- 红点冷却: is_clickable(ry, timeout=15) 再点击。

已知陷阱 → memory/

- PyTorch DLL 冲突 (测试隔离用 SKIP_EMBED_TESTS)
- .pyc 缓存 — .py 改动后旧字节码不更新。行为不对时先清 __pycache__。
- OCR 累积 — 旧文本在视口中和新消息拼在一起。
- PyInstaller: ~3.2GB 包体, BGE 模型已预捆绑, paddle DLLs 需手动打包, tkinter 需 TCL/TK 环境变量。
  详见: .claude/skills/packaging.md

Caveman 模式已激活。输出压缩约 75%。关闭: "normal mode"。

文档: README.md (用户), docs/design.md (架构), docs/bug_report.md (审计历史)
