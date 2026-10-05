# -*- coding: utf-8 -*-
"""命令行：把资料交给 LLM 整理成问答。默认**只打印不写库**。

    python scripts/llm_ingest.py --file data/chat_raw/01-机身租赁价格.txt --limit 6
    python scripts/llm_ingest.py --file 资料.xlsx --write      # 核对没问题再写

核心逻辑在 `rag/llm_ingest.py`（界面走同一条路）：
    LLM 整理 → 自动校验（数字/型号/单位） → 人工过一眼 → 写入
自动校验为什么不能省、以及它拦不住什么，见那个模块的模块注释。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 兼容：测试从 scripts.llm_ingest 导入这些
from rag.llm_ingest import (check, parse_pairs, build_entries, to_faq,  # noqa: E402,F401
                            chunks_from_file)


def _load_env() -> None:
    """显式读**项目自己的** .env（只读这一个文件，不向上找父目录）。

    不读的话 `rag.llm_client` 拿不到密钥 —— 它从环境变量取值，
    而环境变量是 main.py 启动时 load_dotenv 注入的，脚本单独跑没有这一步。
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env = ROOT / ".env"
    if env.exists():
        load_dotenv(env, override=False)


def load_chunks(path: Path, limit: int) -> list:
    """旧名，等价于 `rag.llm_ingest.chunks_from_file`。"""
    return chunks_from_file(path, limit=limit)


async def main() -> int:
    _load_env()
    ap = argparse.ArgumentParser(description="LLM 整理导入（默认不写库）")
    ap.add_argument("--file", required=True, help="要整理的 txt/xlsx/docx")
    ap.add_argument("--limit", type=int, default=5, help="最多处理几块（省 token）")
    ap.add_argument("--write", action="store_true",
                    help="真正写入资料库（默认只打印供核对）")
    ap.add_argument("--model", default=None, help="覆盖模型名（默认读设置）")
    args = ap.parse_args()

    path = Path(args.file)
    chunks = load_chunks(path, args.limit)
    print("文件: %s   取前 %d 块\n" % (path, len(chunks)))

    def prog(done, total):
        print("  …第 %d/%d 块" % (done, total))

    entries = await build_entries(chunks, model=args.model, on_progress=prog)

    seen_chunk = None
    for e in entries:
        if e["chunk"] is not seen_chunk:
            seen_chunk = e["chunk"]
            print("─" * 78)
            print("【源】%s" % seen_chunk[:150].replace("\n", " "))
        if e["ok"]:
            print("  ✓ 客户问题: %s" % e["question"])
            print("    销售回答: %s" % e["answer"][:100])
        else:
            print("  ✗ 问题: %s" % e["question"])
            print("    回答: %s" % e["answer"][:100])
            for p in e["problems"]:
                print("    !! %s" % p)

    ok = [e for e in entries if e["ok"]]
    print("\n" + "=" * 78)
    print("共 %d 条：通过 %d，**被数字/型号校验拦下 %d**"
          % (len(entries), len(ok), len(entries) - len(ok)))
    print("被拦下的比例就是'LLM 想编点什么'的真实发生率 —— 这就是为什么必须先校验。")

    if not args.write:
        print("\n（默认不写库。核对没问题后加 --write 重跑）")
        if ok:
            print("将写入 %d 条，示例：\n  %s" % (len(ok), to_faq(ok[0])))
        return 0

    from qdrant_client import QdrantClient
    from pipeline.embedder import embed_and_store
    from rag.retriever import active_collection
    qd = QdrantClient(path=str(ROOT / "data" / "qdrant"))
    try:
        n = embed_and_store([to_faq(e) for e in ok], qd, active_collection(),
                            source=path.name, extra={"origin": "AI整理"})
        print("\n已写入 %d 条资料（source=%s，标记 origin=AI整理）" % (n, path.name))
    finally:
        qd.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
