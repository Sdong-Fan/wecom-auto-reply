# rag/archives.py
"""资料库档案：一份清单管多个互相独立的资料库。

**为什么要"档案"**：原来整个程序只有一个库（Qdrant 里的 ``knowledge_base``），
检索、上传、编辑、历史、学习全写死围着它转。店主想再加一门生意
（摄影器材租赁 → 服装租赁），就只能手工搬数据。

**档案＝一套完全独立的东西**：

| 东西 | 第一个档案（从老库升上来） | 新建的档案 |
|---|---|---|
| 向量 | ``knowledge_base`` | ``kb_<id>`` |
| 上传的源文件 | ``data/chat_raw/uploaded/`` | ``data/chat_raw/archives/<id>/`` |
| 提示词 | ``prompts/`` | ``prompts/<id>/`` |
| 学到的语气 | ``data/learned/`` | ``data/learned/<id>/`` |
| 改动历史 | ``data/kb_history.jsonl`` | ``data/kb_history_<id>.jsonl`` |

**第一个档案沿用老路径是刻意的**：现有 237 条资料、上传过的文件、改过的提示词
一个字都不搬 —— 迁移越少，出事的可能越小。

独立性是硬要求：A 库的问题**绝不能**检索到 B 库的资料（串味 = 报错价）。
所以每个档案一个 collection，检索时按当前档案取。
"""

from __future__ import annotations

import datetime
import json
import logging
import re
import shutil
import threading
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent.parent

DEFAULT_ID = "photo"                  # 老库升上来的第一个档案
DEFAULT_NAME = "摄影器材租赁"
DEFAULT_COLLECTION = "knowledge_base"

_CACHE = {"mtime": None, "data": None}
_lock = threading.Lock()


# ── 清单读写 ──────────────────────────────────────────────────────────

def manifest_path() -> Path:
    return HERE / "data" / "kb" / "archives.json"


def _default_manifest() -> dict:
    return {"active": DEFAULT_ID,
            "archives": [{"id": DEFAULT_ID, "name": DEFAULT_NAME,
                          "collection": DEFAULT_COLLECTION,
                          "created": "", "note": "从原来的资料库升上来"}]}


def load() -> dict:
    """读档案清单。文件不存在时返回"只有一个默认档案"的**临时**清单（不写盘）。

    这样老用户什么都不用做：程序照旧跑，等他真的新建档案时才落文件。
    """
    p = manifest_path()
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return _default_manifest()
    if _CACHE["mtime"] == mtime and _CACHE["data"] is not None:
        return _CACHE["data"]
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not data.get("archives"):
            raise ValueError("清单格式不对")
    except Exception as e:
        logger.warning(f"档案清单读不了（{e}），用默认档案")
        return _default_manifest()
    data.setdefault("active", data["archives"][0]["id"])
    _CACHE.update({"mtime": mtime, "data": data})
    return data


def save(data: dict) -> None:
    p = manifest_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
    _CACHE.update({"mtime": p.stat().st_mtime, "data": data})


def reset_cache() -> None:
    """测试用：清掉缓存。"""
    _CACHE.update({"mtime": None, "data": None})


# ── 查询 ──────────────────────────────────────────────────────────────

def all_archives() -> List[dict]:
    return list(load().get("archives", []))


def get(archive_id: str) -> Optional[dict]:
    for a in all_archives():
        if a.get("id") == archive_id:
            return a
    return None


def active_id() -> str:
    data = load()
    aid = data.get("active") or DEFAULT_ID
    if get(aid) is None:                 # 清单被手改坏了，退回第一个
        aid = data["archives"][0]["id"]
    return aid


def active() -> dict:
    return get(active_id()) or _default_manifest()["archives"][0]


def active_name() -> str:
    return active().get("name") or active_id()


def collection_name(aid: str) -> str:
    """档案 id → collection 名。id 已经以 kb 开头的不再叠一次前缀。"""
    return aid if aid.startswith("kb") else f"kb_{aid}"


def collection_of(archive_id: str) -> str:
    a = get(archive_id)
    if a and a.get("collection"):
        return a["collection"]
    return collection_name(archive_id)


def active_collection() -> str:
    """回复时检索用哪个 collection。**每次调用现读**，切档案立刻生效。"""
    return collection_of(active_id())


def is_first(archive_id: str) -> bool:
    return collection_of(archive_id) == DEFAULT_COLLECTION


def set_active(archive_id: str) -> bool:
    if get(archive_id) is None:
        return False
    data = load()
    if data.get("active") == archive_id:
        return True
    data["active"] = archive_id
    save(data)
    logger.info(f"切换资料库档案: {archive_id}")
    return True


# ── 每个档案各自的目录 ────────────────────────────────────────────────

def upload_dir(archive_id: Optional[str] = None) -> Path:
    aid = archive_id or active_id()
    if is_first(aid):
        return HERE / "data" / "chat_raw" / "uploaded"
    return HERE / "data" / "chat_raw" / "archives" / aid


def prompts_dir(archive_id: Optional[str] = None) -> Path:
    aid = archive_id or active_id()
    if is_first(aid):
        return HERE / "prompts"
    return HERE / "prompts" / aid


def learned_dir(archive_id: Optional[str] = None) -> Path:
    aid = archive_id or active_id()
    if is_first(aid):
        return HERE / "data" / "learned"
    return HERE / "data" / "learned" / aid


def history_path(archive_id: Optional[str] = None) -> Path:
    aid = archive_id or active_id()
    if is_first(aid):
        return HERE / "data" / "kb_history.jsonl"
    return HERE / "data" / f"kb_history_{aid}.jsonl"


def trash_dir() -> Path:
    """删掉的档案挪这里 —— 源是资产，不真删。"""
    return HERE / "data" / "kb" / "deleted"


# ── 增删改 ────────────────────────────────────────────────────────────

def slug(name: str, taken: Optional[set] = None) -> str:
    """中文名字 → 内部 id。中文没法当目录名就直接用拼音？不，用 ``kb1`` 这种序号更稳。

    保留 ASCII 字母数字，中文等其它字符丢掉；全丢光了就用 ``kb<N>``。
    """
    taken = taken or {a.get("id") for a in all_archives()}
    s = re.sub(r"[^a-zA-Z0-9_]+", "", (name or "").lower())[:16]
    if len(s) < 2:
        n = 1
        while f"kb{n}" in taken:
            n += 1
        return f"kb{n}"
    if s not in taken:
        return s
    n = 2
    while f"{s}{n}" in taken:
        n += 1
    return f"{s}{n}"


def create(name: str, note: str = "") -> dict:
    """新建一个空档案（只建清单和目录，collection 等第一次入库时自动建）。"""
    name = (name or "").strip()
    if not name:
        raise ValueError("档案名不能为空")
    with _lock:
        data = load()
        aid = slug(name, {a.get("id") for a in data["archives"]})
        entry = {"id": aid, "name": name, "collection": collection_name(aid),
                 "created": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                 "note": note}
        data["archives"].append(entry)
        save(data)
    upload_dir(aid).mkdir(parents=True, exist_ok=True)
    logger.info(f"新建资料库档案: {name}（{aid}）")
    return entry


def rename(archive_id: str, name: str) -> bool:
    name = (name or "").strip()
    if not name:
        return False
    with _lock:
        data = load()
        for a in data["archives"]:
            if a.get("id") == archive_id:
                a["name"] = name
                save(data)
                return True
    return False


def update_note(archive_id: str, note: str) -> bool:
    with _lock:
        data = load()
        for a in data["archives"]:
            if a.get("id") == archive_id:
                a["note"] = note
                save(data)
                return True
    return False


def delete(archive_id: str) -> bool:
    """删档案：清单里去掉 + 目录挪到回收站。**collection 由调用方删**（要 client）。

    两个拦路的检查，顺序有讲究：
    1. **只剩一个不让删** —— 删了程序就无库可用
    2. **当前正在用的不让删** —— 每次回复都按它检索，删到一半最难受

    先查 1 再查 2：只剩一个时它必然正在用，先报"至少要留一个"才说得通。
    """
    with _lock:
        data = load()
        if len(data["archives"]) <= 1:
            raise ValueError("至少要留一个档案 —— 删光了机器人就没资料可查了")
        if archive_id == active_id():
            raise ValueError("这是当前正在用的档案，先切到别的档案再删")
        keep = [a for a in data["archives"] if a.get("id") != archive_id]
        if len(keep) == len(data["archives"]):
            return False
        data["archives"] = keep
        save(data)

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    for d in (upload_dir(archive_id), learned_dir(archive_id), prompts_dir(archive_id)):
        if d.is_dir():
            dest = trash_dir() / f"{archive_id}_{stamp}" / d.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(d), str(dest))
            except Exception as e:
                logger.warning(f"档案目录挪进回收站失败（{d}）: {e}")
    h = history_path(archive_id)
    if h.is_file():
        dest = trash_dir() / f"{archive_id}_{stamp}" / h.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(h), str(dest))
        except Exception as e:
            logger.warning(f"档案历史挪进回收站失败: {e}")
    logger.info(f"删除资料库档案: {archive_id}（目录已挪进 {trash_dir()}）")
    return True


def copy_dirs(src_id: str, dst_id: str,
              kinds=("uploads", "prompts", "learned")) -> None:
    """把一个档案的目录整份拷给新档案。

    * **新建**：只拷 ``prompts`` + ``learned``（"怎么说话"沿用你现在的，事实从零开始）
    * **复制档案**：连 ``uploads`` 一起拷（做变体用）
    """
    mapping = {"uploads": (upload_dir, upload_dir),
               "prompts": (prompts_dir, prompts_dir),
               "learned": (learned_dir, learned_dir)}
    for kind in kinds:
        if kind not in mapping:
            continue
        src, dst = mapping[kind][0](src_id), mapping[kind][1](dst_id)
        if not src.is_dir():
            continue
        dst.mkdir(parents=True, exist_ok=True)
        for f in src.iterdir():
            if f.is_file():
                try:
                    shutil.copy2(f, dst / f.name)
                except Exception as e:
                    logger.warning(f"复制 {f} → {dst} 失败: {e}")
            elif f.is_dir() and f.name != ".old":
                try:
                    shutil.copytree(f, dst / f.name, dirs_exist_ok=True)
                except Exception as e:
                    logger.warning(f"复制目录 {f} → {dst} 失败: {e}")


def counts() -> dict:
    """每个档案大概有多少东西（界面列表用，不查 Qdrant）。"""
    out = {}
    for a in all_archives():
        aid = a["id"]
        try:
            n = len([f for f in upload_dir(aid).iterdir() if f.is_file()])
        except OSError:
            n = 0
        out[aid] = {"sources": n}
    return out
