# rag/unanswered.py
"""「最常转人工的问题」排行榜 —— 知识库的缺口清单。

为什么要记这个：**转人工就是资料库里没写的东西**。原来这些消息只躺在
「待人工」里，人工处理完就没了，同样的问题明天还会再来一次。
把转人工的问题按次数排出来，店主每周补一次排前面的几条，转人工率就会掉下来。

存一份 ``data/unanswered.json``：问题指纹 → 次数 / 最近一次的原因 / 时间。
按指纹合并，"房租多少钱？" 和 "房租多少钱" 算同一条。
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_PATH = "data/unanswered.json"
# 测试可以覆盖（跟 kb_history / learn_store 一样的写法）
PATH = None

_lock = threading.Lock()
_MAX_ITEMS = 500          # 兜底上限：别让它无限长

_PUNCT = re.compile(r"[\s\u3000，。！？、；：,.!?;:~～\-—_（）()\[\]【】"
                    r"\"'“”‘’]+")


def path() -> Path:
    return Path(PATH or DEFAULT_PATH)


def _fp(question: str) -> str:
    """问题指纹：忽略空格/标点/大小写差异。"""
    s = _PUNCT.sub("", question or "")
    return s[:80].lower()


def _load() -> dict:
    p = path()
    try:
        if not p.is_file():
            return {}
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:                      # 坏文件不该影响扫描
        logger.warning(f"读取转人工排行失败（忽略）: {e}")
        return {}
    items = raw.get("items") if isinstance(raw, dict) else None
    return items if isinstance(items, dict) else {}


def _save(items: dict) -> None:
    p = path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"items": items}, ensure_ascii=False,
                                  indent=1), encoding="utf-8")
        tmp.replace(p)
    except Exception as e:
        logger.warning(f"保存转人工排行失败（忽略）: {e}")


def record(question: str, reason: str = "", customer_name: str = "") -> None:
    """记一次「这个问题转人工了」。转人工的地方都该调这个。"""
    q = (question or "").strip()
    key = _fp(q)
    if not key:
        return
    now = time.time()
    with _lock:
        items = _load()
        item = items.get(key)
        if item:
            item["count"] = int(item.get("count", 0)) + 1
            item["last_ts"] = now
            if reason:
                item["reason"] = reason
            if customer_name:
                item["name"] = customer_name
            # ★ 补过资料库又转人工了 = 那条资料没解决问题，重新标回"待补"
            item.pop("done_ts", None)
        else:
            items[key] = {"question": q[:200], "count": 1,
                          "reason": reason or "", "name": customer_name or "",
                          "first_ts": now, "last_ts": now}
        if len(items) > _MAX_ITEMS:
            # 满了丢"最久没再出现"的，保住高频的
            keep = sorted(items.items(),
                          key=lambda kv: -float(kv[1].get("last_ts", 0)))
            items = dict(keep[:_MAX_ITEMS])
        _save(items)


def mark_done(question: str) -> bool:
    """标记「这条已经补进资料库了」。返回是否找到对应条目。"""
    key = _fp(question)
    if not key:
        return False
    with _lock:
        items = _load()
        item = items.get(key)
        if item is None:
            return False
        item["done_ts"] = time.time()
        _save(items)
        return True


def all_items() -> list:
    """全部条目（未排序）。"""
    with _lock:
        return [dict(v) for v in _load().values()]


def top(n: int = 20) -> list:
    """按次数从多到少；次数相同按最近出现的排前面。"""
    items = all_items()
    items.sort(key=lambda it: (-int(it.get("count", 0)),
                               -float(it.get("last_ts", 0))))
    if n is None:
        return items
    n = int(n)
    return items[:n] if n > 0 else []


def count() -> int:
    """有几条不同的待补问题。"""
    return len(all_items())


def total() -> int:
    """一共转了多少次人工。"""
    return sum(int(it.get("count", 0)) for it in all_items())


def todo_count() -> int:
    """还有几条**没补进资料库**的。"""
    return len([it for it in all_items() if not it.get("done_ts")])


def clear() -> None:
    with _lock:
        _save({})
