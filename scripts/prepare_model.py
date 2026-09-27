#!/usr/bin/env python
"""准备本地嵌入模型（bge-small-zh-v1.5）到 bge_model/。

**为什么需要这一步**：`bge_model/` 是 gitignore 的（~92MB，不适合进仓库），
而 `rag/embed_query.py` 在源码模式下会优先用 `bge_model/`；如果既没有本地目录、
HF 缓存里也没有，程序一启动就会在加载嵌入模型时报错。

用法：
    python scripts/prepare_model.py            # 用镜像（国内推荐）
    python scripts/prepare_model.py --official # 直连 HuggingFace（需能访问）

做完之后 `bge_model/model.safetensors` 应该存在（约 92MB）。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "bge_model"
MODEL = "BAAI/bge-small-zh-v1.5"


def main() -> int:
    ap = argparse.ArgumentParser(description="下载本地嵌入模型到 bge_model/")
    ap.add_argument("--official", action="store_true",
                    help="直连 huggingface.co（默认走 hf-mirror.com 镜像）")
    a = ap.parse_args()

    if (TARGET / "model.safetensors").exists():
        size = (TARGET / "model.safetensors").stat().st_size / 1024 / 1024
        print(f"[OK] 模型已存在：{TARGET}（model.safetensors {size:.0f} MB），不用重复下载")
        return 0

    if a.official:
        os.environ["HF_ENDPOINT"] = "https://huggingface.co"
    else:
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    # 这一步必须联网，显式关掉离线开关（rag/embed_query.py 默认是开的）
    os.environ["HF_HUB_OFFLINE"] = "0"
    os.environ["TRANSFORMERS_OFFLINE"] = "0"

    print(f"[1/2] 从 {os.environ['HF_ENDPOINT']} 下载 {MODEL} …（约 92MB）")
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("[错误] 缺少 huggingface_hub，请先：pip install -r requirements.txt")
        return 1

    try:
        snapshot_download(MODEL, local_dir=str(TARGET))
    except Exception as e:
        print(f"\n[错误] 下载失败：{e}")
        print("  可尝试：① 直连用 --official ② 检查网络/代理 "
              "③ 手动把模型文件放到 bge_model/（需含 model.safetensors、config.json、"
              "tokenizer 相关文件）")
        return 1

    ok = (TARGET / "model.safetensors").exists()
    print(f"[2/2] {'完成' if ok else '完成但没找到 model.safetensors，请检查目录'}"
          f"：{TARGET}")
    if ok:
        print("      现在可以启动程序了：python main.py")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
