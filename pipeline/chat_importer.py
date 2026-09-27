# pipeline/chat_importer.py
"""Parse WeCom chat export .txt files into structured Q&A pairs.

Supports two formats:
- Standard: "YYYY-MM-DD HH:MM:SS 角色-姓名"
- Forwarded: "姓名@微信@微信联系人 MM/DD HH:MM:SS"
"""

import re
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 标准导出格式: "2026-06-09 14:49:03 客户-Will"
TIMESTAMP_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+(.+)$")

# 转发格式: "Will@微信@微信联系人 6/9 14:49:03" 或 "朱廷帅 6/9 14:49:13"
FORWARDED_LINE = re.compile(r"^(.+?)\s+(\d{1,2}/\d{1,2}\s+\d{2}:\d{2}:\d{2})$")

NOISE_PATTERNS = [
    r"^(你好|您好|在吗|哈喽|hi|hello)[\s!！。.,，]*$",
    r"^(好的|OK|ok|收到|明白|嗯嗯|哦哦|谢谢|感谢)[\s!！。.,，]*$",
    r"^[\s!！。.,，?!？!！]*$",
    r"^\[图片\]$", r"^\[语音\]$", r"^\[视频\]$", r"^\[文件\]$",
    r"^\{.*\}$",
]

MIN_QUESTION_LEN = 4
MIN_ANSWER_LEN = 8


@dataclass
class QAPair:
    question: str
    answer: str
    salesperson: str = ""
    date: str = ""
    product: str = ""


def _is_noise(text: str) -> bool:
    text = text.strip()
    for pattern in NOISE_PATTERNS:
        if re.match(pattern, text, re.IGNORECASE):
            return True
    return False


def _extract_name(speaker_raw: str) -> tuple[str, str]:
    parts = speaker_raw.split("-", 1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return "", speaker_raw.strip()


def _is_customer_forwarded(speaker_raw: str) -> bool:
    """转发格式中判断是否为客户（含 @微信 标识）。"""
    return "@微信" in speaker_raw


def _strip_at_suffix(speaker_raw: str) -> str:
    """去掉 @微信@微信联系人 等后缀，返回纯姓名。"""
    return re.sub(r"@微信.*$", "", speaker_raw).strip()


def _normalize_date(date_part: str, fallback_year: str = "2026") -> str:
    """将 MM/DD HH:MM:SS 标准化为 YYYY-MM-DD HH:MM:SS。"""
    m = re.match(r"(\d{1,2})/(\d{1,2})\s+(\d{2}:\d{2}:\d{2})", date_part)
    if m:
        month, day, time = m.group(1), m.group(2), m.group(3)
        return f"{fallback_year}-{int(month):02d}-{int(day):02d} {time}"
    return date_part


def _detect_format(lines: list[str]) -> str:
    """检测聊天记录格式: 'standard' 或 'forwarded'。"""
    for line in lines[:20]:
        line = line.strip()
        if not line:
            continue
        if TIMESTAMP_LINE.match(line):
            return "standard"
        if FORWARDED_LINE.match(line):
            return "forwarded"
    return "standard"


def _parse_to_messages(lines: list[str], fmt: str) -> list[dict]:
    """将原始行解析为消息列表。"""
    messages = []
    current_timestamp = ""
    current_speaker = ""
    current_text: list[str] = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        match = None
        if fmt == "standard":
            match = TIMESTAMP_LINE.match(line)
            if match:
                current_timestamp = match.group(1)
                current_speaker = match.group(2)
        else:
            match = FORWARDED_LINE.match(line)
            if match:
                current_timestamp = _normalize_date(match.group(2))
                raw_speaker = match.group(1)
                role = "客户" if _is_customer_forwarded(raw_speaker) else "销售"
                name = _strip_at_suffix(raw_speaker)
                current_speaker = f"{role}-{name}"

        if match:
            if current_text and messages:
                messages[-1]["text"] = "\n".join(current_text)
            messages.append({
                "timestamp": current_timestamp,
                "speaker": current_speaker,
                "text": "",
            })
            current_text = []
        else:
            current_text.append(line)

    if current_text and messages:
        messages[-1]["text"] = "\n".join(current_text)

    return [m for m in messages if m["text"]]


def parse_chat_file(filepath: str) -> list[QAPair]:
    """Parse a WeCom chat export file into Q&A pairs.

    Supports standard export format and forwarded copy-paste format.
    """
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    fmt = _detect_format(lines)
    logger.info(f"Detected format: {fmt} for {filepath}")
    messages = _parse_to_messages(lines, fmt)

    pairs = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        role, name = _extract_name(msg["speaker"])

        if role == "客户" and not _is_noise(msg["text"]) \
                and len(msg["text"]) >= MIN_QUESTION_LEN:
            question = msg["text"]
            for j in range(i + 1, min(i + 5, len(messages))):
                next_msg = messages[j]
                next_role, next_name = _extract_name(next_msg["speaker"])
                if next_role == "销售" and not _is_noise(next_msg["text"]) \
                        and len(next_msg["text"]) >= MIN_ANSWER_LEN:
                    pairs.append(QAPair(
                        question=question,
                        answer=next_msg["text"],
                        salesperson=next_name,
                        date=msg["timestamp"][:10],
                    ))
                    i = j
                    break
        i += 1

    logger.info(f"Extracted {len(pairs)} QA pairs from {filepath}")
    return pairs
