# tests/test_dedup_window.py
"""「客户又问一遍同一句话，要能正常回答」。

2026-10-07 店主："为什么现在不会回消息来了"。查日志：

    18:06:16  收到「索尼zve10有吗」→ 记进"已见"
    18:07:xx  店主在待人工页人工回了答案
    18:08:43  客户又发「索尼zve10有吗」（147 秒后）
              → 「气泡都已回复过（1 条），跳过」
    18:09:55  再发一次 → 同样跳过

而那个气泡**结构上明明是未回复的**（在最新蓝泡之下）—— 是**文字去重**把它盖住了。
两个窗口都设得过长：
  · `unreplied_dedup_seconds` = 600（精确哈希）
  · `message_dedup_seconds`   = 300（模糊子串，默认值、没进配置）
147 秒 < 600 被精确那层拦住；就算降了精确那层，147 < 300 还会被模糊那层拦住
—— 所以**必须两个一起降**。都降到 120。

不动的那一层：`seen_message_ttl_seconds` = 86400（「已经有人回过」的长记忆）。
它管的是"这条气泡上面已经有蓝泡了"，用来防止重启/重排后把旧气泡再回一遍 ——
跟"客户又发了一遍"是两件事，动了会重新引入重复回复。
"""
import time

import pytest

from wxbot.detector import MessageDetector

CFG = {"state_dir": None}


@pytest.fixture
def det(tmp_path):
    d = MessageDetector({"state_dir": str(tmp_path)})
    return d


def _plant(det, customer: str, text: str, age_sec: float):
    """往"已见"里塞一条 `age_sec` 秒前的记录（直接造时间，不用等）。"""
    import hashlib
    key = f"{customer}:{text}"
    h = hashlib.md5(key.encode()).hexdigest()
    det._seen_messages[h] = (time.time() - age_sec, key)
    return h


CUST = "客户A@微信"


def test_recent_message_is_still_deduped(det):
    """刚处理过的（30 秒前）仍然算「回过」—— 防同一气泡被反复处理。"""
    _plant(det, CUST, "有货吗", 30)
    assert det.is_message_seen(CUST, "有货吗", long_term=False) is True


def test_reask_after_two_minutes_is_not_deduped(det):
    """★ 核心：147 秒后客户又问同一句话，**必须**能正常回答。

    这是店主实际踩到的场景（600 秒窗口把它吞了）。
    """
    _plant(det, CUST, "索尼zve10有吗", 147)
    assert det.is_message_seen(CUST, "索尼zve10有吗", long_term=False) is False


def test_fuzzy_layer_does_not_swallow_the_reask(det):
    """模糊那层也不能吞：只降到精确那层是不够的（147 < 300 仍会被拦）。"""
    _plant(det, CUST, "索尼zve10有吗", 147)
    # 模拟 OCR 多读进一个字（真实日志里出现过 "发送 索尼zve10有吗"）
    assert det.is_message_seen(CUST, "发送 索尼zve10有吗", long_term=False) is False


def test_fuzzy_still_catches_ocr_variants_immediately(det):
    """但 OCR 抖动（几秒内读到子串）仍然要拦住 —— 否则同一气泡回两遍。"""
    _plant(det, CUST, "我需要索尼相机，你们店有哪些型号", 2)
    assert det.is_message_seen(
        CUST, "发送 我需要索尼相机，你们店有哪些型号", long_term=False) is True


def test_long_memory_untouched(det):
    """「已经有人回过」的长记忆**不许动**（24h）—— 那是防重复回复的另一层。"""
    import hashlib
    # 注意：`is_message_seen` 对 3 字以下直接返回 False，所以别用"你好"测
    text = "有货吗亲"
    key = f"{CUST}:{text}"
    h = hashlib.md5(key.encode()).hexdigest()
    det._replied_seen[h] = (time.time() - 3600, key)      # 一小时前回过的
    assert det.is_message_seen(CUST, text) is True, \
        "长记忆放这里会重新引入重复回复"
    assert det.is_message_seen(CUST, text, long_term=True) is True
    # 就算短窗口那边早过期了，长记忆也照样认得
    assert det.is_message_seen(CUST, text, long_term=False) is True


def test_other_customer_is_not_affected(det):
    """去重按客户分开：别的客户发同一句话不受影响。"""
    _plant(det, "客户B@微信", "有货吗", 30)
    assert det.is_message_seen(CUST, "有货吗", long_term=False) is False


def test_config_values_are_in_the_short_band():
    """配置里这两个值必须是**短窗口**（分钟级），不能又被人改回 600/300。

    `message_dedup_seconds` 原来没进配置（代码默认 300），现在显式写进去，
    这样它才跟 `unreplied_dedup_seconds` 一起被 review 到。
    """
    import json
    from pathlib import Path
    cfg = json.loads((Path(__file__).resolve().parent.parent / "config.json")
                     .read_text(encoding="utf-8"))
    assert cfg["unreplied_dedup_seconds"] <= 180, "去重窗口超过 3 分钟会漏答"
    assert cfg["message_dedup_seconds"] <= 180
    # 长记忆仍然是 24h（别顺手把它也降了）
    assert cfg["seen_message_ttl_seconds"] >= 3600
