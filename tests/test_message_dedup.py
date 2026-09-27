"""Tests for customer-aware message deduplication.

Step 1 of architecture refactor: is_message_seen / mark_message_seen
now take a customer_name parameter so that the same message text from
different customers is NOT considered a duplicate.
"""

import time
import pytest

from wxbot.detector._message_detector import MessageDetector


@pytest.fixture()
def detector():
    """Create a MessageDetector with short dedup window for fast tests."""
    det = MessageDetector({"message_dedup_seconds": 300})
    return det


# ── is_message_seen ──────────────────────────────────────────────

class TestIsMessageSeen:
    """is_message_seen(customer_name, text)"""

    def test_same_customer_same_message_skipped(self, detector):
        """Same customer + same message -> skip (True)"""
        detector.mark_message_seen("Alice", "你好请问价格")
        assert detector.is_message_seen("Alice", "你好请问价格") is True

    def test_same_customer_different_message_processed(self, detector):
        """Same customer + different message -> process (False)"""
        detector.mark_message_seen("Alice", "你好请问价格")
        assert detector.is_message_seen("Alice", "什么时候发货") is False

    def test_different_customer_same_message_processed(self, detector):
        """Different customer + same message -> process (False)"""
        detector.mark_message_seen("Alice", "你好请问价格")
        assert detector.is_message_seen("Bob", "你好请问价格") is False

    def test_same_customer_substring_skipped(self, detector):
        """Same customer + similar message (substring) with comparable length
        -> skip (True). Ratio = 8/10 = 0.8 >= 0.6."""
        detector.mark_message_seen("Alice", "请问商品价格是多少")
        assert detector.is_message_seen("Alice", "商品价格是多少") is True

    def test_different_customer_substring_processed(self, detector):
        """Different customer + substring match -> process (False)"""
        detector.mark_message_seen("Alice", "请问这个商品的价格是多少")
        assert detector.is_message_seen("Bob", "商品的价格") is False

    def test_empty_text_processed(self, detector):
        """Empty text -> process (False)"""
        assert detector.is_message_seen("Alice", "") is False

    def test_short_text_processed(self, detector):
        """Text shorter than 3 chars -> process (False)"""
        assert detector.is_message_seen("Alice", "好") is False

    def test_none_text_processed(self, detector):
        """None text -> process (False)"""
        assert detector.is_message_seen("Alice", None) is False

    def test_exact_match_survives_short_window(self, detector):
        """原样同一句：过了 message_dedup_seconds **仍然算回过**。

        这是 2026-09-26 事故的修法：21:45 回过的话，21:59 又被读成"未回复"，
        同一句回了客户两遍。所以精确匹配改用长 TTL（seen_message_ttl_seconds）。
        """
        detector.mark_message_seen("Alice", "你好请问价格")
        for h in detector._seen_messages:
            ts, key = detector._seen_messages[h]
            detector._seen_messages[h] = (ts - 400, key)   # 400s 前
        assert detector.is_message_seen("Alice", "你好请问价格") is True

    def test_dedup_expires_past_long_ttl(self, detector):
        """超过长 TTL（默认 24 小时）才允许再回一次。"""
        detector.mark_message_seen("Alice", "你好请问价格")
        old = detector._seen_ttl + 60
        for h in detector._seen_messages:
            ts, key = detector._seen_messages[h]
            detector._seen_messages[h] = (ts - old, key)
        assert detector.is_message_seen("Alice", "你好请问价格") is False

    def test_new_message_merged_with_old_processed(self, detector):
        """A new (longer) message that contains an old seen message
        but from the same customer — ratio < 0.6 means it's accumulated
        OCR with new content, NOT a duplicate."""
        detector.mark_message_seen("Alice", "商品价格")
        # "请问这个商品价格是多少" contains "商品价格" but ratio=4/10=0.4
        assert detector.is_message_seen("Alice", "请问这个商品价格是多少") is False

    def test_accumulated_ocr_with_new_question_not_deduped(self, detector):
        """OCR accumulates all visible bubbles. When customer sends a NEW
        question, the OCR text contains the old seen text as substring.
        If lengths differ greatly (ratio < 0.6), it's NOT a duplicate."""
        detector.mark_message_seen("Will@微信", "课程多少钱？")
        # Customer sends a new question; OCR sees accumulated text
        new_text = "抖音账号检测在哪块儿？ 课程多少钱？"
        assert detector.is_message_seen("Will@微信", new_text) is False

    def test_similar_length_substring_still_deduped(self, detector):
        """Two messages of similar length that are substrings of each other
        should still be deduped (ratio >= 0.6)."""
        detector.mark_message_seen("Alice", "课程多少钱")
        # Slight variation, similar length
        assert detector.is_message_seen("Alice", "课程多少钱？") is True
