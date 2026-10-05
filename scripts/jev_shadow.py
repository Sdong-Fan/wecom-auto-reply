# -*- coding: utf-8 -*-
"""离线对比：现状判断 vs Jev 判断模型。两个维度分开量，别混着吹。

    --dimension should_reply（默认）
        「该不该答」：规则词表 vs Jev 的 is_actionable_question
        标注映射：no_reply → 不该回；auto/escalate → 该回
        ★ 已实测结论：规则层 600/600（100%）。这批判评测集在这个维度上**没有区分度**
          （34 条 no_reply 全是表情/符号/收尾语，正则就够），所以 Jev 不可能更好。

    --dimension coverage
        「资料覆盖没覆盖」：现在的**检索相似度分数 > 阈值** vs Jev 的 kb_covers_question
        标注映射：大类「高频直答（资料库有答案）」→ 覆盖；「资料缺口（应转人工）」→ 未覆盖
        ★ 这才是实测最大的失败模式（600 条里约 10 条卡在门槛、46 题里 12/15 的
          "该答却转人工"都在这儿），也是唯一有干净标注、能量出正收益的位置。
        覆盖那一维**需要本地检索**（embedding + Qdrant），所以会加载 BGE 模型。

## 用法

    # 不联网也能量「现状基线」——这一步就能看出有没有 headroom
    python scripts/jev_shadow.py --dimension coverage --dry-run

    # 配好 .env 的 JEV_API_KEY 后对比（先 30 条）
    python scripts/jev_shadow.py --dimension coverage --limit 30
    python scripts/jev_shadow.py --dimension coverage --out docs/Jev对比结果.md

判断模型来源在 `config.json` → `judge.jev`（默认博查 Jev，限时免费），
也可用 `JEV_PROVIDER` / `JEV_MODEL` / `JEV_BASE_URL` 临时覆盖。密钥只从 `.env` 读。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# BGE 模型：别让它上网校验（和 main.py 一致，否则启动会卡十几秒）
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

SHOULD_REPLY_LABELS = {"auto", "escalate"}
NO_REPLY_LABELS = {"no_reply"}

# 「资料覆盖」这一维只用标注干净的两类（见文件头说明）
COVERED_KIND = "高频直答（资料库有答案）"
GAP_KIND = "资料缺口（应转人工）"

DEFAULT_FILES = ("eval/messages_200.csv", "eval/messages_200b.csv",
                 "eval/messages_200c.csv")


def load_rows(files) -> list:
    rows = []
    for f in files:
        p = ROOT / f
        if not p.exists():
            print(f"  [跳过] 找不到 {f}")
            continue
        with p.open(encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                text = (r.get("消息") or "").strip()
                label = (r.get("期望行为") or "").strip()
                if not text or label not in SHOULD_REPLY_LABELS | NO_REPLY_LABELS:
                    continue
                rows.append({
                    "id": f"{p.stem}-{r.get('编号', '')}",
                    "kind": (r.get("大类") or "").strip(),
                    "text": text,
                    "label": label,
                    "expected_reply": label in SHOULD_REPLY_LABELS,
                })
    return rows


def load_cfg() -> dict:
    try:
        return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[warn] 读 config.json 失败（用默认值）: {e}")
        return {}


def baseline_threshold(cfg: dict) -> float:
    try:
        return float(cfg.get("rag", {}).get("high_confidence_threshold", 0.5))
    except Exception:
        return 0.5


# ── 维度一：该不该答 ──────────────────────────────────────────────────

def run_should_reply(rows, cfg, args, jev_client) -> list:
    from rag.judge import should_reply as rules_should_reply
    from rag.jev_judge import QUESTIONS, build_state, decide_from_answers

    stat = Counter()
    diffs = []
    errors = Counter()
    tokens_in = 0
    latency = []

    for i, r in enumerate(rows, 1):
        rules_need = rules_should_reply(r["text"], cfg)[0]
        r["rules"] = rules_need
        r["rules_ok"] = (rules_need == r["expected_reply"])
        stat["rules_ok"] += r["rules_ok"]

        if not args.dry_run:
            t0 = time.time()
            try:
                data = jev_client.ask(build_state(r["text"], cfg), QUESTIONS, cfg)
                need, reason, ev = decide_from_answers(data.get("answers") or {})
                r.update({"jev": need, "jev_reason": reason,
                          "intent": ev.get("intent", ""), "noul": ev.get("noul")})
                tokens_in += int((data.get("usage") or {}).get("input_tokens") or 0)
                if need is not None:
                    r["jev_ok"] = (need == r["expected_reply"])
                    stat["jev_ok"] += r["jev_ok"]
                    stat["agree"] += (need == rules_need)
                    if need != rules_need:
                        stat["disagree"] += 1
                        if not need and r["expected_reply"] is False:
                            stat["jev_caught"] += 1
                        if need and r["expected_reply"] is True:
                            stat["jev_saved"] += 1
                        diffs.append(r)
            except Exception as e:
                errors[type(e).__name__] += 1
                r["error"] = str(e)[:200]
            latency.append(time.time() - t0)
            if args.sleep:
                time.sleep(args.sleep)
        if i % 25 == 0 or i == len(rows):
            print(f"  进度 {i}/{len(rows)}")

    n = len(rows)
    lines = [
        f"样本 {n} 条（该回 {sum(1 for r in rows if r['expected_reply'])} / "
        f"不该回 {sum(1 for r in rows if not r['expected_reply'])}）",
        "",
        "| 指标 | 规则层（现状） | Jev |",
        "|---|---|---|",
        f"| 「该不该答」判对 | {stat['rules_ok']}/{n}"
        f"（{stat['rules_ok']/n*100:.1f}%） | "
        + (f"{stat['jev_ok']}/{n}（{stat['jev_ok']/n*100:.1f}%）"
           if stat["jev_ok"] else "—") + " |",
    ]
    if stat["jev_ok"]:
        lines += [
            f"| 两边一致 | {stat['agree']}/{n}"
            f"（{stat['agree']/n*100:.1f}%） | — |",
            f"| Jev 多拦对（防刷屏） | — | {stat['jev_caught']} 条 |",
            f"| Jev 救回来（该答没答） | — | {stat['jev_saved']} 条 |",
        ]
        if latency:
            lines.append(f"| 平均判断耗时 | 0 秒（纯本地） | "
                         f"{sum(latency)/len(latency):.2f} 秒 |")
        if tokens_in:
            lines.append(f"| 累计输入 token | 0 | {tokens_in} |")
    if errors:
        lines += ["", f"调用失败：{dict(errors)}（这几条按「退回规则层」处理）"]
    if diffs:
        lines += ["", "分歧明细（最多 20 条）：", "",
                  "| 消息 | 标注 | 规则 | Jev | 意图 | 说明 |", "|---|---|---|---|---|---|"]
        for r in diffs[:20]:
            tag = ("Jev 救回" if (r.get("jev") and not r["rules"]) else "Jev 拦对")
            lines.append(f"| {r['text'][:30]} | {r['label']} | "
                         f"{'要回' if r['rules'] else '不回'} | "
                         f"{'要回' if r.get('jev') else '不回'} | "
                         f"{r.get('intent','')} | {tag} |")
    return lines


# ── 维度二：资料覆盖 ──────────────────────────────────────────────────

def run_coverage(rows, cfg, args, jev_client) -> list:
    """现状 = 检索 top_score > 阈值；候选 = Jev 的 kb_covers_question（带片段）。"""
    from rag.jev_judge import (QUESTIONS, build_state,
                               coverage_from_answers)
    from rag.retriever import active_collection, ensure_collection, search

    try:
        from qdrant_client import QdrantClient
    except Exception as e:
        return [f"没装 qdrant_client，跑不了覆盖维度：{e}"]

    thr = baseline_threshold(cfg)
    qd_path = str(ROOT / "data" / "qdrant")
    if not Path(qd_path).exists():
        return [f"本地向量库不存在：{qd_path}（先在界面上建一次资料库）"]
    try:
        client = QdrantClient(path=qd_path)
        coll = active_collection()
        ensure_collection(client, coll)
    except Exception as e:
        return [f"打开本地向量库失败（可能有另一个实例占着锁）：{e}"]

    from rag.embed_query import embed_query

    sub = [r for r in rows if r["kind"] in (COVERED_KIND, GAP_KIND)]
    for r in sub:
        r["covered_label"] = (r["kind"] == COVERED_KIND)

    stat = Counter()
    near = []          # 卡在阈值附近的（最有说服力的证据）
    errors = Counter()
    tokens_in = 0
    lat = []
    print(f"  覆盖维度样本 {len(sub)} 条"
          f"（覆盖 {sum(1 for r in sub if r['covered_label'])} / "
          f"未覆盖 {sum(1 for r in sub if not r['covered_label'])}），"
          f"现状阈值 = {thr}")

    for i, r in enumerate(sub, 1):
        try:
            pts = search(embed_query(r["text"]), client, collection_name=coll, top_k=5)
        except Exception as e:
            errors["检索失败"] += 1
            print(f"  [warn] 检索失败（{r['id']}）: {e}")
            continue
        r["top_score"] = float(pts[0].score) if pts else 0.0
        r["chunks"] = [getattr(p, "payload", {}).get("text", "") for p in pts]
        r["base_covered"] = r["top_score"] > thr          # 现状：分数当代理
        stat["base_ok"] += (r["base_covered"] == r["covered_label"])
        if r["base_covered"] != r["covered_label"]:
            stat["base_fp" if r["base_covered"] else "base_fn"] += 1
            if abs(r["top_score"] - thr) <= 0.10:
                near.append(r)

        if not args.dry_run:
            t0 = time.time()
            try:
                data = jev_client.ask(
                    build_state(r["text"], cfg, chunks=r["chunks"]),
                    QUESTIONS, cfg)
                cov, reason, ev = coverage_from_answers(data.get("answers") or {})
                r.update({"jev": cov, "jev_reason": reason, "noul": ev.get("noul")})
                tokens_in += int((data.get("usage") or {}).get("input_tokens") or 0)
                if cov is not None:
                    r["jev_ok"] = (cov == r["covered_label"])
                    stat["jev_ok"] += r["jev_ok"]
                    if cov != r["covered_label"]:
                        near.append(r)
            except Exception as e:
                errors[type(e).__name__] += 1
                r["error"] = str(e)[:200]
            lat.append(time.time() - t0)
            if args.sleep:
                time.sleep(args.sleep)
        if i % 25 == 0 or i == len(sub):
            print(f"  进度 {i}/{len(sub)}")

    n = max(1, sum(1 for r in sub if "top_score" in r))
    cov_n = sum(1 for r in sub if r["covered_label"])
    lines = [
        f"样本 {n} 条（资料覆盖 {cov_n} / 未覆盖 {n - cov_n}）",
        f"标注来源：大类「{COVERED_KIND}」→ 覆盖；「{GAP_KIND}」→ 未覆盖",
        "",
        f"现状判定方式：**检索相似度 top_score > {thr}** 视为覆盖",
        "",
        "| 指标 | 现状（分数当代理） | Jev 判覆盖 |",
        "|---|---|---|",
        f"| 「资料覆盖没覆盖」判对 | {stat['base_ok']}/{n}"
        f"（{stat['base_ok']/n*100:.1f}%） | "
        + (f"{stat['jev_ok']}/{n}（{stat['jev_ok']/n*100:.1f}%）"
           if stat["jev_ok"] else "—") + " |",
        f"| 假阳性：说覆盖、其实没覆盖（会拿错资料答，只能靠下游护栏兜） "
        f"| {stat['base_fp']} 条 | "
        + (f"{sum(1 for r in sub if r.get('jev') is True and not r['covered_label'])} 条"
           if stat["jev_ok"] else "—") + " |",
        f"| 假阴性：说没覆盖、其实覆盖（该答没答） "
        f"| {stat['base_fn']} 条 | "
        + (f"{sum(1 for r in sub if r.get('jev') is False and r['covered_label'])} 条"
           if stat["jev_ok"] else "—") + " |",
    ]
    if lat:
        lines.append(f"| 平均判断耗时 | 0 秒（本地检索约 0.1 秒） | "
                     f"{sum(lat)/len(lat):.2f} 秒 |")
    if tokens_in:
        lines.append(f"| 累计输入 token | 0 | {tokens_in} |")
    if errors:
        lines += ["", f"调用失败：{dict(errors)}"]

    bad = [r for r in sub if r.get("base_covered") != r.get("covered_label")
           and "top_score" in r]
    if bad:
        lines += ["", f"现状判错的 {len(bad)} 条（这就是 headroom）：", "",
                  "| 消息 | 类别 | top_score | 现状 | 应该 | 卡在阈值附近 |",
                  "|---|---|---|---|---|---|"]
        for r in bad[:20]:
            lines.append(
                f"| {r['text'][:28]} | {'覆盖' if r['covered_label'] else '未覆盖'} | "
                f"{r['top_score']:.3f} | "
                f"{'覆盖' if r['base_covered'] else '未覆盖'} | "
                f"{'覆盖' if r['covered_label'] else '未覆盖'} | "
                f"{'是' if abs(r['top_score']-thr) <= 0.10 else '否'} |")
    return lines


# ── 主流程 ────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="现状 vs Jev：离线对比")
    ap.add_argument("--dimension", choices=("should_reply", "coverage"),
                    default="should_reply")
    ap.add_argument("--files", nargs="*", default=list(DEFAULT_FILES))
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 条（0=全部）")
    ap.add_argument("--dry-run", action="store_true",
                    help="不联网：只量现状基线（该不该答=规则层；覆盖=检索阈值）")
    ap.add_argument("--out", default="", help="Markdown 报告写到这个路径")
    ap.add_argument("--sleep", type=float, default=0.0, help="每次判断间隔（秒）")
    ap.add_argument("--ping", action="store_true",
                    help="只做连通性自检：验凭据 + 验协议，不跑评测")
    args = ap.parse_args()

    cfg = load_cfg()
    from rag import jev_client

    provider, base, _path, model = jev_client.resolve_config(cfg)

    if args.ping:
        print("=" * 74)
        print("Bocha Jev 连通性自检")
        print("=" * 74)
        print(f"来源={provider}  地址={base}  模型={model}")
        names = " / ".join(jev_client.ENV_KEYS)
        print(f"凭据变量：{names}")
        print(f"当前凭据：{'已配置' if jev_client.has_key() else '**一个都没配**'}"
              f"（命中 {next((n for n in jev_client.ENV_KEYS if os.environ.get(n)), '无')}）")
        ok, why = jev_client.ping(cfg)
        print(("✅ " if ok else "❌ ") + why)
        if not ok:
            print("\n排查顺序：")
            print("  1) 凭据没配 → 在项目根目录 .env 里加一行 BOCHA_JEV_API_KEY=你的key")
            print("  2) 401 → 这把 key 没有 Jev 权限：去 https://jev.bocha.cn 领限时免费的")
            print("     （已有博查 key 且有权访问时可复用，变量名 BOCHA_SEARCH_API_KEY）")
            print("  3) 422 → 题目/state 形状或大小不对（脚本会打原始响应）")
            print("  4) 404 → 模型名或地址不对")
            print("  5) 超时/连不上 → 网络或代理")
        return 0 if ok else 3

    title = {"should_reply": "该不该答", "coverage": "资料覆盖"}[args.dimension]
    print("=" * 74)
    print(f"现状 vs Jev 判断模型 —— 维度：{title}")
    print("=" * 74)
    if args.dry_run:
        print("模式：DRY-RUN（不联网，只量现状基线）")
    else:
        print(f"模式：在线判断｜来源={provider} 模型={model}")
        if not jev_client.has_key():
            print(f"\n没配 {jev_client.ENV_KEY}，跑不了在线对比。")
            print("先把密钥写进 .env；博查 Jev 现在限时免费：https://open.bocha.cn")
            return 2

    rows = load_rows(args.files)
    if args.limit:
        rows = rows[:args.limit]
    if not rows:
        print("没有样本，检查 --files")
        return 1

    if args.dimension == "should_reply":
        lines = run_should_reply(rows, cfg, args, jev_client)
    else:
        lines = run_coverage(rows, cfg, args, jev_client)

    print("\n" + "=" * 74)
    for ln in lines:
        print(ln)
    if args.out:
        p = ROOT / args.out
        p.write_text(f"# 现状 vs Jev —— {title}\n\n"
                     "> 由 `scripts/jev_shadow.py` 生成，样本来自 `eval/`\n\n"
                     + "\n".join(lines) + "\n", encoding="utf-8")
        print(f"\n报告已写入：{args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
