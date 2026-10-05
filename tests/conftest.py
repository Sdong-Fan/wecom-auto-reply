"""测试全局配置。

★ 最重要的一件事：**把主程序的日志重定向到临时目录**。

为什么：`tests/` 里很多用例 `import main`，而 `main.py` 在**模块顶层**就建立了
日志处理器（`logging.basicConfig(handlers=[FileHandler(logs/monitor.log)])`）。
于是跑一次测试就会往**真实的** `logs/monitor.log` 里写进一堆东西：

    签名校验不通过 —— Token 填错了吧？
    发送失败（-1: system busy），1.6s 后重试
    非文本消息（wmAAA）: 图片、语音 ×2 → 已应答并转人工

这些是测试夹具，不是线上现象。但排查现场时它们和真实日志混在一起 ——
2026-10-05 就被误导过一次（把测试的签名校验失败当成了程序在跑 API 通道）。

做法：**在导入任何测试/被测模块之前**设置 `WECOM_LOG_PATH` 指向临时文件。
`main.py` 的 `LOG_PATH` 认这个环境变量，所以 conftest 先跑就能截住。
"""

import os
import tempfile
from pathlib import Path

_LOG_DIR = Path(tempfile.mkdtemp(prefix="wecom_test_logs_"))
os.environ["WECOM_LOG_PATH"] = str(_LOG_DIR / "monitor.log")
