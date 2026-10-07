# -*- coding: utf-8 -*-
"""把 dist/WeComBot 压成发布用的 zip（条目以 WeComBot/ 开头，解压即得一个文件夹）。

用法: python scripts/make_release_zip.py v1.3
产出: WeComBot-v1.3-win64.zip（仓库根目录）

为什么不用 Compress-Archive：1.6GB 会爆内存；7-Zip 这台机器没装。
zipfile + compresslevel=6 的压缩率与体积跟上一版基本一致（~850MB）。
"""
import os
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "dist" / "WeComBot"
# 绝不打进包的东西（万一有人在 dist 里跑过，会留下这些）
EXCLUDE_DIRS = {"logs", "debug", "data/context", "data/state", "data/pending"}
EXCLUDE_FILES = {".env"}
EXCLUDE_SUFFIX = {".pyc"}


def main() -> int:
    version = sys.argv[1] if len(sys.argv) > 1 else "v0"
    out = ROOT / f"WeComBot-{version}-win64.zip"
    if not SRC.is_dir():
        print(f"没有 {SRC}，先跑 scripts/build.py")
        return 1

    files = []
    for p in SRC.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(SRC).as_posix()
        if any(rel == d or rel.startswith(d + "/") for d in EXCLUDE_DIRS):
            continue
        if p.name in EXCLUDE_FILES or p.suffix.lower() in EXCLUDE_SUFFIX:
            continue
        files.append((p, f"WeComBot/{rel}"))

    total = sum(p.stat().st_size for p, _ in files)
    print(f"待压缩 {len(files)} 个文件 / {total/1024/1024:.0f} MB")

    t0 = time.time()
    done = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for i, (p, arc) in enumerate(files, 1):
            done += p.stat().st_size
            z.write(p, arc)
            if i % 500 == 0:
                print(f"  {i}/{len(files)}  {done/1024/1024:.0f}/{total/1024/1024:.0f} MB "
                      f"({time.time()-t0:.0f}s)", flush=True)
    size = out.stat().st_size
    print(f"完成: {out.name}  {size/1024/1024:.0f} MB  用时 {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
