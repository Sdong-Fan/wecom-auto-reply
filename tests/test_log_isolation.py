# tests/test_log_isolation.py
"""跑测试**不许**写生产日志 `logs/monitor.log`。

起因（2026-10-05）：`tests/` 里很多用例 `import main`，而 `main.py` 在模块顶层
就建好了日志处理器。于是每跑一次测试就往生产日志里灌：

    签名校验不通过 —— Token 填错了吧？
    发送失败（-1: system busy），1.6s 后重试
    非文本消息（wmAAA）: 图片、语音 ×2 → 已应答并转人工

这些是测试夹具，不是线上现象。排查现场时它们和真实日志混在一起，
2026-10-05 就被误导过一次（把测试的"签名校验失败"当成了程序在跑 API 通道，
白查了一轮）。所以 `tests/conftest.py` 在导入任何东西之前把 `WECOM_LOG_PATH`
指向临时文件 —— 这两条测试保证那个重定向一直有效。
"""
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_conftest_redirects_log_path():
    raw = os.environ.get("WECOM_LOG_PATH", "")
    assert raw, "tests/conftest.py 必须设置 WECOM_LOG_PATH"
    path = Path(raw).resolve()
    # 必须在系统临时目录里，**不能**落在仓库的 logs/ 下
    assert str(path).startswith(str(Path(tempfile.gettempdir()).resolve())), \
        "测试日志要落在临时目录，实际是 %s" % path
    assert ROOT / "logs" not in path.parents, "别写进仓库的 logs/"


def test_main_honours_the_env_var():
    """`main.py` 的 LOG_PATH 要认这个环境变量 —— 否则 conftest 设了也没用。"""
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    assert 'os.environ.get("WECOM_LOG_PATH"' in src
    # 而且是**模块顶层**就算好（处理器是模块级建的，函数里读环境变量来不及）
    top = src.split("def ", 1)[0]
    assert "WECOM_LOG_PATH" in top, "LOG_PATH 必须在模块顶层确定"
