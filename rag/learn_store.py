# rag/learn_store.py
"""「从人工改稿里学东西」的存储层：存 / 读 / 去重 / 遮蔽 / 上限 / 黑名单。

**为什么单独一层**：学到的内容会**直接进提示词**，是"能自己长大"的东西。
自己长大的东西必须看得见、删得掉、不会无限膨胀，所以存储、去重、上限、
遮蔽这几件事集中在这里，注入逻辑（`rag/learn.py`）只负责读。

三类东西分开存，因为它们的风险完全不同：

| 文件 | 内容 | 风险 |
|---|---|---|
| ``style_rules.jsonl`` | 语气规则（"句子拆短，句尾加哈"） | 低：只说怎么说话 |
| ``tone_samples.jsonl`` | 人工真说过的句子（数字已遮蔽） | 低：不携带事实 |
| ``fact_candidates.jsonl`` | 待确认的新知识 | **高：必须人工确认才进资料库** |
| ``accepts.json`` | 采纳次数（直接发 AI 草稿的次数） | 只统计，不学习 |

**遮蔽**：人工的话里常带价格（"押金 4000，还回来就退你"）。这种句子被注入提示词后，
模型可能把 4000 抄到别的回答里。所以学来的样本把**独立整数**换成 ▢；
模型名里的数字（A7M4、F2.8）不动 —— 那是型号不是价格，遮了反而看不懂。
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent.parent

# 学到的语气跟资料库档案走：第一个档案用 data/learned/，新建的档案用 data/learned/<档案>/
# （显式赋值可覆盖，测试用）
LEARN_DIR: Optional[Path] = None

STYLE_RULES = "style_rules.jsonl"
TONE_SAMPLES = "tone_samples.jsonl"
FACT_CANDIDATES = "fact_candidates.jsonl"
ACCEPTS = "accepts.json"
BLOCKED = "blocked.json"

DEFAULT_MAX_ITEMS = 50
_lock = threading.Lock()


# ── 路径与配置（每次调用都重算，测试会换掉常量） ─────────────────────

def learn_dir(base: Optional[Path] = None) -> Path:
    """当前档案的学习记录目录。**每次调用现算**，切档案立刻生效。"""
    if base is not None:
        return Path(base)
    if LEARN_DIR is not None:
        return Path(LEARN_DIR)
    try:
        from rag import archives
        return archives.learned_dir()
    except Exception:
        return HERE / "data" / "learned"


_cfg_cache: dict = {"mtime": None, "cfg": {}}


def _load_cfg() -> dict:
    """没传 cfg 时从 config.json 读。

    **为什么需要这个**：`rag/generator.py` 在生成回复时手上没有内存里的 config，
    如果 ``enabled(None)`` 一律当"开着"，用户在界面上关掉学习也没用 ——
    关了还在学、还在注入，那是骗人。所以按文件读，并用 mtime 做缓存。
    """
    p = HERE / "config.json"
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return {}
    if _cfg_cache["mtime"] == mtime:
        return _cfg_cache["cfg"]
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    _cfg_cache.update({"mtime": mtime, "cfg": cfg})
    return cfg


def max_items(cfg: dict = None) -> int:
    try:
        n = int((cfg if cfg is not None else _load_cfg())
                .get("kb", {}).get("learn", {}).get("max_items", DEFAULT_MAX_ITEMS))
    except Exception:
        n = DEFAULT_MAX_ITEMS
    return max(10, min(500, n))


def enabled(cfg: dict = None) -> bool:
    """学习总开关，默认开（用户可在「学到的」页一键关）。"""
    try:
        return bool((cfg if cfg is not None else _load_cfg())
                    .get("kb", {}).get("learn", {}).get("enabled", True))
    except Exception:
        return True


# ── 文本处理 ──────────────────────────────────────────────────────────

# 独立的 2 位以上整数：押金 4000 / ￥1200 / 3 台里的 3 不动（单个数字噪声太大）
_STANDALONE_INT = re.compile(r"(?<![A-Za-z0-9])\d{2,}(?![A-Za-z0-9])")
# 手机号 / 长串数字
_PHONE = re.compile(r"(?<!\d)\d{7,}(?!\d)")
# @昵称（微信里客户的名字）
_AT_NAME = re.compile(r"@[^\s，。！？,.!?]{1,20}")


def mask(text: str) -> str:
    """把可能被误抄的数字和客户昵称遮掉。"""
    s = text or ""
    s = _PHONE.sub("▢", s)
    s = _STANDALONE_INT.sub("▢", s)
    s = _AT_NAME.sub("▢", s)
    return re.sub(r"\s+", " ", s).strip()


def norm(text: str) -> str:
    """归一化：去空白和标点，用来判断"改没改"和去重。"""
    s = re.sub(r"[\s\u3000]+", "", text or "")
    # 全角引号是刻意的：ASCII 双引号写在 "..." 里会把字符串断开成两段拼接
    return re.sub(r"[，。！？、；：“”‘’（）()~!?.,;:—…\-]", "", s)


def content_hash(text: str) -> str:
    return hashlib.md5(norm(text).encode("utf-8")).hexdigest()[:12]


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _seq() -> int:
    """排序用的递增序号。

    时间戳只精确到秒，同一秒里学进来的几条按 ``ts`` 排不出先后（排序不稳定，
    会出现"新的排在旧的后面"）。所以另存一个纳秒序号专门用来排序。
    """
    return time.time_ns()


# ── 通用读写 ──────────────────────────────────────────────────────────

def _path(name: str, base: Optional[Path] = None) -> Path:
    return learn_dir(base) / name


def _read_jsonl(name: str, base: Optional[Path] = None) -> List[dict]:
    p = _path(name, base)
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue                       # 单行坏了不能把整段学习记录带崩
    return out


def _write_jsonl(name: str, rows: List[dict], base: Optional[Path] = None) -> None:
    p = _path(name, base)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                   encoding="utf-8")
    tmp.replace(p)


def _append(name: str, row: dict, keep: int, base: Optional[Path] = None) -> None:
    """追加一条，超过上限就丢最旧的。"""
    rows = _read_jsonl(name, base)
    rows = [r for r in rows if r.get("id") != row.get("id")]
    rows.append(row)
    if len(rows) > keep:
        rows = rows[-keep:]
    _write_jsonl(name, rows, base)


# ── 黑名单（「这条别学」） ────────────────────────────────────────────

def _read_blocked(base: Optional[Path] = None) -> dict:
    p = _path(BLOCKED, base)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def is_blocked(kind: str, text: str, base: Optional[Path] = None) -> bool:
    return content_hash(text) in _read_blocked(base).get(kind, {})


def block(kind: str, text: str, base: Optional[Path] = None) -> None:
    """「这条别学」：删掉它，并记住别再学进来。"""
    with _lock:
        blocked = _read_blocked(base)
        blocked.setdefault(kind, {})[content_hash(text)] = mask(text)[:80]
        p = _path(BLOCKED, base)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(blocked, ensure_ascii=False, indent=2), encoding="utf-8")


def blocked_count(base: Optional[Path] = None) -> int:
    return sum(len(v) for v in _read_blocked(base).values())


# ── 语气规则 ──────────────────────────────────────────────────────────

def add_style_rule(rule: str, evidence: str = "", cfg: dict = None,
                   base: Optional[Path] = None) -> Optional[dict]:
    """加一条语气规则。重复的只累加 ``hits``，不重复占位置。"""
    rule = re.sub(r"\s+", " ", (rule or "").strip()).lstrip("-·* ").strip()
    if len(rule) < 4 or len(rule) > 80:
        return None                        # 太短没信息量，太长会塞爆提示词
    if is_blocked(STYLE_RULES, rule, base):
        return None
    with _lock:
        rows = _read_jsonl(STYLE_RULES, base)
        hid = content_hash(rule)
        for r in rows:
            if r.get("id") == hid:
                r["hits"] = int(r.get("hits", 1)) + 1
                r["ts"] = _now()
                _write_jsonl(STYLE_RULES, rows, base)
                return r
        row = {"id": hid, "ts": _now(), "seq": _seq(), "rule": rule, "hits": 1,
               "evidence": mask(evidence)[:120]}
        _append(STYLE_RULES, row, max_items(cfg), base)
        return row


def bump_rule(item_id: str, base: Optional[Path] = None) -> Optional[dict]:
    """把一条已有规则再记一次命中（语义重复时用这个，而不是新增一条）。"""
    with _lock:
        rows = _read_jsonl(STYLE_RULES, base)
        for r in rows:
            if r.get("id") == item_id:
                r["hits"] = int(r.get("hits", 1)) + 1
                r["ts"] = _now()
                _write_jsonl(STYLE_RULES, rows, base)
                return r
    return None


def style_rules(base: Optional[Path] = None) -> List[dict]:
    """排序：归并过的按人工定的次序在前，其余按命中次数，同分新的在前。"""
    rows = _read_jsonl(STYLE_RULES, base)
    rows.sort(key=lambda r: (r.get("order", 9999),
                             -int(r.get("hits", 1)),
                             -(r.get("seq") or 0)))
    return rows


def replace_rules(rules: List[str], base: Optional[Path] = None) -> int:
    """用一份整理过的清单**整体替换**规则（LLM 归并用）。

    归并后命中次数没法对应回原条目，所以一律重置为 1、改用 ``order`` 记住
    "哪条更重要"（顺序由归并时的输出顺序决定）。次数只是排序用的，不影响功能。
    """
    rows = []
    for i, text in enumerate(rules):
        t = re.sub(r"\s+", " ", (text or "").strip()).lstrip("-·* ").strip()
        if not t:
            continue
        rows.append({"id": content_hash(t), "ts": _now(), "seq": _seq(),
                     "rule": t, "hits": 1, "order": i, "evidence": "归并后"})
    with _lock:
        _write_jsonl(STYLE_RULES, rows, base)
    return len(rows)


# ── 口吻样本 ──────────────────────────────────────────────────────────

def add_tone_sample(human_text: str, question: str = "", draft: str = "",
                    cfg: dict = None, base: Optional[Path] = None) -> Optional[dict]:
    """记一句"人真正说出口的话"。数字会遮蔽。"""
    masked = mask(human_text)
    if len(norm(masked)) < 2:
        return None
    if is_blocked(TONE_SAMPLES, masked, base):
        return None
    hid = content_hash(masked)
    with _lock:
        rows = _read_jsonl(TONE_SAMPLES, base)
        for r in rows:
            if r.get("id") == hid:
                r["hits"] = int(r.get("hits", 1)) + 1
                r["ts"] = _now()
                _write_jsonl(TONE_SAMPLES, rows, base)
                return r
        row = {"id": hid, "ts": _now(), "seq": _seq(), "text": masked, "hits": 1,
               "question": mask(question)[:80], "draft": mask(draft)[:120]}
        _append(TONE_SAMPLES, row, max_items(cfg), base)
        return row


def tone_samples(base: Optional[Path] = None) -> List[dict]:
    rows = _read_jsonl(TONE_SAMPLES, base)
    rows.sort(key=lambda r: (r.get("seq") or 0, r.get("ts", "")), reverse=True)
    return rows


def find_tone(text: str, base: Optional[Path] = None) -> Optional[dict]:
    """这句样本是不是已经学过了（勾选界面用来提示"已学过"）。"""
    hid = content_hash(mask(text))
    for r in _read_jsonl(TONE_SAMPLES, base):
        if r.get("id") == hid:
            return r
    return None


# ── 待确认的新知识 ────────────────────────────────────────────────────

def add_fact(question: str, claim: str, draft: str = "",
             chunk_seen: bool = False, base: Optional[Path] = None,
             cfg: dict = None, source: str = "改稿学到") -> Optional[dict]:
    """一条"待确认的说法" → 等用户确认才进资料库。

    两个来源：
    * ``改稿学到`` —— 人工改稿时带出的、草稿里没有的新说法
    * ``直发确认`` —— 待人工页**直接发送**了 AI 草稿：等于店主认可了这个回答，
      于是把「客户问题 + 发出的回答」当候选收进来。采纳后进资料库，
      下次同一个问题检索分就高了，不用再转人工。

    ``chunk_seen``：这条说法在检索到的资料里已经有了吗？有的话不用确认（不是新知识）。
    **直发确认不走这个过滤** —— 它恰恰是"资料里有、但这个问题没检索到"的情况，
    收进来才有意义。
    """
    claim = re.sub(r"\s+", " ", (claim or "").strip())
    if len(norm(claim)) < 4:
        return None
    if chunk_seen:
        return None
    hid = content_hash(claim)
    with _lock:
        rows = _read_jsonl(FACT_CANDIDATES, base)
        for r in rows:
            if r.get("id") == hid:
                r["hits"] = int(r.get("hits", 1)) + 1
                r["ts"] = _now()
                _write_jsonl(FACT_CANDIDATES, rows, base)
                return r
        row = {"id": hid, "ts": _now(), "seq": _seq(), "claim": claim,
               "question": question[:120], "draft": draft[:200],
               "status": "pending", "hits": 1, "source": source}
        _append(FACT_CANDIDATES, row, max(20, max_items(cfg)), base)
        return row


def facts(status: str = "pending", base: Optional[Path] = None) -> List[dict]:
    rows = [r for r in _read_jsonl(FACT_CANDIDATES, base)
            if status is None or r.get("status", "pending") == status]
    rows.sort(key=lambda r: (r.get("seq") or 0, r.get("ts", "")), reverse=True)
    return rows


def set_fact_status(fact_id: str, status: str,
                    base: Optional[Path] = None) -> bool:
    """``accepted`` / ``rejected``。"""
    with _lock:
        rows = _read_jsonl(FACT_CANDIDATES, base)
        hit = False
        for r in rows:
            if r.get("id") == fact_id:
                r["status"] = status
                r["decided"] = _now()
                hit = True
        if hit:
            _write_jsonl(FACT_CANDIDATES, rows, base)
        return hit


# ── 采纳次数（直接发 AI 草稿） ────────────────────────────────────────

def bump_accept(question: str, reply: str = "", base: Optional[Path] = None) -> dict:
    """直接点「发送」＝认可这条草稿。**只记次数，不学语气**（学自己的话会越跑越偏）。

    留着给以后用：某个问题被采纳很多次，说明它其实答得对，可以考虑放宽自动发送。
    """
    q = re.sub(r"\s+", " ", (question or "").strip())[:120]
    if not q:
        return {}
    hid = content_hash(q)
    with _lock:
        p = _path(ACCEPTS, base)
        data = {}
        if p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8")) or {}
            except Exception:
                data = {}
        row = data.get(hid) or {"question": q, "count": 0}
        row["count"] = int(row.get("count", 0)) + 1
        row["last_ts"] = _now()
        data[hid] = row
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return row


def accepts(base: Optional[Path] = None) -> List[dict]:
    p = _path(ACCEPTS, base)
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    rows = list(data.values())
    rows.sort(key=lambda r: -int(r.get("count", 0)))
    return rows


# ── 管理 ──────────────────────────────────────────────────────────────

KINDS = {STYLE_RULES: "rule", TONE_SAMPLES: "text", FACT_CANDIDATES: "claim"}


def delete_item(kind: str, item_id: str, base: Optional[Path] = None) -> bool:
    """删一条。"""
    if kind not in KINDS:
        return False
    with _lock:
        rows = _read_jsonl(kind, base)
        left = [r for r in rows if r.get("id") != item_id]
        if len(left) == len(rows):
            return False
        _write_jsonl(kind, left, base)
        return True


def drop_and_block(kind: str, item_id: str, base: Optional[Path] = None) -> bool:
    """「这条别学」：删掉 + 记住别再学进来。"""
    rows = _read_jsonl(kind, base)
    for r in rows:
        if r.get("id") == item_id:
            block(kind, r.get(KINDS[kind], ""), base)
            return delete_item(kind, item_id, base)
    return False


def counts(base: Optional[Path] = None) -> dict:
    return {
        "rules": len(_read_jsonl(STYLE_RULES, base)),
        "tones": len(_read_jsonl(TONE_SAMPLES, base)),
        "pending_facts": len(facts("pending", base)),
        "blocked": blocked_count(base),
        "accepts": len(accepts(base)),
    }


def clear(base: Optional[Path] = None) -> None:
    """清空所有学习记录（黑名单和采纳次数也清）。"""
    for name in (STYLE_RULES, TONE_SAMPLES, FACT_CANDIDATES, ACCEPTS, BLOCKED):
        p = _path(name, base)
        if p.is_file():
            p.unlink()
