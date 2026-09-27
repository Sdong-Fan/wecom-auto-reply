# wxbot/new_message_tracker.py
"""只认「点开始之后收到的新消息」—— 用会话列表的变化来判断，不猜、不靠白名单。

用户提的规则（比白名单更实用）：
    微信收到消息会把会话顶到最上面，所以**只要管点开始之后才有变化的那些会话**。

实现方式：给每个会话算两个指纹
    key  = 会话**名字区**的指纹（名字不变就不变 → 换行/新消息都不会让它变）
    fp   = 名字 + 消息预览 的指纹（有新消息就会变）

点开始时拍一次基线（key → fp），之后：
    * key 从没见过 → 新会话（有人第一次给你发消息）→ 算新消息
    * fp 与基线不同 → 这个会话有新内容 → 算新消息
    * 处理完之后 accept(key, fp) 把基线推进，**免得我们自己发出去的回复
      又把这一行"变得有新消息"**（否则会陷入反复点开的循环）

为什么名字区指纹能当 key：新消息改的是预览文字和排序，不改名字；
而行会重排，所以不能用行号 y 当 key（y 会串到别人身上）。
"""

from __future__ import annotations

import hashlib
import logging
from typing import Dict, Iterable, List, Tuple

logger = logging.getLogger(__name__)


def row_key(name_img) -> str:
    """会话名区指纹 —— 稳定的"这是谁"标识。"""
    return hashlib.md5(name_img.tobytes()).hexdigest()[:16]


def row_fp(row_img) -> str:
    """「名字 + 消息预览」指纹 —— 有新消息就会变。"""
    return hashlib.md5(row_img.tobytes()).hexdigest()[:16]


class NewMessageTracker:
    """会话列表的变化跟踪器。"""

    def __init__(self, enabled: bool = True):
        self.enabled = bool(enabled)
        self._baseline: Dict[str, str] = {}
        self._primed = False

    @property
    def primed(self) -> bool:
        return self._primed

    def prime(self, rows: Iterable[Tuple[str, str]]) -> int:
        """建立基线。``rows`` 是 (key, fp)。点「开始」后的第一轮调用它。"""
        self._baseline = {k: v for k, v in rows if k}
        self._primed = True
        logger.info(f"已记录 {len(self._baseline)} 个会话作为基线；"
                    f"之后只处理新收到的消息")
        return len(self._baseline)

    def reset(self) -> None:
        self._baseline.clear()
        self._primed = False

    def is_new(self, key: str, fp: str) -> bool:
        """这个会话此刻是否"有新消息"。未建基线时一律返回 False（先建基线）。"""
        if not self.enabled or not self._primed or not key:
            return False
        old = self._baseline.get(key)
        if old is None:
            return True          # 新会话冒出来了（第一次给你发消息）
        return old != fp

    def accept(self, key: str, fp: str) -> None:
        """把这个会话的当前内容记为基线（处理完/回复完/决定不理时调用）。"""
        if key:
            self._baseline[key] = fp

    def known(self, key: str) -> bool:
        return key in self._baseline

    @property
    def size(self) -> int:
        return len(self._baseline)


def resolve_only_new_messages(cfg: dict) -> bool:
    """从 config 读开关。默认关（企业微信有自己的红点+后缀规则，不受影响）。"""
    scan = (cfg or {}).get("scan", {})
    return bool(scan.get("only_new_messages", False))
