# rag/llm_ingest.py
"""用 LLM 把「陈述句 / 表格」资料整理成资料库问答 —— **只重构，不创作**。

为什么需要：问答型 chunk **只嵌入"问题"部分**（`pipeline/embedder.py::embed_text_for`），
所以"客户会怎么问"直接决定召回。实测同一段内容：
    对「索尼A7M4一天多少钱」  问答式 0.941 / 陈述句 0.724
    对「A7M4日租多少」（换说法）陈述句 0.721 / 问答式 0.685
=> 把陈述句拆成多条问答，正好补上"客户换个说法就召不回"的短板。

⚠️ **为什么要自动校验**（这是本模块的重点，不是附加项）：
这是价格库。LLM 把 4000 写成 40000、"日租"写成"月租"，一旦入库，
护栏的"数字必须有出处"会**把被污染的库当依据** —— 错误被系统性地合法化。
所以每条回答里的数字/单位/型号都必须能在**原文**里找到。

踩过的两个真实坑（都有回归测试 tests/test_llm_ingest.py）：
  1. 只比裸数字会被「押金 4000 元，即 4 万元？」骗过（"4"在 4000 里出现过）
     → 改成比「数字+单位」
  2. 「索尼 A7M4 日租 90 元」被读成单位配对「4日」（型号里的 4 + 日租的日）
     → 加后行断言 `(?<![\\dA-Za-z])`

**拦不住的**：纯文字幻觉（原文没提库存，却答"有现货，明天就能发"）。
所以流程必须是 `LLM 整理 → 自动校验 → 人工过一眼 → 写入`，人工那步不能省。
"""
from __future__ import annotations

import logging
import re
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

Q_PREFIX = "客户问题:"
A_PREFIX = "销售回答:"
SEPARATOR_CHARS = set("-=_*#")


INGEST_SYSTEM = """你在帮一家相机租赁店整理客服资料库。用户给你一段原始资料，\
你把它整理成若干条"客户问题 / 销售回答"。

铁律（违反即作废）：
1. 只能使用原文里出现过的事实。**绝对不许新增、推测、补充原文没有的信息。**
2. 所有数字（价格、押金、天数、像素、帧数）必须与原文**逐字一致**，不许换算、\
不许四舍五入、不许改单位。
3. 型号必须原样照抄（例如 A7M4、A7R5、4K60、24-70 不许写成 A7 M4 / 4k60）。
4. 一个回答只讲一件事；原文一条信息可以配 1~3 个**客户真会怎么问**的问法，\
但每条问法的答案都必须是原文原话或原话的忠实缩写。
5. 原文没提到的（例如库存、运费、到货时间）一律不许出现在回答里。

输出格式（严格照抄，不要任何解释、不要 markdown 代码块）：
客户问题: <客户会怎么问>
销售回答: <只用原文事实>
---
客户问题: <另一个问法>
销售回答: <同上>
"""

# 数字。★ `(?<![\dA-Za-z])`：型号里的数字不能当数量用
#   （「A7M4 日租」里的 4 不是"4 天"）。
NUM_RE = re.compile(r"(?<![\dA-Za-z])\d+(?:\.\d+)?")
# 型号 / 规格 token：A7M4、4K60、R6、ISO102400
TOKEN_RE = re.compile(r"[A-Za-z]{1,6}\d+[A-Za-z0-9\-]*|\d+[A-Za-z]{1,4}\d*")
# 「数字 + 单位」配对。只比裸数字拦不住「4000 元 → 4 万元」这类改写。
UNIT_RE = re.compile(
    r"(?<![\dA-Za-z])(\d+(?:\.\d+)?)\s*"
    r"(万|%|元|块|折|天|帧|次|年|月|日|小时|分钟|点|个|台|张|米|公里|倍|级|寸)?")


def _numbers(text: str) -> set:
    return set(NUM_RE.findall(text))


def _tokens(text: str) -> set:
    return {t.upper() for t in TOKEN_RE.findall(text)}


def _pairs(text: str) -> set:
    return {(m.group(1), m.group(2) or "") for m in UNIT_RE.finditer(text)}


def check(src: str, pairs: List[tuple]) -> List[tuple]:
    """逐条校验 → ``[(问题, 回答, 问题列表)]``；问题列表为空表示通过。"""
    src_nums, src_tokens, src_pairs = _numbers(src), _tokens(src), _pairs(src)
    out = []
    for q, a in pairs:
        problems = []
        bad_nums = _numbers(a) - src_nums
        if bad_nums:
            problems.append("回答里的数字原文没有: %s" % sorted(bad_nums))
        bad_pairs = _pairs(a) - src_pairs
        if bad_pairs:
            problems.append("数字+单位与原文对不上: %s"
                            % ["%s%s" % p for p in sorted(bad_pairs)])
        bad_tok = {t for t in _tokens(a) if t not in src_tokens}
        if bad_tok:
            problems.append("型号/单位 token 原文没有: %s" % sorted(bad_tok))
        if not q.strip():
            problems.append("问题为空")
        if len(a.strip()) < 4:
            problems.append("回答太短")
        out.append((q, a, problems))
    return out


def parse_pairs(text: str) -> List[tuple]:
    """把 LLM 输出切成 ``(问题, 回答)``。分隔线不算回答内容。"""
    pairs, q, a = [], None, None
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line.startswith(Q_PREFIX):
            if q is not None:
                pairs.append((q, (a or "").strip()))
            q, a = line[len(Q_PREFIX):].strip(), None
        elif line.startswith(A_PREFIX):
            a = line[len(A_PREFIX):].strip()
        elif line and a is not None:
            if set(line) <= SEPARATOR_CHARS:
                continue
            a += line
    if q is not None:
        pairs.append((q, (a or "").strip()))
    return [(q, a.strip().strip("-=_*# ").strip()) for q, a in pairs if q]


async def restructure(chunk: str, model: str = None) -> List[tuple]:
    """让 LLM 把一段原文整理成问答对（低温度，要它照抄不要发挥）。"""
    from rag.llm_client import chat
    out = await chat([{"role": "system", "content": INGEST_SYSTEM},
                      {"role": "user", "content": chunk}],
                     temperature=0.2, max_tokens=900, model=model)
    return parse_pairs(out)


def make_entry(chunk: str, question: str, answer: str, problems: list) -> Dict:
    """整理结果的一条（GUI 与 CLI 都用这个结构）。"""
    return {"chunk": chunk, "question": question, "answer": answer,
            "problems": list(problems), "ok": not problems}


def to_faq(entry: Dict) -> str:
    from rag.kb_tools import make_faq
    return make_faq(entry["question"], entry["answer"])


async def build_entries(chunks: List[str], model: str = None,
                        on_progress: Optional[Callable[[int, int], None]] = None
                        ) -> List[Dict]:
    """逐块整理 + 校验。单块失败**不中断整体**（只记一条错误条目）。

    on_progress(已完成块数, 总块数)：界面用它刷进度。
    """
    entries: List[Dict] = []
    total = len(chunks)
    for i, chunk in enumerate(chunks, 1):
        try:
            pairs = await restructure(chunk, model=model)
        except Exception as e:
            logger.warning("整理第 %d 块失败: %s", i, e)
            pairs = []
            entries.append(make_entry(chunk, "", "", ["LLM 调用失败: %s" % e]))
        else:
            for q, a, problems in check(chunk, pairs):
                entries.append(make_entry(chunk, q, a, problems))
        if on_progress:
            try:
                on_progress(i, total)
            except Exception:
                pass
    return entries


def summary(entries: List[Dict]) -> Dict:
    ok = [e for e in entries if e["ok"]]
    return {"total": len(entries), "ok": len(ok), "bad": len(entries) - len(ok),
            "chunks": len({id(e["chunk"]) for e in entries})}


def chunks_from_file(path, limit: int = 200) -> List[str]:
    """用项目自己的解析器拿分块（表格/文档/txt 都走同一条路）。

    表格行走 `pipeline.upload._row_sentence` 转成自然句；
    txt/md 交给通用切块器（500/100，与导入时一致）—— **和正常导入共用一套解析**，
    这样"AI 整理"看到的原文和"直接导入"进库的内容是同一份。
    """
    from pathlib import Path
    from pipeline.upload import parse_records
    text, chunks = parse_records(Path(path))
    if chunks is None:
        from pipeline.chunker import split_text
        chunks = split_text(text)
    return [c for c in chunks if c.strip()][:limit]
