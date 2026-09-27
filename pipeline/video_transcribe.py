# pipeline/video_transcribe.py
"""Transcribe training videos to text using faster-whisper (local, lighter)."""

import logging
import os

# Use HF mirror for model downloads
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

logger = logging.getLogger(__name__)

_model_cache: dict = {}


def get_model(model_size: str = "medium"):
    """Load faster-whisper model (lazy, cached by size)."""
    if model_size not in _model_cache:
        from faster_whisper import WhisperModel
        logger.info(f"Loading faster-whisper model: {model_size}")
        # Use int8 quantization for speed on CPU
        _model_cache[model_size] = WhisperModel(
            model_size, device="cpu", compute_type="int8"
        )
    return _model_cache[model_size]


def transcribe_video(video_path: str, model_size: str = "medium",
                     language: str = "zh") -> dict:
    """Transcribe a video file to text.

    Returns dict with keys: text, segments.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")

    model = get_model(model_size)
    logger.info(f"Transcribing: {video_path} "
                f"({os.path.getsize(video_path) / 1024 / 1024:.0f}MB)...")

    segments_result, info = model.transcribe(
        video_path, language=language, beam_size=5
    )

    segments = []
    full_text = ""
    for segment in segments_result:
        segments.append({
            "start": segment.start,
            "end": segment.end,
            "text": segment.text.strip(),
        })
        full_text += segment.text

    logger.info(f"Transcribed {len(segments)} segments from {video_path}")
    return {
        "text": full_text.strip(),
        "segments": segments,
    }


def transcribe_videos_in_directory(video_dir: str,
                                   model_size: str = "medium") -> list[dict]:
    """Batch transcribe all videos in a directory."""
    results = []
    for filename in sorted(os.listdir(video_dir)):
        if filename.lower().endswith(
            (".mp4", ".avi", ".mov", ".mkv", ".wmv", ".flv", ".webm")
        ):
            filepath = os.path.join(video_dir, filename)
            try:
                result = transcribe_video(filepath, model_size)
                result["video_file"] = filename
                results.append(result)
            except Exception as e:
                logger.error(f"Failed to transcribe {filename}: {e}")
    return results
