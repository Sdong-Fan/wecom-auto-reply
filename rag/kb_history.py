# rag/kb_history.py
"""资料库改动前留一份旧内容，支持撤销。

**为什么值得单独一个模块**：改资料是"手一抖就报错价"的操作。老板把
「日租 120」改成「日租 100」结果改错行了，没有撤销就只能自己再想一遍原来写什么。

* 每次改动前 ``record()`` 一条快照（JSONL 追加，天然不怕写坏）
* 每个条目只留最近 ``keep`` 条（默认 5，配置 ``kb.history_versions``）
* ``undo()`` 取回最新一条快照并**消耗掉它** —— 所以连点两次能退两步

**历史挂在哪个 id 上**：挂在**改完之后的新 id** 上（内容变了 chunk_id 就变）。
这样列表里选中一行就能直接撤销它；而撤销会把文本换回旧内容，chunk_id 又变回
改之前的 id —— 于是"连点两次撤销"能一路退回去。
"""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent.parent

# 改动历史跟资料库档案走（第一个档案 data/kb_history.jsonl，新建的 data/kb_history_<档案>.jsonl）
HISTORY_PATH: Optional[Path] = None
DEFAULT_KEEP = 5


def history_path(path: Optional[Path] = None) -> Path:
    """每次调用都重新算 —— 切档案立刻生效，测试也能换掉。"""
    if path is not None:
        return Path(path)
    if HISTORY_PATH is not None:
        return Path(HISTORY_PATH)
    try:
        from rag import archives
        return archives.history_path()
    except Exception:
        return HERE / "data" / "kb_history.jsonl"


def keep_versions(cfg: dict = None) -> int:
    try:
        n = int((cfg or {}).get("kb", {}).get("history_versions", DEFAULT_KEEP))
    except Exception:
        n = DEFAULT_KEEP
    return max(1, min(50, n))


def _read_all(path: Path) -> List[dict]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue                      # 单行坏了不能把整段历史带崩
    return out


def _write_all(path: Path, rows: List[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                   encoding="utf-8")
    tmp.replace(path)


def record(entry_id: str, text: str, source: str = "", reason: str = "手动编辑",
           path: Optional[Path] = None, keep: int = DEFAULT_KEEP) -> None:
    """改动**之前**调用：把旧内容存下来（``entry_id`` 传改完之后的 id）。"""
    p = history_path(path)
    rows = _read_all(p)
    rows.append({"ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                 "id": str(entry_id), "text": text, "source": source,
                 "reason": reason})
    mine = [i for i, r in enumerate(rows) if str(r.get("id")) == str(entry_id)]
    if len(mine) > keep:
        drop = set(mine[:len(mine) - keep])       # 只留最近 keep 条
        rows = [r for i, r in enumerate(rows) if i not in drop]
    _write_all(p, rows)


def versions(entry_id: str, path: Optional[Path] = None) -> List[dict]:
    """某个条目的历史，最新在前。"""
    rows = [r for r in _read_all(history_path(path))
            if str(r.get("id")) == str(entry_id)]
    return list(reversed(rows))


def pop_last(entry_id: str, path: Optional[Path] = None) -> Optional[dict]:
    """取出并消耗最新一条快照（撤销用）。"""
    p = history_path(path)
    rows = _read_all(p)
    for i in range(len(rows) - 1, -1, -1):
        if str(rows[i].get("id")) == str(entry_id):
            got = rows.pop(i)
            _write_all(p, rows)
            return got
    return None


def undo(client, entry_id: str, collection: str = None,
         path: Optional[Path] = None) -> dict:
    """把某个条目退回上一版。没有历史就抛 ``ValueError``。"""
    from rag import kb_tools

    snap = pop_last(entry_id, path)
    if snap is None:
        raise ValueError("这条没有可撤销的记录（可能刚加的，或者已经退到底了）")

    text = snap["text"]
    if kb_tools.get_entry(client, entry_id, collection) is None:
        # 原 id 不在了：按旧文本重新插一条
        from pipeline.embedder import chunk_id, embed_and_store
        n = embed_and_store([text], client, collection, source=snap.get("source", ""))
        return {"id": str(chunk_id(text)), "text": text,
                "source": snap.get("source", ""), "chunks": n,
                "restored_at": snap.get("ts", "")}
    res = kb_tools.update_entry(client, entry_id, text, collection)
    res["restored_at"] = snap.get("ts", "")
    return res


def clear(path: Optional[Path] = None) -> None:
    p = history_path(path)
    if p.is_file():
        p.unlink()
