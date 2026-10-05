# -*- coding: utf-8 -*-
"""原型：LLM 把「陈述句/表格」整理成资料库问答条目 —— 默认只打印，不写库。

设计原则（这是价格库，错一个数字的代价很高）：
  1. **LLM 只做"重构"，不做"创作"** —— 不许新增原文没有的事实。
  2. **数字零误差**：输出里的每个数字都必须能在原文里找到，否则这条判不通过。
  3. **型号零误差**：英文/数字型号 token（A7M4、4K60、24-70）必须原样出现。
  4. **默认不写库**：只出 diff 供人过一眼；加 --write 才真正写入（且仍是逐条 upsert）。

用法：
    python scripts/llm_ingest.py --file data/chat_raw/01-机身租赁价格.txt --limit 6
    python scripts/llm_ingest.py --file xx.xlsx --write      # 确认后再写

为什么值得做（实测依据）：问答型的 chunk **只嵌入"问题"部分**，
所以"客户会怎么问"这件事直接决定召回分：
  同一段内容，对「索尼A7M4一天多少钱」问答式 0.941 / 陈述句 0.724；
  但客户换个说法（「A7M4日租多少」）陈述句 0.721 > 问答式 0.685。
=> 给每条事实**补几个真实问法**，才是 LLM 在这里最值钱的用法。
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rag.llm_client import chat                          # noqa: E402
from rag.kb_tools import make_faq                        # noqa: E402

SYSTEM = """你在帮一家相机租赁店整理客服资料库。用户给你一段原始资料，\
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

NUM_RE = re.compile(r"(?<![\dA-Za-z])\d+(?:\.\d+)?")
TOKEN_RE = re.compile(r"[A-Za-z]{1,6}\d+[A-Za-z0-9\-]*|\d+[A-Za-z]{1,4}\d*")
# 「数字 + 单位」配对。★ 为什么不能只比裸数字：实测
#   「押金 4000 元，即 4 万元？」能骗过裸数字校验 ——
#   因为"4"在原文的 4000 里出现过。加上单位后，"4 万"在原文里不存在，就会被拦下。
#
# ★ 为什么要 `(?<![\dA-Za-z])`：型号里的数字不能当数字用。实测误杀现场：
#   「索尼 A7M4 日租 90 元」——A7M4 里的 "4" +「日租」的"日" 被读成单位配对「4日」，
#   原文没有「4日」，于是这条正常答案被判不合格。加上后行断言就不会了。
UNIT_RE = re.compile(
    r"(?<![\dA-Za-z])(\d+(?:\.\d+)?)\s*"
    r"(万|%|元|块|折|天|帧|次|年|月|日|小时|分钟|点|个|台|张|米|公里|倍|级|寸)?")


def _pairs(text: str) -> set:
    return {(m.group(1), m.group(2) or "") for m in UNIT_RE.finditer(text)}


def _numbers(text: str) -> set:
    return set(NUM_RE.findall(text))


def _tokens(text: str) -> set:
    return {t.upper() for t in TOKEN_RE.findall(text)}


def check(src: str, pairs: list) -> list:
    """逐条校验，返回 [(问题, 回答, 问题列表)] —— 问题列表为空表示通过。"""
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


def parse_pairs(text: str) -> list:
    """把 LLM 输出切成 (问题, 回答) 列表。"""
    pairs, q, a = [], None, None
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line.startswith("客户问题:"):
            if q is not None:
                pairs.append((q, (a or "").strip()))
            q, a = line[len("客户问题:"):].strip(), None
        elif line.startswith("销售回答:"):
            a = line[len("销售回答:"):].strip()
        elif line and a is not None:
            # 分隔线（---、===、___）不是回答内容，别拼进去
            if line and set(line) <= set("-=_*#"):
                continue
            a += line                      # 回答偶尔会换行
    if q is not None:
        pairs.append((q, (a or "").strip()))
    # 兜底：去掉回答尾部残留的分隔符与空白
    return [(q, a.strip().strip("-=_*# ").strip()) for q, a in pairs if q]


async def restructure(chunk: str) -> list:
    out = await chat([{"role": "system", "content": SYSTEM},
                      {"role": "user", "content": chunk}],
                     temperature=0.2, max_tokens=900)
    return parse_pairs(out)


def load_chunks(path: Path, limit: int) -> list:
    """用项目自己的解析器拿分块（表格/文档/txt 都走同一条路）。"""
    from pipeline.upload import parse_records
    text, chunks = parse_records(path)
    if chunks is None:                  # txt/md 交给通用切块器（默认 500/100）
        from pipeline.chunker import split_text
        chunks = split_text(text)
    return [c for c in chunks if c.strip()][:limit]


def _load_env() -> None:
    """显式读**项目自己的** .env（只读这一个文件，不向上找父目录）。

    不读的话 `rag.llm_client` 拿不到密钥 —— 它是从环境变量取值，
    而环境变量是 main.py 启动时 load_dotenv 注入的，脚本单独跑没有这一步。
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env = ROOT / ".env"
    if env.exists():
        load_dotenv(env, override=False)


async def main() -> int:
    _load_env()
    ap = argparse.ArgumentParser(description="LLM 整理导入原型（默认不写库）")
    ap.add_argument("--file", required=True, help="要整理的 txt/xlsx/docx")
    ap.add_argument("--limit", type=int, default=5, help="最多处理几块（省 token）")
    ap.add_argument("--write", action="store_true",
                    help="真正写入资料库（默认只打印供核对）")
    args = ap.parse_args()

    path = Path(args.file)
    chunks = load_chunks(path, args.limit)
    print("文件: %s   取前 %d 块\n" % (path, len(chunks)))

    total = ok = bad = 0
    written = []
    for i, src in enumerate(chunks, 1):
        pairs = await restructure(src)
        checked = check(src, pairs)
        total += len(checked)
        print("─" * 78)
        print("【源 %d】%s" % (i, src[:150].replace("\n", " ")))
        for q, a, problems in checked:
            if problems:
                bad += 1
                print("  ✗ 问题: %s" % q)
                print("    回答: %s" % a[:100])
                for p in problems:
                    print("    !! %s" % p)
            else:
                ok += 1
                print("  ✓ 客户问题: %s" % q)
                print("    销售回答: %s" % a[:100])
                written.append(make_faq(q, a))

    print("\n" + "=" * 78)
    print("共 %d 条：通过 %d，**被数字/型号校验拦下 %d**" % (total, ok, bad))
    print("被拦下的比例就是'LLM 想编点什么'的真实发生率 —— 这就是为什么必须先校验。")

    if not args.write:
        print("\n（默认不写库。核对没问题后加 --write 重跑）")
        print("将写入 %d 条，示例：\n  %s" % (len(written), written[0] if written else "-"))
        return 0

    from qdrant_client import QdrantClient
    from pipeline.embedder import embed_and_store
    from rag.retriever import active_collection
    qd = QdrantClient(path=str(ROOT / "data" / "qdrant"))
    try:
        n = embed_and_store(written, qd, active_collection(), source="llm_整理")
        print("\n已写入 %d 条资料（source=llm_整理）" % n)
    finally:
        qd.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
