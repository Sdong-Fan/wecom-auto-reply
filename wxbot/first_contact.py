# wxbot/first_contact.py
"""第一次跟某个客户说话之前，先打个招呼。

**为什么两种模式都要**：

* API 模式有 ``enter_session`` 事件 —— 客户刚进会话、**还没说话**这个动作是服务端
  告诉我们的，那时就能欢迎。
* 截图模式**看不到这个动作**：客户不发言，屏幕上没有任何变化，无从观测。
  只能退一步：「**第一次**要跟这个客户说话之前，先招呼一句」。

两边共用这一份记录，所以 API 模式发过欢迎语之后，同一个人不会再被招呼第二次
（否则客户会被连着招呼两遍）。

**名字归一**：截图模式的会话名会变（`@微信` 有时读不出来、后面粘着消息预览），
所以判断"招呼过没有"要按"是不是同一个人"来比，不能按字符串相等 ——
否则同一个人会被反复招呼。复用 ``rag.human_fallback.same_person``。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent.parent
GREETED_PATH = HERE / "data" / "state" / "greeted.json"
MAX_KEEP = 5000          # 记满这么多人就丢最旧的（一家店几年也到不了）
_lock = threading.Lock()


def greeted_path(path: Optional[Path] = None) -> Path:
    return Path(path or GREETED_PATH)


def _read(path: Optional[Path] = None) -> List[list]:
    p = greeted_path(path)
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        rows = data.get("greeted", []) if isinstance(data, dict) else data
        return [r for r in rows if isinstance(r, list) and r]
    except Exception as e:
        logger.warning(f"读招呼记录失败（当没招呼过）: {e}")
        return []


def _write(rows: List[list], path: Optional[Path] = None) -> None:
    p = greeted_path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"greeted": rows[-MAX_KEEP:]}, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(p)
    except Exception as e:
        logger.warning(f"写招呼记录失败（不影响运行）: {e}")


def is_greeted(name: str, path: Optional[Path] = None) -> bool:
    """这个人招呼过没有（按"是不是同一个人"比，不按字符串相等）。"""
    if not name:
        return False
    from rag.human_fallback import same_person
    for row in _read(path):
        if same_person(name, str(row[0])):
            return True
    return False


def mark_greeted(name: str, path: Optional[Path] = None) -> None:
    if not name:
        return
    with _lock:
        rows = _read(path)
        from rag.human_fallback import same_person
        if any(same_person(name, str(r[0])) for r in rows):
            return
        rows.append([name, round(time.time())])
        _write(rows, path)


def count(path: Optional[Path] = None) -> int:
    return len(_read(path))


def reset(path: Optional[Path] = None) -> None:
    """清空记录（测试用；也让店主能"重新跟所有人打招呼"）。"""
    p = greeted_path(path)
    if p.is_file():
        p.unlink()
