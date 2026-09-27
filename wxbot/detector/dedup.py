"""Message deduplication and cooldown tracking.

Extracted from MessageDetector. Provides standalone functions for:
- Image hash dedup (col2 screenshot seen-before check)
- Message text dedup (MD5 + fuzzy substring, with TTL)
- Click cooldown per Y position
- Reply cooldown between sends
"""
import hashlib
import time
from typing import Dict, List, Optional


# ── defaults ───────────────────────────────────────────────────────────────

DEDUP_DEFAULTS = {
    "col2_hash_cache_size": 50,
    "click_cooldown_seconds": 60,
    "reply_cooldown_seconds": 10,
    "message_dedup_seconds": 300,
}


# ── state objects (caller owns lifecycle; pass in) ─────────────────────────


class ImageHashCache:
    """Track seen column-2 screenshot hashes to skip unchanged frames."""

    def __init__(self, max_size: int = 50):
        self._seen: set = set()
        self._max_size = max_size

    def is_seen(self, img_bytes: bytes) -> bool:
        h = hashlib.md5(img_bytes).hexdigest()
        if h in self._seen:
            return True
        self._seen.add(h)
        if len(self._seen) > self._max_size:
            self._seen.clear()
        return False


class ClickCooldown:
    """Prevent re-clicking the same Y position within a timeout window."""

    def __init__(self, cooldown_seconds: float = 60.0):
        self._tried: Dict[int, float] = {}
        self._cooldown = cooldown_seconds

    def is_clickable(self, y: int, timeout: float = None) -> bool:
        t = timeout if timeout is not None else self._cooldown
        last = self._tried.get(y, 0)
        return (time.monotonic() - last) > t

    def mark_clicked(self, y: int):
        self._tried[y] = time.monotonic()


class MessageDedup:
    """Deduplicate OCR-extracted messages by content hash and fuzzy substring."""

    def __init__(self, ttl_seconds: float = 300.0):
        self._seen: Dict[str, tuple[float, str]] = {}  # hash → (timestamp, text)
        self._ttl = ttl_seconds

    def is_seen(self, text: str) -> bool:
        """Check if message text has been seen recently.

        Uses MD5 exact match first, then fuzzy substring match against
        recently seen messages.  Expired entries are cleaned on each check.
        """
        if not text or len(text.strip()) < 2:
            return True  # too short to be meaningful

        now = time.monotonic()
        h = hashlib.md5(text.encode("utf-8")).hexdigest()

        # Clean expired
        expired = [k for k, (ts, _) in self._seen.items() if now - ts > self._ttl]
        for k in expired:
            del self._seen[k]

        # Exact match
        if h in self._seen:
            return True

        # Fuzzy substring: only dedup when new message is a SUBSET of a seen
        # message (shorter). When new message is LONGER, it contains new
        # content (e.g., OCR concatenated multiple messages) — don't dedup.
        for _, (_, original) in self._seen.items():
            if len(text) >= 3 and len(original) >= 3:
                if text in original:
                    # New message is a substring of a seen message — likely duplicate
                    self._seen[h] = (now, text)
                    return True

        # Not seen yet — mark it now
        self._seen[h] = (now, text)
        return False

    def mark_seen(self, text: str):
        h = hashlib.md5(text.encode("utf-8")).hexdigest()
        self._seen[h] = (time.monotonic(), text)


class ReplyCooldown:
    """Enforce minimum interval between auto-replies."""

    def __init__(self, cooldown_seconds: float = 10.0):
        self._last_reply = 0.0
        self._cooldown = cooldown_seconds

    def is_in_cooldown(self) -> bool:
        return (time.monotonic() - self._last_reply) < self._cooldown

    def mark_replied(self):
        self._last_reply = time.monotonic()
