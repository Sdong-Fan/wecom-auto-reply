# rag/learn.py
"""从"人工改稿"里学：店主怎么说话（自动学）、店里还有什么说法（要确认）。

**触发点**：待人工页 → 点「编辑」→ 改完 → **发送成功之后**。不是点发送的瞬间 ——
发失败（比如没能确认会话、发错人中止）时说的话不该被学走。

**三分法**（整个设计都围绕这个）：

| 学什么 | 例子 | 处理 |
|---|---|---|
| 语气/措辞 | 把"您好，我为您查询一下"改成"我看看哈" | 自动学，下次直接用 |
| 事实/资料 | 人工补了"周末也发货"、"押金 3500" | **必须人工确认**才进资料库 |
| 绝不学 | "这次给你免押"这种一次性例外、客户私人信息 | 丢弃（靠确认拦下来） |

自动学语气安全（只改措辞不改事实），自动学事实危险（一次性例外被当成通用政策，
以后对所有客户免押）。所以两条路完全分开。

**两个不学**：
* 直接点「发送」（没编辑）→ 不学语气，只记采纳次数（学自己的话会自我强化、越跑越偏）
* 草稿和终稿归一化后一样（只改了标点空格）→ 不学
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import List, Optional

from rag import learn_store as ls

logger = logging.getLogger(__name__)

MAX_RULES_IN_BLOCK = 8
MAX_TONES_IN_BLOCK = 5
MAX_PAIRS_IN_BLOCK = 3


# ── LLM 分析 ──────────────────────────────────────────────────────────

ANALYZE_SYSTEM = """你在帮一家摄影器材租赁店总结"店主自己是怎么说话的"。
你是**分析器**，不是写手。只输出 JSON，不要解释、不要 markdown 代码块。

给你三样东西：客户原话、AI 草稿、店主实际发出去的话。请对比"草稿→终稿"的改动，输出：

{
  "style_rules": ["..."],
  "tone_sample": "...",
  "facts": ["..."],
  "fact_question": "..."
}

* **style_rules**：店主在**说话方式**上的习惯，**最多 2 条**，每条不超过 25 字。
  只描述措辞：句子长短、语气词（呀/哈/呢）、称呼、标点、emoji 习惯、有没有客套话、
  是先给结论还是先解释。**绝对不许包含任何价格、型号、库存、政策、时间、数字**。
  要**具体到能照着做**，不要写"要口语化"这种空话。
  如果改动只是个别字的润色、看不出习惯，就给空数组 []。
* **tone_sample**：从终稿里挑**一句最能代表他说话样子的原话**，照抄不要改写。
  没有合适的就给空串 ""。
* **facts**：终稿里**新出现的、关于店里的具体说法**（价格、时效、政策、库存、流程等），
  而且草稿和客户原话里都没有。只是换个说法不算 facts。
  ★ **同一件事的几个参数必须合成一条**，不许拆开。例如终稿是
  「索尼zve10售价5000，日租80，租期3天起」，要写成一条
  「索尼 zve10：售价 5000 元，日租 80 元，租期 3 天起」，
  **不要**拆成「索尼zve10售价5000」「日租80」「租期3天起」三条 ——
  拆开之后每条都是残缺的，检索时都对不上，等于白学。
  没有就给空数组 []。
* **fact_question**：**客户会怎么问**这件事 —— 要写成一句**通用的、别的客户也会问**的话，
  例如「索尼zve10多少钱一天」。**绝对不要照抄客户原话**：客户原话往往带着
  这场对话特有的上下文（"你们客服说有""你去查一下"），照抄之后
  下次别人换个问法就检索不到，学到的知识等于没用。
  没有 facts 就给空串 ""。

铁律：不确定的一律不要写。宁可少写，也不要猜。"""


def _parse_analysis(raw: str) -> Optional[dict]:
    """从模型输出里抠出 JSON。模型爱加解释和```，所以宽松点。"""
    if not raw:
        return None
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None

    def _list(key, limit):
        out = []
        for x in (data.get(key) or []):
            s = re.sub(r"\s+", " ", str(x)).strip()
            if s:
                out.append(s)
            if len(out) >= limit:
                break
        return out

    return {
        "style_rules": _list("style_rules", 2),
        "facts": _list("facts", 5),
        "tone_sample": str(data.get("tone_sample") or "").strip()[:200],
        # ★ 通用问法：**别丢掉**。原来这里没带出来，于是 propose 只能拿客户原话
        #   当问题，学到的条目检索不到（见 propose 里的说明）。
        "fact_question": str(data.get("fact_question") or "").strip()[:200],
    }


def analyze(question: str, draft: str, human: str,
            timeout: float = 30.0) -> Optional[dict]:
    """让 LLM 分离"语气"和"新事实"。失败返回 None（调用方走规则兜底）。"""
    try:
        from rag.llm_client import chat
        msgs = [
            {"role": "system", "content": ANALYZE_SYSTEM},
            {"role": "user", "content": (
                f"客户原话：{question or '（无）'}\n\n"
                f"AI 草稿：{draft or '（空）'}\n\n"
                f"店主实际发出：{human}")},
        ]
        raw = asyncio.run(asyncio.wait_for(
            chat(msgs, temperature=0.2, max_tokens=400), timeout=timeout))
    except Exception as e:
        logger.warning(f"改稿分析失败（走规则兜底）: {e}")
        return None
    got = _parse_analysis(raw)
    if got is None:
        logger.warning("改稿分析输出解析不了，走规则兜底")
    return got


def fallback(question: str, draft: str, human: str) -> dict:
    """LLM 不可用时的兜底：**只从数字下手**。

    终稿里有草稿和原话都没有的数字 → 当"新事实"交给人工确认（价格/押金最危险）。
    非数字的新说法（"周末也发货"）抓不到 —— 这是没网时的固有损失，不装作学到了。
    """
    known = set(re.findall(r"\d+", f"{question} {draft}"))
    fresh = [n for n in re.findall(r"\d+", human) if n not in known]
    return {"style_rules": [], "facts": [human] if fresh else [],
            "tone_sample": human}


# ── 规则去重 ──────────────────────────────────────────────────────────
#
# 先说结论：**语义相似度在这里不可靠**。实测同一件事的规则余弦最高只有 0.681
# （"短句为主，语气直接" vs "省略主语和客套话，直接报信息"），而不相干的也能到
# 0.528（"报价前先问客户拍什么" vs "称呼用你不用您"）—— 区间重叠，阈值分不开。
# 所以主力是下面的 LLM 归并，余弦只做一次便宜的"完全等价"兜底。

MERGE_THRESHOLD = 0.82
CONSOLIDATE_AT = 6
MAX_RULES_KEPT = 6


def _cos(a, b) -> float:
    s = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return s / (na * nb) if na and nb else 0.0


def similar_rule(new_rule: str, existing: List[dict],
                 threshold: float = MERGE_THRESHOLD) -> Optional[dict]:
    """已有规则里有没有**几乎逐字相同**的（阈值定得很高，只拦等价说法）。"""
    if not existing or not new_rule.strip():
        return None
    try:
        from rag.embed_query import embed_query
        v_new = embed_query(new_rule)
    except Exception as e:
        logger.debug(f"规则去重跳过（嵌入不可用）: {e}")
        return None
    best, best_s = None, threshold
    for r in existing:
        text = r.get("rule") or ""
        if not text:
            continue
        try:
            s = _cos(v_new, embed_query(text))
        except Exception:
            continue
        if s >= best_s:
            best, best_s = r, s
    return best


def learn_rule(rule: str, existing: List[dict], evidence: str = "",
               cfg: dict = None, base=None) -> Optional[dict]:
    """学一条规则：几乎等价就加命中次数，否则新增。"""
    dup = similar_rule(rule, existing)
    if dup is not None:
        return ls.bump_rule(dup["id"], base)
    return ls.add_style_rule(rule, evidence=evidence, cfg=cfg, base=base)


# ── 规则归并（LLM） ──────────────────────────────────────────────────

CONSOLIDATE_SYSTEM = """下面是从店主改稿里攒下来的"说话习惯"清单，里面有重复和重叠。
把它们合并成**最多 6 条互不重复**的习惯，每条不超过 25 字，要具体到能照做。

* 说的是同一件事的合并成一条（"短句为主"、"短句口语"、"省略客套话" 其实是一件事）。
* 保留信息量最大的说法，丢掉空话。
* 不要新增原清单里没有的习惯。
* 只输出 JSON：{"rules": ["...", "..."]}"""


def _llm_consolidate(rules: List[dict], timeout: float = 40.0) -> Optional[List[str]]:
    try:
        from rag.llm_client import chat
        listing = "\n".join(f"- {r.get('rule')}（学过 {r.get('hits', 1)} 次）"
                            for r in rules if r.get("rule"))
        raw = asyncio.run(asyncio.wait_for(
            chat([{"role": "system", "content": CONSOLIDATE_SYSTEM},
                  {"role": "user", "content": listing}],
                 temperature=0.2, max_tokens=300), timeout=timeout))
        m = re.search(r"\{.*\}", raw or "", re.S)
        if not m:
            return None
        got = json.loads(m.group(0)).get("rules") or []
        out = [re.sub(r"\s+", " ", str(x)).strip() for x in got]
        return [x for x in out if x][:MAX_RULES_KEPT] or None
    except Exception as e:
        logger.warning(f"规则归并失败（保留原清单）: {e}")
        return None


def consolidate(cfg: dict = None, base=None) -> int:
    """清单超过 ``CONSOLIDATE_AT`` 条就归并一次。

    **天然限流**：归并后条数一定 ≤ 6，所以要等它重新长到 7 条以上才会再跑一次
    （不是每次改稿都调 LLM）。
    """
    try:
        rules = ls.style_rules(base)
        if len(rules) <= CONSOLIDATE_AT:
            return len(rules)
        merged = _llm_consolidate(rules)
        if not merged or len(merged) >= len(rules):
            return len(rules)
        ls.replace_rules(merged, base)
        logger.info(f"规则归并：{len(rules)} 条 → {len(merged)} 条")
        return len(merged)
    except Exception as e:
        logger.warning(f"归并出错（保留原清单）: {e}")
        return len(ls.style_rules(base))


# ── 学习入口：先给候选，用户勾完才写 ────────────────────────────────

def propose(question: str, draft: str, human: str, cfg: dict = None,
            context_chunks: Optional[list] = None, base=None) -> dict:
    """分析这次改动，**只产出候选清单，什么都不写库**。

    返回的结构交给界面勾选：``rules`` / ``tone`` / ``facts`` 每项带一个 ``key``。

    **为什么不直接学**：学到的语气会直接影响之后所有回复。让用户看一眼再决定，
    比事后去「学到的」页里删要省事得多 —— 而且"一次性的说法"根本不该进来。
    """
    out = {"ok": False, "skipped": "", "used_llm": False,
           "customer_question": question or "", "draft": draft or "",
           "human": (human or "").strip(), "rules": [], "tone": None, "facts": []}
    try:
        if not ls.enabled(cfg):
            return {**out, "skipped": "学习已关闭"}
        human = (human or "").strip()
        if not human:
            return {**out, "skipped": "人工回复是空的"}
        if ls.norm(human) == ls.norm(draft):
            return {**out, "skipped": "没改动，不学"}

        res = analyze(question, draft, human)
        if res is None:
            res = fallback(question, draft, human)
        else:
            out["used_llm"] = True

        existing = ls.style_rules(base)
        for i, rule in enumerate(res.get("style_rules", [])[:2]):
            dup = similar_rule(rule, existing)
            out["rules"].append({"key": f"r{i}", "text": rule,
                                 "already": dup.get("rule") if dup else ""})

        tone_text = (res.get("tone_sample") or human).strip()
        if tone_text:
            masked = ls.mask(tone_text)
            out["tone"] = {"key": "t", "text": masked,
                           "already": bool(ls.find_tone(masked, base))}

        seen = " ".join(context_chunks or [])
        # ★ 2026-10-06 修：问题**不再照抄客户原话**。
        #   原来每条 fact 都挂 `question`（客户原话），实测学出来的条目长这样：
        #       客户问题: 我就要zve10，你们客服说有，你去查一下
        #       销售回答: 日租80
        #   问题里全是那场对话特有的上下文，答案还是个碎片 ——
        #   客户正常问「索尼zve10日租多少」根本检索不到（实测 top1 是别的机型）。
        #   现在优先用模型给的**通用问法** `fact_question`；模型没给就退回客户原话
        #   （至少不丢东西），但那种情况会记一条日志，方便发现模型没照做。
        gen_q = (res.get("fact_question") or "").strip()
        if not gen_q and res.get("facts"):
            logger.info("改稿分析没给通用问法，退回客户原话当问题（检索命中率会低）")
        for i, claim in enumerate(res.get("facts", [])[:5]):
            if seen and ls.norm(claim) in ls.norm(seen):
                continue          # 资料里已经有了，不算新知识
            out["facts"].append({"key": f"f{i}", "claim": claim,
                                 "question": gen_q or (question or "").strip()})
        out["ok"] = True
        return out
    except Exception as e:
        logger.exception(f"分析改稿出错: {e}")
        return {**out, "skipped": f"分析出错：{e}"}


def commit(proposal: dict, keys, cfg: dict = None, base=None,
           qdrant=None, collection: str = None) -> dict:
    """把用户勾选的候选写进去（``keys`` 是勾中的 ``key`` 集合）。

    勾中的**新说法**直接写进资料库（带历史可撤销）；没给 ``qdrant`` 时退回
    「待确认」列表，等用户在「学到的」页处理 —— 总之不会悄悄丢掉。
    """
    keys = set(keys or [])
    res = {"rules": 0, "tones": 0, "facts": 0, "fact_errors": []}
    if not proposal or not proposal.get("ok"):
        return res

    existing = ls.style_rules(base)
    for r in proposal.get("rules", []):
        if r["key"] in keys and learn_rule(r["text"], existing,
                                           evidence=proposal.get("human", ""),
                                           cfg=cfg, base=base):
            res["rules"] += 1
            existing = ls.style_rules(base)

    tone = proposal.get("tone")
    if tone and tone["key"] in keys:
        if ls.add_tone_sample(tone["text"], question=proposal.get("customer_question", ""),
                              draft=proposal.get("draft", ""), cfg=cfg, base=base):
            res["tones"] += 1

    for f in proposal.get("facts", []):
        if f["key"] not in keys:
            continue
        if qdrant is None:
            if ls.add_fact(f["question"], f["claim"], proposal.get("draft", ""),
                           base=base, cfg=cfg):
                res["facts"] += 1
            continue
        try:
            from rag import kb_history, kb_tools
            added = kb_tools.add_entry(qdrant, f["question"], f["claim"],
                                       source="人工改稿学到", collection=collection)
            kb_history.record(added["id"], "", "人工改稿学到", reason="学到的说法入库")
            res["facts"] += 1
        except Exception as e:
            logger.warning(f"学到的说法入库失败: {e}")
            res["fact_errors"].append(f"{f['claim']}（{e}）")
            ls.add_fact(f["question"], f["claim"], proposal.get("draft", ""),
                        base=base, cfg=cfg)

    res["rules_total"] = consolidate(cfg=cfg, base=base)
    return res


def capture(question: str, draft: str, human: str, cfg: dict = None,
            context_chunks: Optional[list] = None,
            base=None) -> dict:
    """一次性学完（分析 + 全选）。没有界面时用；有界面就走 propose + commit。"""
    p = propose(question, draft, human, cfg=cfg,
                context_chunks=context_chunks, base=base)
    if not p.get("ok"):
        return {"enabled": ls.enabled(cfg), "skipped": p.get("skipped", ""),
                "rules": 0, "tones": 0, "facts": 0, "used_llm": p.get("used_llm")}
    keys = [r["key"] for r in p["rules"]]
    if p.get("tone"):
        keys.append(p["tone"]["key"])
    keys += [f["key"] for f in p["facts"]]
    res = commit(p, keys, cfg=cfg, base=base)
    res["enabled"] = True
    res["skipped"] = ""
    res["used_llm"] = p.get("used_llm")
    return res


def record_direct_send(question: str, answer: str, cfg: dict = None,
                       base=None) -> Optional[dict]:
    """待人工页**直接发送**了 AI 草稿 → 把「客户问题 + 发出的回答」收进待确认。

    等于店主用行动认可了这个回答。收进来之后点「采纳」就进资料库，
    下次同一个问题检索分就高了（不用再转人工）。

    **为什么不顺便学语气**：这句是 AI 自己写的，拿它当口吻样本等于让机器人学自己，
    越学越偏（第一版故意没做）。想开的话这里加一行 add_tone_sample 就行。
    """
    if not ls.enabled(cfg):
        return None
    question = (question or "").strip()
    answer = (answer or "").strip()
    if not answer:
        return None
    return ls.add_fact(question, answer, draft=answer, base=base, cfg=cfg,
                       source="直发确认")


def capture_async(question: str, draft: str, human: str, cfg: dict = None,
                  context_chunks: Optional[list] = None, base=None,
                  on_done=None) -> None:
    """后台线程里跑 capture —— 学习要调 LLM，绝不能卡住界面或发送。"""
    import threading

    def work():
        res = capture(question, draft, human, cfg=cfg,
                      context_chunks=context_chunks, base=base)
        logger.info(f"改稿学习结果: {res}")
        if on_done:
            try:
                on_done(res)
            except Exception:
                pass

    threading.Thread(target=work, daemon=True).start()


# ── 注入提示词 ────────────────────────────────────────────────────────

BLOCK_HEADER = "【店主说话的习惯 —— 从人工改稿里学到的】"
BLOCK_RULES = ("这些只影响**怎么说**，不得改变任何事实：价格、库存、租期、政策一律"
               "以资料为准。")
BLOCK_TONES = ("以下是他真实说过的话（数字已遮蔽）。**只是例子，绝对不要照抄其中的数字或型号**。")


def learned_block(cfg: dict = None, base=None) -> str:
    """生成"追加"到 system 提示词尾部的一段。

    **为什么是追加而不是写进 ``prompts/system.md``**：那是用户可编辑的文件，
    程序往里写会覆盖用户改动，"恢复默认"也会变得莫名其妙。追加式还有个好处：
    用户随时能在「学到的」页看到 AI 到底把什么喂给了模型。
    """
    if not ls.enabled(cfg):
        return ""
    rules = [r for r in ls.style_rules(base) if r.get("rule")][:MAX_RULES_IN_BLOCK]
    tones = [t for t in ls.tone_samples(base) if t.get("text")]
    if not rules and not tones:
        return ""

    parts: List[str] = []
    if rules:
        lines = "\n".join(f"- {r['rule']}" for r in rules)
        parts.append(f"{BLOCK_HEADER}\n{BLOCK_RULES}\n{lines}")

    if tones:
        picks = tones[:MAX_TONES_IN_BLOCK]
        lines = "\n".join(f"- {t['text']}" for t in picks)
        parts.append(f"{BLOCK_TONES}\n{lines}")

        # 改稿前后对照 —— 比孤立句子更能教会模型"该怎么改"
        pairs = [t for t in tones if t.get("draft") and t.get("question")][:MAX_PAIRS_IN_BLOCK]
        if pairs:
            rows = []
            for t in pairs:
                rows.append(f"客户问：{t['question']}\n"
                            f"AI 原来写：{t['draft']}\n"
                            f"店主改成：{t['text']}")
            parts.append("【他平时怎么改 AI 的话 —— 学表达方式，不要照抄内容】\n"
                         + "\n\n".join(rows))
    return "\n\n".join(parts)


def append_to_system(system_text: str, cfg: dict = None, base=None) -> str:
    """把学到的内容追加到 system 提示词尾部（没有就原样返回）。"""
    block = learned_block(cfg=cfg, base=base)
    if not block:
        return system_text
    return f"{system_text}\n\n{block}"


def stats(cfg: dict = None, base=None) -> dict:
    got = ls.counts(base)
    got["enabled"] = ls.enabled(cfg)
    return got
