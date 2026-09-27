# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for WeComBot — 企业微信智能客服一键打包."""

import sys
from pathlib import Path
import importlib.metadata
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_data_files

# ── Hidden imports that PyInstaller can't auto-detect ──────────────
# torch and sentence_transformers use dynamic imports
# that PyInstaller's analysis misses.

hiddenimports = [
    # PyTorch + CUDA (CPU-only build, but keep for safety)
    "torch",
    "torch.nn",
    "torch.utils",
    "torch._C",
    # OCR: RapidOCR (onnxruntime) —— 默认后端，替代会偶发
    # "RuntimeError: could not execute a primitive" 的 PaddleOCR
    "rapidocr_onnxruntime",
    "onnxruntime",
    "onnxruntime.capi",
    "onnxruntime.capi._pybind_state",
    # Sentence Transformers (BGE model)
    "sentence_transformers",
    "sentence_transformers.models",
    "transformers",
    "transformers.models.bert",
    # Qdrant
    "qdrant_client",
    "qdrant_client.http",
    # OpenAI (DeepSeek API)
    "openai",
    # Image processing
    "PIL",
    "PIL.Image",
    "PIL.ImageDraw",
    "PIL.ImageFont",
    # Windows automation
    "pynput",
    "pynput.keyboard",
    "pynput.mouse",
    "win32gui",
    "win32con",
    "win32process",
    "win32api",
    "win32clipboard",
    # Screenshot
    "mss",
    "mss.windows",
    # Misc
    "psutil",
    "numpy",
    "dotenv",
    "asyncio",
    "queue",
    "json",
    "logging",
    "dataclasses",
    "ctypes",
]

# ── Data files to include ──────────────────────────────────────────
# These are copied into the bundle root so the exe can find them.
def _dist_info(name: str):
    """Optional dist-info dir; 没装就跳过（别让 spec 直接炸）。"""
    try:
        return Path(importlib.metadata.distribution(name)._path)
    except Exception:
        return None


datas = [
    ("config.json", "."),
    (".env.example", "."),
    # ★ 绝对不能写 ("data", "data")：那会把 data/context（真实客户聊天记录）、
    #   data/state（待人工队列）、data/qdrant 整个打进 exe —— 等于把店主的
    #   客户数据一起发出去。运行时的 data/ 由 scripts/build.py 组装到
    #   dist/WeComBot/data/（程序按 CWD 相对路径读写）。
    ("prompts", "prompts"),                           # 提示词（用户要求保留）
    ("profiles", "profiles"),                         # 窗口标定（wxbot/profile.py 按 __file__ 找）
    ("bge_model", "bge_model"),                       # BGE model (~92MB, offline)
]

# RapidOCR 自带 PP-OCRv4 onnx 模型（det 4.5MB / rec 10.4MB / cls 0.6MB），
# 必须打进包，否则打包版要联网下载模型。
datas += collect_data_files("rapidocr_onnxruntime")

_imageio = _dist_info("imageio")
if _imageio is not None:
    datas.append((str(_imageio), _imageio.name))     # imageio metadata (imgaug needs at import)

# onnxruntime 的原生 DLL（onnxruntime.dll 等）
binaries = collect_dynamic_libs("onnxruntime")

# ── Exclude dirs that should NOT be bundled ─────────────────────────
excluded_dirs = [
    "tests",
    "docs",
    "eval",
    ".git",
    "__pycache__",
    "scripts",
    "logs",
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excluded_dirs,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="启动",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,        # Windows GUI app — no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="WeComBot",
)
