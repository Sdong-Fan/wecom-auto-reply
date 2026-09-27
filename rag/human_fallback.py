"""Human fallback queue and conversation context management.

PendingQueue — per-customer newest-only dedup, 24h TTL, expiry detection.
ContextStore — per-customer conversation history persistence (JSONL).
"""

import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

TTL_SECONDS = 24 * 3600  # 24 hours


def name_candidates(name: str) -> set:
    """一个会话名可能的所有"本人名字"写法。

    OCR 读出来的会话名经常是 **「名字 + 消息预览」**：

        客户A@微信             ← 干净
        客户A@微信 帮您问下算好没，稍等。   ← 带预览
        客户A 这一串我算不明白了   ← @微信 没读出来，后缀切不断

    `clean_name_text` 靠 `@微信` 后缀切断预览，后缀没读出来时整串都会留下。
    这里把三种可能的切法都列出来，用**完全相等**去比对（不做模糊匹配）——
    宁可漏归一，也不能把两个人合成一个：合并的后果是回复发错人。
    """
    s = (name or "").strip()
    if not s:
        return set()
    out = {s}
    core = re.split(r"[@（(]", s)[0].strip()          # 去掉 @微信 之后的尾巴
    if len(core) >= 2:
        out.add(core)
    head = re.split(r"[\s\u3000]", s)[0].strip()      # 第一个空格之前
    if len(head) >= 2:
        out.add(head)
    return out


def same_person(a: str, b: str) -> bool:
    """两个 OCR 名字是不是同一个人（严格：名字写法有交集）。"""
    if not a or not b:
        return False
    if a == b:
        return True
    ca, cb = name_candidates(a), name_candidates(b)
    long_enough = {x for x in ca if len(x) >= 2} & {x for x in cb if len(x) >= 2}
    return bool(long_enough)


def better_name(a: str, b: str) -> str:
    """两个写法留更干净的那个（更短的一般是不带预览的那个）。"""
    if not a:
        return b
    if not b:
        return a
    return a if len(a) <= len(b) else b


@dataclass
class PendingItem:
    """One pending human-confirmation item.

    **一条消息一行**，不是一个人一行。同一个人连问两句是**两件事**，
    要人工分别处理（"有打算在鼓浪屿开店吗" 和 "鼓浪屿房租不便宜" 是两回事）。
    """
    timestamp: float          # when it entered the queue
    customer_name: str
    customer_message: str
    ai_reply: str
    confidence: float         # retrieval cosine score
    guard_decision: str       # "low_risk" or from guard
    status: str = "pending"   # "pending" | "expired"
    key: str = ""             # 唯一标识：人 + 消息指纹（界面按它定位一行）
    person: str = ""          # 归一后的"这个人"（OCR 名字变体都算同一个）
    seq: int = 0              # 递增序号，用来排序

    def is_expired(self) -> bool:
        return (time.time() - self.timestamp) > TTL_SECONDS


_seq_lock = threading.Lock()
_seq_counter = [0]


def _next_seq() -> int:
    """递增序号。

    **为什么不能只靠 timestamp**：Windows 上 ``time.time()`` 只有约 15ms 精度，
    同一秒内连着的两条消息时间戳可能完全一样，"哪条是最新的"就变成随机的
    （实测：同一客户连着问两句，界面上"最新那条"指错了）。
    """
    with _seq_lock:
        _seq_counter[0] += 1
        return _seq_counter[0]


def _msg_fp(text: str) -> str:
    """消息指纹：只用来判断"是不是同一句话"，归一掉空白和标点。"""
    s = re.sub(r"[\s\u3000]+", "", text or "")
    # 注意用全角引号：ASCII 双引号写在 "..." 里会把字符串**断开**成两段拼接，
    # 第二段不是 raw 串，`\-` 就会变成无效转义（SyntaxWarning）
    s = re.sub(r"[，。！？、；：“”‘’（）()~!?.,;:—…\-]", "", s)
    return hashlib.md5(s.encode("utf-8")).hexdigest()[:10]


class PendingQueue:
    """待人工队列：**按「人 + 消息」排队**，不是按人。

    - 同一个人问了两件不同的事 → 两条，各处理各的
    - 同一条消息重复入队（发送失败重试、OCR 抖动）→ 合成一条，不刷屏
    - 每个人最多留 ``max_per_customer`` 条，超了丢最旧的（防刷屏的保险丝）
    - 超过 24 小时的条目自动过期

    为什么要"按消息"：原来是一个人只留最新一条，第二条一到就把第一条**顶掉**了。
    实测踩过：客户问「有打算在鼓浪屿开店吗」→ 转人工；紧接着问「鼓浪屿房租不便宜」
    → 又转人工，把前一条顶没了。店主只看得到一条，第一条问题**静默消失**。
    """

    # 每个人最多留几条（防刷屏；正常聊天到不了这个数）。
    # 定 10 而不是 5：被挤掉一条问题正是这次要修的毛病，阈值要留足余量。
    DEFAULT_MAX_PER_CUSTOMER = 10

    def __init__(self, persist_path: str = "data/state/pending_queue.json",
                 max_per_customer: int = None):
        self._lock = threading.Lock()
        self._items: dict[str, PendingItem] = {}  # keyed by item.key
        self._cleaned_counts: dict[str, int] = {}   # per-person cleanup count
        self._max_per_customer = int(max_per_customer
                                     or self.DEFAULT_MAX_PER_CUSTOMER)
        self._path = Path(persist_path)
        self._load()

    # ── 归一：OCR 名字变体都算同一个人 ──────────────────────────────

    def _person_of(self, customer_name: str) -> str:
        """把 ``customer_name`` 归一到队列里已有的、同一个人的那个标识。"""
        for it in self._items.values():
            if same_person(customer_name, it.customer_name):
                return it.person or it.customer_name
        return customer_name

    def _cleanest_name(self, person: str, customer_name: str) -> str:
        """同一个人可能有几种 OCR 写法，留最干净的那个（发送要用它对会话）。"""
        best = customer_name
        for it in self._items.values():
            if (it.person or it.customer_name) == person:
                best = better_name(best, it.customer_name)
        return best

    def push(self, customer_name: str, customer_message: str,
             ai_reply: str, confidence: float = 0.0,
             guard_decision: str = "low_risk",
             key_hint: str = "") -> Optional[PendingItem]:
        """Add or replace the pending item for **this message**.

        ``key_hint`` 用来区分"消息文本一样但不是同一条"的情况 ——
        比如客户连发几张图片，文本都是「（客户发来图片）」，靠文本指纹会合成一条，
        传 ``key_hint="nontext:<msgid>"`` 就各自占一行。

        Returns the displaced item if the same message was already queued
        (e.g. a retry), else None.
        """
        person = self._person_of(customer_name)
        name = self._cleanest_name(person, customer_name)
        fp = key_hint or _msg_fp(customer_message)
        key = f"{person}::{fp}"
        new_item = PendingItem(
            timestamp=time.time(),
            customer_name=name,
            customer_message=customer_message,
            ai_reply=ai_reply,
            confidence=confidence,
            guard_decision=guard_decision,
            key=key,
            person=person,
            seq=_next_seq(),
        )
        with self._lock:
            old = self._items.pop(key, None)
            if old is not None:
                self._cleaned_counts[person] = \
                    self._cleaned_counts.get(person, 0) + 1
            self._items[key] = new_item

            # 同一个人名字写法变了 → 他名下所有条目一起换成更干净的名字
            for it in self._items.values():
                if (it.person or it.customer_name) == person:
                    it.customer_name = name

            # 保险丝：一个人攒太多条就丢最旧的（正常聊天到不了）
            mine = sorted((it for it in self._items.values()
                           if (it.person or it.customer_name) == person),
                          key=lambda i: (i.seq, i.timestamp))
            while len(mine) > self._max_per_customer:
                drop = mine.pop(0)
                self._items.pop(drop.key, None)
                self._cleaned_counts[person] = \
                    self._cleaned_counts.get(person, 0) + 1
                logger.info(f"待人工超过 {self._max_per_customer} 条，"
                            f"丢掉最旧的一条: {drop.customer_message[:30]}")
        self._save()
        return old

    def get_all(self) -> list[PendingItem]:
        """Return active (non-expired) pending items, newest first."""
        with self._lock:
            active = []
            expired_keys = []
            for key, item in self._items.items():
                if item.is_expired():
                    expired_keys.append(key)
                else:
                    active.append(item)
            for key in expired_keys:
                del self._items[key]
        if expired_keys:
            self._save()
        active.sort(key=lambda i: (i.seq, i.timestamp), reverse=True)
        return active

    # ── 按 key（界面那一行）取 ──────────────────────────────────────

    def get_key(self, key: str) -> Optional[PendingItem]:
        with self._lock:
            return self._items.get(key)

    def remove_key(self, key: str) -> Optional[PendingItem]:
        with self._lock:
            item = self._items.pop(key, None)
        if item:
            self._save()
        return item

    # ── 按人取（兼容老调用：拿这个人**最新**的一条）────────────────

    def get(self, customer_name: str) -> Optional[PendingItem]:
        """这个人最新的一条待人工。"""
        with self._lock:
            mine = [it for it in self._items.values()
                    if same_person(customer_name, it.customer_name)]
        if not mine:
            return None
        return max(mine, key=lambda i: (i.seq, i.timestamp))

    def get_all_of(self, customer_name: str) -> list:
        """这个人的所有待人工条目（新→旧）。"""
        with self._lock:
            mine = [it for it in self._items.values()
                    if same_person(customer_name, it.customer_name)]
        mine.sort(key=lambda i: (i.seq, i.timestamp), reverse=True)
        return mine

    def remove(self, customer_name: str) -> Optional[PendingItem]:
        """移除这个人最新的一条（兼容老调用；界面走 remove_key）。"""
        item = self.get(customer_name)
        return self.remove_key(item.key) if item else None

    def mark_expired(self, customer_name: str) -> Optional[PendingItem]:
        """Mark this person's items as expired (customer sent newer messages)."""
        with self._lock:
            hit = None
            for it in self._items.values():
                if same_person(customer_name, it.customer_name):
                    it.status = "expired"
                    hit = it
            if hit:
                self._save()
        return hit

    @property
    def count(self) -> int:
        with self._lock:
            return len([i for i in self._items.values() if not i.is_expired()])

    def get_cleanup_count(self, customer_name: str) -> int:
        with self._lock:
            return self._cleaned_counts.get(customer_name, 0)

    def _save(self):
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "items": {
                    key: {
                        "timestamp": item.timestamp,
                        "customer_name": item.customer_name,
                        "customer_message": item.customer_message,
                        "ai_reply": item.ai_reply,
                        "confidence": item.confidence,
                        "guard_decision": item.guard_decision,
                        "status": item.status,
                        "key": item.key,
                        "person": item.person,
                        "seq": item.seq,
                    }
                    for key, item in self._items.items()
                },
                "cleaned_counts": self._cleaned_counts,
            }
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.error(f"Failed to save pending queue: {e}")

    def _load(self):
        if not self._path.exists():
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            now = time.time()
            loaded = 0
            for stored_key, raw in data.get("items", {}).items():
                ts = raw.get("timestamp", 0)
                if now - ts > TTL_SECONDS:
                    continue  # skip expired
                name = raw.get("customer_name", "")
                text = raw.get("customer_message", "")
                # 老文件里 key 就是人名（一人一条的时代）—— 重新按「人+消息」算
                key = raw.get("key") or f"{name}::{_msg_fp(text)}"
                self._items[key] = PendingItem(
                    timestamp=ts,
                    customer_name=name,
                    customer_message=text,
                    ai_reply=raw.get("ai_reply", ""),
                    confidence=raw.get("confidence", 0.0),
                    guard_decision=raw.get("guard_decision", ""),
                    status=raw.get("status", "pending"),
                    key=key,
                    person=raw.get("person") or name,
                    seq=int(raw.get("seq") or 0),
                )
                loaded += 1
            self._cleaned_counts = data.get("cleaned_counts", {})
            # 老文件没有 seq：按时间顺序补一个递增序号，保证"后进来的排后面"
            missing = [it for it in self._items.values() if not it.seq]
            for n, it in enumerate(sorted(missing, key=lambda i: i.timestamp), 1):
                it.seq = n
            if loaded:
                logger.info(f"Loaded {loaded} pending items from {self._path}")
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load pending queue: {e}")


class ContextStore:
    """Per-customer conversation history stored as JSONL files.

    Each customer gets ``data/context/{name}.jsonl`` with one JSON
    line per conversation turn: ``{ts, role, text}`` where role is
    "customer" or "assistant".

    **同一个人的名字变体要落到同一个文件**：截图模式读到的会话名会变
    （``@微信`` 有时读不出来、后面粘着消息预览），按字面存就会把一个人的历史
    切成好几份（实测见过 ``客户A_微信`` / ``客户C`` / ``大`` 这种），
    机器人回忆"跟这个人聊过什么"时只看得到其中一份，回答就断片了。
    所以这里用和待人工队列同一套 ``same_person`` 归一，并且把已经切开的文件**合并**。
    """

    def __init__(self, base_dir: str = "data/context"):
        self._dir = Path(base_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ── 归一：找同一个人的文件；发现切开的就并起来 ──────────────────

    @staticmethod
    def _stem_variants(stem: str) -> List[str]:
        """文件名的几种可能写法（``_safe_filename`` 把 @ 换成了 _，要还原回来比）。"""
        return [stem, stem.replace("_", "@")]

    def _matches(self, stem: str, customer_name: str) -> bool:
        return any(same_person(customer_name, v)
                   for v in self._stem_variants(stem))

    def _resolve_file(self, customer_name: str) -> Path:
        safe_name = _safe_filename(customer_name)
        direct = self._dir / f"{safe_name}.jsonl"
        try:
            candidates = sorted(self._dir.glob("*.jsonl"))
        except OSError:
            return direct
        hits = [f for f in candidates if self._matches(f.stem, customer_name)]
        if not hits:
            return direct
        if len(hits) > 1:
            # ★ 必须用**合并后活下来的那份**：合并是按行数挑主文件，
            # 挑中的不一定是 hits[0]，用 hits[0] 会指向刚被删掉的文件。
            return self._merge(hits)
        return hits[0]

    def _merge(self, files: List[Path]) -> Path:
        """把同一个人的几份历史并成一份（行多的那份当主文件），返回主文件。"""
        try:
            files = sorted(files, key=lambda f: -_line_count(f))
            main, others = files[0], files[1:]
            with open(main, "a", encoding="utf-8") as out:
                for f in others:
                    try:
                        text = f.read_text(encoding="utf-8")
                    except OSError:
                        continue
                    if text:
                        if not text.endswith("\n"):
                            text += "\n"
                        out.write(text)
                    f.unlink()
                    logger.info(f"会话历史合并：{f.name} → {main.name}")
            return main
        except OSError as e:
            logger.warning(f"合并会话历史失败（忽略）: {e}")
            return files[0]

    def append(self, customer_name: str, role: str, text: str):
        """Record one conversation turn."""
        entry = {
            "ts": time.time(),
            "role": role,
            "text": text[:1000],
        }
        with self._lock:
            path = self._resolve_file(customer_name)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def get_recent(self, customer_name: str, turns: int = 3) -> list[dict]:
        """Return the most recent N conversation turns."""
        with self._lock:
            path = self._resolve_file(customer_name)
        if not path.exists():
            return []
        lines = []
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        lines.append(json.loads(line))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to read context for {customer_name}: {e}")
            return []
        return lines[-turns:]


def _safe_filename(name: str) -> str:
    """Replace path-unsafe characters in customer names."""
    return "".join(c if c.isalnum() or c in "._- " else "_" for c in name).strip()


def _line_count(path: Path) -> int:
    try:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        return 0
