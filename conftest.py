"""pytest 全局引导 — 必须在任何测试模块导入 paddle 之前把 torch 加载进来。

背景（项目已知陷阱）::

    paddleocr
      -> ppocr.data.imaug.iaa_augment
        -> albumentations
          -> albumentations.pytorch   # 无条件导入
            -> torch

torch 的 DLL 一旦在 paddle 之后加载，就会抛出::

    OSError: [WinError 127] Error loading "...torch\\lib\\shm.dll"

pytest 收集阶段会直接 import wxbot.detector（含 PaddleOCR），此时若
没有先加载 torch，就会出现「2 errors during collection」而整套测试跑不起来。

main.py 用同样的顺序规避（见 main.py 顶部 "torch 必须在 paddle 之前导入"）。
这里补上测试侧的等价处理，使 ``pytest tests/ -q`` 可独立运行。

如需临时关闭此引导（例如排查导入顺序问题），设置环境变量
``NO_TORCH_PRELOAD=1``。
"""

import os

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

if not os.environ.get("NO_TORCH_PRELOAD"):
    try:
        import torch  # noqa: F401  —— 只为提前加载 DLL，顺序敏感
    except Exception:  # pragma: no cover
        # 环境未装 torch 时不应阻断纯逻辑测试
        pass


def pytest_configure(config):
    """注册自定义标记，免得 pytest 报 Unknown pytest.mark。"""
    config.addinivalue_line(
        "markers",
        "slow: 会真的把程序拉起来（约 25 秒），默认不跑；"
        "设 RUN_STARTUP_SMOKE=1 才启用")
