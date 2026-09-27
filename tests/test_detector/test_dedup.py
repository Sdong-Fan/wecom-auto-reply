"""Tests for dedup module."""
import time
from wxbot.detector.dedup import ImageHashCache, ClickCooldown, MessageDedup, ReplyCooldown


def test_image_hash_cache_seen():
    cache = ImageHashCache(max_size=10)
    data = b"test_image_bytes"
    assert cache.is_seen(data) is False  # first time
    assert cache.is_seen(data) is True   # second time


def test_click_cooldown():
    cc = ClickCooldown(cooldown_seconds=0.1)
    assert cc.is_clickable(100) is True
    cc.mark_clicked(100)
    assert cc.is_clickable(100) is False


def test_message_dedup_exact():
    md = MessageDedup(ttl_seconds=10)
    assert md.is_seen("你好，这个课程多少钱？") is False
    assert md.is_seen("你好，这个课程多少钱？") is True  # exact dup


def test_message_dedup_fuzzy():
    md = MessageDedup(ttl_seconds=10)
    md.mark_seen("这个课程多少钱？有什么内容？")
    # fuzzy: shorter version appears inside longer
    assert md.is_seen("这个课程多少钱？") is True


def test_message_dedup_too_short():
    md = MessageDedup(ttl_seconds=10)
    assert md.is_seen("嗯") is True  # too short, treated as seen
    assert md.is_seen("  ") is True


def test_reply_cooldown():
    rc = ReplyCooldown(cooldown_seconds=0.1)
    assert rc.is_in_cooldown() is False
    rc.mark_replied()
    assert rc.is_in_cooldown() is True
    time.sleep(0.11)
    assert rc.is_in_cooldown() is False


# ── Bug fix: new messages containing old message as substring ────────────

def test_new_longer_message_not_falsely_deduped():
    """When customer sends multiple messages that OCR concatenates,
    the new (longer) message should NOT be deduped just because it
    contains the old message as a substring.

    Bug: customer sends "课程多少钱" (auto-replied), then later sends
    "抖音账号检测在哪里？ 能定制开发吗? 怎么投诉？ 课程多少钱？"
    The new message has 3 NEW questions but was falsely deduped because
    "课程多少钱" appeared as a substring.
    """
    md = MessageDedup(ttl_seconds=300)
    # Old message was processed
    md.mark_seen("课程多少钱？")
    # New message contains old + new questions — should NOT be deduped
    new_msg = "抖音账号检测在哪里？ 能定制开发吗? 怎么投诉？ 课程多少钱？"
    assert md.is_seen(new_msg) is False, \
        "New message with additional content should not be falsely deduped"


def test_shorter_subset_of_seen_message_is_deduped():
    """If the new message is a shorter subset of a seen message,
    it should still be deduped (customer might have re-sent part of it)."""
    md = MessageDedup(ttl_seconds=300)
    md.mark_seen("抖音账号检测在哪里？ 能定制开发吗? 怎么投诉？ 课程多少钱？")
    # Shorter subset should be deduped
    assert md.is_seen("课程多少钱？") is True


def test_exact_same_message_is_deduped():
    """Exact same message should always be deduped."""
    md = MessageDedup(ttl_seconds=300)
    md.mark_seen("课程多少钱？")
    assert md.is_seen("课程多少钱？") is True
