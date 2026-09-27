"""OCR wrapper with WeChat UI artifact filtering.

Two backends, chosen by config key ``backend``:

* ``"rapidocr"`` (默认) —— RapidOCR / ONNX Runtime。没有 paddle 运行时，
  不会碰 torch 的 DLL，也没有 paddle 那句偶发的
  ``RuntimeError: could not execute a primitive``（本机实测每半小时崩数次，
  一崩就丢一轮扫描）。实测同一张真实企微截图：会话列表 28 行 / 307ms（热），
  聊天区 8 行 / 356ms（热），文本与 PaddleOCR 一致甚至更干净
  （Paddle 会把"星期二"误拆成"星"+"1"）。日志干净，不像 paddle 会往 stdout
  吐整段 Namespace(...) DEBUG。
* ``"paddle"`` —— 原有实现，保留以便对比与回退。
  **注意：paddle 必须先 import torch**，否则报 torch/lib/shm.dll 加载失败。

Provides extract_text() as a standalone function that can be called
without a MessageDetector instance.  Maintains a module-level engine
cache for performance (models load once).
"""
import logging
import re
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image, ImageEnhance

logger = logging.getLogger(__name__)

# ── module-level engine cache ──────────────────────────────────────────────
_ocr_instance = None
_ocr_backend: Optional[str] = None


class _RapidBackend:
    """RapidOCR(ONNX) → 统一的 [(text, conf)] 输出。"""

    name = "rapidocr"

    def __init__(self):
        from rapidocr_onnxruntime import RapidOCR
        logger.info("Loading RapidOCR (onnxruntime)...")
        # det_limit_type 默认 'min' 会把小图放大到短边 736，裁小反而更慢 → 必须 'max'
        self._engine = RapidOCR(intra_op_num_threads=4,
                                det_limit_type="max", det_limit_side_len=4000)
        logger.info("RapidOCR ready")

    def run(self, arr: np.ndarray) -> List[Tuple[str, float]]:
        res, _ = self._engine(arr, use_cls=False)
        return [(r[1], float(r[2])) for r in (res or [])]


class _PaddleBackend:
    """PaddleOCR → 统一的 [(text, conf)] 输出。"""

    name = "paddle"

    def __init__(self, lang: str = "ch"):
        # torch 必须先于 paddle 导入，否则 paddle 的 DLL 会和 torch 抢
        try:
            import torch  # noqa: F401
        except Exception:
            pass
        from paddleocr import PaddleOCR
        logger.info("Loading PaddleOCR...")
        self._engine = PaddleOCR(lang=lang, show_log=False)
        logger.info("PaddleOCR ready")

    def run(self, arr: np.ndarray) -> List[Tuple[str, float]]:
        result = self._engine.ocr(arr)
        if not result or not result[0]:
            return []
        return [(r[1][0], float(r[1][1])) for r in result[0]]


def _get_engine(backend: str = "rapidocr", lang: str = "ch"):
    """Lazy-load and cache one OCR backend (module-level singleton)."""
    global _ocr_instance, _ocr_backend
    wanted = (backend or "rapidocr").lower()
    if _ocr_instance is not None and _ocr_backend == wanted:
        return _ocr_instance
    if _ocr_instance is not None and _ocr_backend != wanted:
        logger.info(f"OCR 后端切换: {_ocr_backend} → {wanted}")
        _ocr_instance = None
    try:
        _ocr_instance = _RapidBackend() if wanted == "rapidocr" else _PaddleBackend(lang=lang)
    except Exception as e:
        # 主后端起不来就退到另一个，别让整个程序因为引擎缺失起不来
        fallback = "paddle" if wanted == "rapidocr" else "rapidocr"
        logger.warning(f"{wanted} 初始化失败（{type(e).__name__}: {e}），回退到 {fallback}")
        _ocr_instance = _RapidBackend() if fallback == "rapidocr" else _PaddleBackend(lang=lang)
        wanted = fallback
    _ocr_backend = wanted
    return _ocr_instance


# ── UI artifact patterns ───────────────────────────────────────────────────

_DATE_ARTIFACT_RE = re.compile(
    r'^(周[一二三四五六日]|星期[一二三四五六日]|期[一二三四五六日])\s*'
)

_UI_ARTIFACTS = [
    "√转换完成", "✓转换完成", "转换完成",
    "√ 转换完成", "✓ 转换完成",
]


def strip_ocr_artifacts(text: str) -> str:
    """Remove WeChat UI labels misread by OCR (dates, voice-to-text markers)."""
    text = _DATE_ARTIFACT_RE.sub('', text, count=1)
    for artifact in _UI_ARTIFACTS:
        text = text.replace(artifact, "")
    return re.sub(r'\s{2,}', ' ', text).strip()


# ── config defaults ────────────────────────────────────────────────────────

OCR_DEFAULTS = {
    "backend": "rapidocr",
    "lang": "ch",
    "min_image_height": 50,
    "enhance_contrast": False,
    "contrast_factor": 2.0,
    "timestamp_filter_pattern": r'^[\d:;.\-\s]+$',
    "default_confidence": 0.4,
}


# ── main API ───────────────────────────────────────────────────────────────

def extract_text(
    img: Image.Image,
    config: dict = None,
) -> str:
    """Run OCR on an image and return cleaned text.

    Args:
        img: PIL Image (RGB) to OCR.
        config: Optional dict overriding ``OCR_DEFAULTS`` keys.

    Returns:
        Cleaned text string, or ``""`` if no text found.
    """
    cfg = dict(OCR_DEFAULTS)
    if config:
        cfg.update(config)

    min_conf = cfg["default_confidence"]
    backend = cfg.get("backend") or "rapidocr"
    ocr_lang = cfg["lang"]
    min_h = cfg["min_image_height"]
    enhance = cfg["enhance_contrast"]
    contrast = cfg["contrast_factor"]
    ts_pattern = cfg["timestamp_filter_pattern"]

    w, h = img.size
    # 退化裁剪（宽或高为 0）会让 OCR 直接抛异常：
    #   rapidocr: ResizeImgError: resize_w or resize_h is less than or equal to 0
    # 实测踩过：行偏移导致裁出空图 → 整个扫描周期异常退出。
    # 这里当"没文字"处理，绝不把异常抛给调用方。
    if w <= 0 or h <= 0:
        return ""
    if h < min_h:
        target_h = min_h
        img = img.resize((max(1, int(w * target_h / h)), target_h), Image.LANCZOS)
    if enhance:
        img = ImageEnhance.Contrast(img).enhance(contrast)

    arr = np.array(img.convert("RGB"))
    engine = _get_engine(backend=backend, lang=ocr_lang)
    lines = engine.run(arr)
    if not lines:
        return ""

    ts_re = re.compile(ts_pattern)
    texts = []
    for text, conf in lines:
        if conf < min_conf:
            continue
        t = str(text).strip()
        if ts_re.match(t):
            continue
        if len(t) <= 1 and not re.search(r'[一-鿿]', t):
            continue
        texts.append(t)

    combined = " ".join(texts)
    return strip_ocr_artifacts(combined)


def clear_ocr_cache():
    """Reset the global OCR engine (useful for testing)."""
    global _ocr_instance, _ocr_backend
    _ocr_instance = None
    _ocr_backend = None
