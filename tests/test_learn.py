# tests/test_learn.py
"""从人工改稿里学：语气自动学、事实等确认、注入提示词。

LLM 全部打桩 —— 这里要验的是"分类和边界"，不是模型水平。
"""

import pytest

from rag import learn, learn_store as ls


@pytest.fixture
def base(tmp_path, monkeypatch):
    d = tmp_path / "learned"
    monkeypatch.setattr(ls, "LEARN_DIR", d)
    # ★ 不读仓库里的 config.json：学习开关是用户可改的（关掉也合法），
    #   用例不该跟着本机配置时红时绿。这里显式当作"开着"，
    #   要测"关掉"的用例自己传 cfg（见 test_capture_disabled_by_switch）。
    monkeypatch.setattr(ls, "_load_cfg",
                        lambda: {"kb": {"learn": {"enabled": True}}})
    return d


@pytest.fixture
def no_llm(monkeypatch):
    """让 LLM 分析失败 → 走规则兜底。"""
    monkeypatch.setattr(learn, "analyze", lambda *a, **k: None)


def _llm(monkeypatch, payload):
    monkeypatch.setattr(learn, "analyze", lambda *a, **k: payload)


# ── 解析模型输出 ──────────────────────────────────────────────────────

def test_parse_plain_json():
    got = learn._parse_analysis('{"style_rules":["句子短"],"tone_sample":"好嘞","facts":[]}')
    # `fact_question` 是 2026-10-07 加的（通用问法，不许照抄客户原话 ——
    # 见 test_learn_switch.py 里那条"学了但检索不到"的回归）。模型没给就是空串。
    assert got == {"style_rules": ["句子短"], "tone_sample": "好嘞",
                   "facts": [], "fact_question": ""}


def test_parse_json_inside_markdown():
    raw = '分析如下：\n```json\n{"style_rules":["别用客套话"],"tone_sample":"","facts":["周末发货"]}\n```\n完毕'
    got = learn._parse_analysis(raw)
    assert got["style_rules"] == ["别用客套话"]
    assert got["facts"] == ["周末发货"]


def test_parse_garbage():
    assert learn._parse_analysis("我不知道") is None
    assert learn._parse_analysis("") is None
    assert learn._parse_analysis("{坏掉的 json}") is None


def test_parse_limits_counts():
    many = {"style_rules": [f"规则{i}" for i in range(10)],
            "facts": [f"说法{i}" for i in range(10)], "tone_sample": "x"}
    got = learn._parse_analysis(__import__("json").dumps(many, ensure_ascii=False))
    assert len(got["style_rules"]) == 2
    assert len(got["facts"]) == 5


def test_parse_drops_empty_entries():
    got = learn._parse_analysis('{"style_rules":["  ","好的"],"facts":[" "],"tone_sample":""}')
    assert got["style_rules"] == ["好的"]
    assert got["facts"] == []


# ── capture：学什么、不学什么 ─────────────────────────────────────────

def test_capture_learns_tone_and_rules(base, monkeypatch):
    _llm(monkeypatch, {"style_rules": ["句子拆短，句尾加「哈」"],
                       "tone_sample": "在的在的，你说", "facts": []})
    res = learn.capture("A7M4有货吗", "您好，我为您查询一下库存情况",
                        "在的在的，你说", base=base)
    assert res["used_llm"] is True
    assert (res["rules"], res["tones"], res["facts"]) == (1, 1, 0)
    assert ls.style_rules(base)[0]["rule"] == "句子拆短，句尾加「哈」"
    assert ls.tone_samples(base)[0]["text"] == "在的在的，你说"


def test_capture_fact_goes_to_pending_not_kb(base, monkeypatch):
    _llm(monkeypatch, {"style_rules": [], "tone_sample": "周末也发的",
                       "facts": ["周末也发货，下午三点前的单当天走"]})
    res = learn.capture("周末发货吗", "您好，请咨询客服", "周末也发的，下午三点前的单当天走",
                        base=base)
    assert res["facts"] == 1
    facts = ls.facts("pending", base)
    assert facts[0]["claim"] == "周末也发货，下午三点前的单当天走"
    assert facts[0]["question"] == "周末发货吗"


def test_capture_skips_when_not_edited(base, monkeypatch):
    _llm(monkeypatch, {"style_rules": ["不该被用到"], "tone_sample": "x", "facts": ["y"]})
    res = learn.capture("问", "好的，我看看哈", "好的，我看看哈", base=base)
    assert res["skipped"] == "没改动，不学"
    assert ls.counts(base)["tones"] == 0


def test_capture_skips_punctuation_only_change(base, monkeypatch):
    """只改了标点和空格不算改稿 —— 不然每一句都会被当成新样本。"""
    _llm(monkeypatch, {"style_rules": ["x"], "tone_sample": "y", "facts": []})
    res = learn.capture("问", "好的，我看看！", "好的 我看看", base=base)
    assert res["skipped"] == "没改动，不学"


def test_capture_skips_empty_human(base, monkeypatch):
    res = learn.capture("问", "草稿", "   ", base=base)
    assert res["skipped"] == "人工回复是空的"


def test_capture_disabled_by_switch(base, monkeypatch):
    _llm(monkeypatch, {"style_rules": ["x"], "tone_sample": "y", "facts": []})
    res = learn.capture("问", "草稿", "终稿", cfg={"kb": {"learn": {"enabled": False}}},
                        base=base)
    assert res["enabled"] is False
    assert ls.counts(base)["tones"] == 0


def test_capture_never_raises(base, monkeypatch):
    """分析出错绝不能把发送流程带崩（学不到是小事）。"""
    def boom(*a, **k):
        raise RuntimeError("爆炸")

    monkeypatch.setattr(learn, "analyze", boom)
    res = learn.capture("问", "草稿", "终稿", base=base)
    assert "出错" in res["skipped"]


# ── 先给候选、勾完才写（propose / commit） ───────────────────────────

def test_propose_writes_nothing(base, monkeypatch):
    """最重要的一条：没点确认之前，库里一个字都不能多。"""
    _llm(monkeypatch, {"style_rules": ["句子拆短"], "tone_sample": "好嘞",
                       "facts": ["周末也发货"]})
    p = learn.propose("周末发货吗", "您好，请咨询客服", "好嘞，周末也发货", base=base)
    assert p["ok"] is True
    assert ls.counts(base) == {"rules": 0, "tones": 0, "pending_facts": 0,
                               "blocked": 0, "accepts": 0}


def test_propose_lists_candidates_with_keys(base, monkeypatch):
    _llm(monkeypatch, {"style_rules": ["句子拆短", "句尾带哈"], "tone_sample": "好嘞",
                       "facts": ["周末也发货"]})
    p = learn.propose("周末发货吗", "您好，请咨询客服", "好嘞，周末也发货", base=base)
    assert [r["key"] for r in p["rules"]] == ["r0", "r1"]
    assert p["tone"]["key"] == "t"
    assert p["facts"][0]["key"] == "f0"
    assert p["facts"][0]["question"] == "周末发货吗"
    assert p["customer_question"] == "周末发货吗"
    assert p["human"] == "好嘞，周末也发货"


def test_propose_masks_tone_sample(base, monkeypatch):
    _llm(monkeypatch, {"style_rules": [], "tone_sample": "押金 4000 就退你", "facts": []})
    p = learn.propose("押金多少", "草稿", "押金 4000 就退你", base=base)
    assert p["tone"]["text"] == "押金 ▢ 就退你"


def test_propose_marks_already_learned(base, monkeypatch):
    ls.add_style_rule("句子拆短", base=base)
    ls.add_tone_sample("好嘞", base=base)
    _llm(monkeypatch, {"style_rules": ["句子拆短"], "tone_sample": "好嘞", "facts": []})
    p = learn.propose("问", "草稿", "好嘞", base=base)
    assert p["tone"]["already"] is True
    assert p["rules"][0]["already"] == "句子拆短"      # 界面提示"已经学过"


def test_propose_skips_unchanged(base, monkeypatch):
    _llm(monkeypatch, {"style_rules": ["x"], "tone_sample": "y", "facts": []})
    p = learn.propose("问", "好的，我看看", "好的 我看看", base=base)
    assert p["ok"] is False
    assert p["skipped"] == "没改动，不学"


def test_propose_respects_switch(base, monkeypatch):
    p = learn.propose("问", "草稿", "终稿", cfg={"kb": {"learn": {"enabled": False}}},
                      base=base)
    assert p["ok"] is False
    assert p["skipped"] == "学习已关闭"


def test_propose_filters_facts_already_in_chunks(base, monkeypatch):
    _llm(monkeypatch, {"style_rules": [], "tone_sample": "", "facts": ["周末也发货"]})
    p = learn.propose("周末发货吗", "草稿", "周末也发货",
                      context_chunks=["营业时间：周末也发货"], base=base)
    assert p["facts"] == []


def test_commit_only_writes_selected(base, monkeypatch):
    _llm(monkeypatch, {"style_rules": ["句子拆短", "句尾带哈"], "tone_sample": "好嘞",
                       "facts": []})
    p = learn.propose("问", "草稿内容", "好嘞", base=base)
    res = learn.commit(p, {"r1"}, base=base)          # 只勾第二条
    assert res["rules"] == 1
    assert res["tones"] == 0                          # 口吻样本没勾 → 不学
    assert [r["rule"] for r in ls.style_rules(base)] == ["句尾带哈"]
    assert ls.tone_samples(base) == []


def test_commit_with_no_selection_writes_nothing(base, monkeypatch):
    _llm(monkeypatch, {"style_rules": ["句子拆短"], "tone_sample": "好嘞",
                       "facts": ["周末也发货"]})
    p = learn.propose("问", "草稿内容", "好嘞，周末也发货", base=base)
    res = learn.commit(p, set(), base=base)
    assert (res["rules"], res["tones"], res["facts"]) == (0, 0, 0)
    assert ls.counts(base)["tones"] == 0


def test_commit_all(base, monkeypatch):
    _llm(monkeypatch, {"style_rules": ["句子拆短"], "tone_sample": "好嘞",
                       "facts": ["周末也发货"]})
    p = learn.propose("问", "草稿内容", "好嘞，周末也发货", base=base)
    res = learn.commit(p, {"r0", "t", "f0"}, base=base)
    assert (res["rules"], res["tones"], res["facts"]) == (1, 1, 1)
    assert ls.tone_samples(base)[0]["text"] == "好嘞"
    assert ls.facts("pending", base)[0]["claim"] == "周末也发货"


def test_commit_ignores_bad_proposal(base):
    assert learn.commit({}, {"t"}, base=base)["tones"] == 0
    assert learn.commit({"ok": False}, {"t"}, base=base)["tones"] == 0


def test_commit_fact_writes_into_kb_when_qdrant_given(base, monkeypatch):
    """勾中的新说法直接进资料库（可撤销），不再只是排队。"""
    calls = []
    monkeypatch.setattr("rag.kb_tools.add_entry",
                        lambda c, q, a, **k: calls.append((c, q, a, k)) or {"id": "new1"})
    monkeypatch.setattr("rag.kb_history.record", lambda *a, **k: None)
    _llm(monkeypatch, {"style_rules": [], "tone_sample": "", "facts": ["周末也发货"]})
    p = learn.propose("周末发货吗", "草稿", "周末也发货", base=base)
    res = learn.commit(p, {"f0"}, base=base, qdrant="Q")
    assert res["facts"] == 1
    assert calls[0][1] == "周末发货吗"          # 用客户问句当问题
    assert calls[0][2] == "周末也发货"
    assert ls.facts("pending", base) == []      # 已经进库，不入待确认


def test_commit_fact_falls_back_to_pending_on_kb_error(base, monkeypatch):
    """入库失败不能把这条说法丢掉，退回待确认列表。"""
    def boom(*a, **k):
        raise RuntimeError("库坏了")

    monkeypatch.setattr("rag.kb_tools.add_entry", boom)
    _llm(monkeypatch, {"style_rules": [], "tone_sample": "", "facts": ["周末也发货"]})
    p = learn.propose("周末发货吗", "草稿", "周末也发货", base=base)
    res = learn.commit(p, {"f0"}, base=base, qdrant="Q")
    assert res["facts"] == 0
    assert len(res["fact_errors"]) == 1
    assert ls.facts("pending", base)[0]["claim"] == "周末也发货"

# ── 规则去重（否则 8 个注入名额会被同一件事占满） ────────────────────

def test_similar_rule_merges_paraphrase(base, monkeypatch):
    """「直接问，不客套」和「直接给结论，不客套」说的是同一件事。"""
    monkeypatch.setattr(learn, "similar_rule",
                        lambda new, existing, threshold=0: existing[0] if existing else None)
    ls.add_style_rule("直接问，不客套", base=base)
    existing = ls.style_rules(base)
    learn.learn_rule("直接给结论，不客套", existing, base=base)
    rows = ls.style_rules(base)
    assert len(rows) == 1
    assert rows[0]["hits"] == 2


def test_learn_rule_adds_when_different(base, monkeypatch):
    monkeypatch.setattr(learn, "similar_rule", lambda *a, **k: None)
    ls.add_style_rule("句子拆短", base=base)
    learn.learn_rule("报价前先问用途", ls.style_rules(base), base=base)
    assert len(ls.style_rules(base)) == 2


def test_similar_rule_no_candidates():
    assert learn.similar_rule("随便什么规则", []) is None


def test_similar_rule_survives_embedding_failure(monkeypatch):
    """嵌入模型不可用时只是不合并，不能把学习搞崩。"""
    import rag.embed_query as eq
    monkeypatch.setattr(eq, "embed_query",
                        lambda t: (_ for _ in ()).throw(RuntimeError("没模型")))
    assert learn.similar_rule("一条规则内容", [{"rule": "另一条"}]) is None


def test_commit_uses_merge_path(base, monkeypatch):
    """写入走的是 learn_rule（会合并），不是无脑新增。"""
    import inspect
    assert "learn_rule(" in inspect.getsource(learn.commit)


def test_capture_reports_learned_count_not_total(base, monkeypatch):
    """banner 要说"这次学到几条"，不能被归并后的总数覆盖。"""
    _llm(monkeypatch, {"style_rules": ["句子拆短"], "tone_sample": "好嘞", "facts": []})
    res = learn.capture("问", "草稿内容", "好嘞", base=base)
    assert res["rules"] == 1
    assert res["rules_total"] == 1


# ── 规则归并（LLM） ──────────────────────────────────────────────────

def _seed_rules(base, n):
    for i in range(n):
        ls.add_style_rule(f"第{i}个说话习惯", base=base)


def test_consolidate_skipped_below_threshold(base, monkeypatch):
    called = []
    monkeypatch.setattr(learn, "_llm_consolidate", lambda *a, **k: called.append(1))
    _seed_rules(base, learn.CONSOLIDATE_AT)
    assert learn.consolidate(base=base) == learn.CONSOLIDATE_AT
    assert called == []


def test_consolidate_merges_when_too_many(base, monkeypatch):
    _seed_rules(base, 8)
    monkeypatch.setattr(learn, "_llm_consolidate",
                        lambda *a, **k: ["句子短，不客套", "句尾常带「哈」"])
    assert learn.consolidate(base=base) == 2
    rules = ls.style_rules(base)
    assert [r["rule"] for r in rules] == ["句子短，不客套", "句尾常带「哈」"]
    assert [r["order"] for r in rules] == [0, 1]        # 顺序＝归并时的输出顺序


def test_consolidate_keeps_original_on_llm_failure(base, monkeypatch):
    _seed_rules(base, 8)
    monkeypatch.setattr(learn, "_llm_consolidate", lambda *a, **k: None)
    assert learn.consolidate(base=base) == 8
    assert len(ls.style_rules(base)) == 8


def test_consolidate_rejects_no_op_merge(base, monkeypatch):
    """归并结果没变少就当作失败（别把顺序打乱）。"""
    _seed_rules(base, 8)
    monkeypatch.setattr(learn, "_llm_consolidate",
                        lambda *a, **k: [f"第{i}个说话习惯" for i in range(8)])
    assert learn.consolidate(base=base) == 8


def test_consolidate_is_self_throttling(base, monkeypatch):
    """归并后只剩几条，要等重新长到 7 条以上才会再调 LLM。"""
    calls = []
    monkeypatch.setattr(learn, "_llm_consolidate",
                        lambda *a, **k: calls.append(1) or ["合并后的一条习惯"])
    _seed_rules(base, 7)
    learn.consolidate(base=base)
    learn.consolidate(base=base)          # 只有 1 条，不该再跑
    assert len(calls) == 1


def test_llm_consolidate_parses_json(base, monkeypatch):
    import rag.llm_client as lc
    import asyncio as _a

    async def fake_chat(msgs, **kw):
        return '```json\n{"rules":["短句，不客套","句尾带哈","报价先问用途"]}\n```'

    monkeypatch.setattr(lc, "chat", fake_chat)
    got = learn._llm_consolidate([{"rule": "x", "hits": 1}])
    assert got == ["短句，不客套", "句尾带哈", "报价先问用途"]


def test_llm_consolidate_caps_at_six(base, monkeypatch):
    import rag.llm_client as lc

    async def fake_chat(msgs, **kw):
        return '{"rules":' + str([f"规则{i}" for i in range(20)]).replace("'", '"') + '}'

    monkeypatch.setattr(lc, "chat", fake_chat)
    assert len(learn._llm_consolidate([{"rule": "x", "hits": 1}])) == 6


def test_llm_consolidate_garbage(monkeypatch):
    import rag.llm_client as lc

    async def fake_chat(msgs, **kw):
        return "我不太确定"

    monkeypatch.setattr(lc, "chat", fake_chat)
    assert learn._llm_consolidate([{"rule": "x", "hits": 1}]) is None


def test_cos_basic():
    assert learn._cos([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert learn._cos([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert learn._cos([], []) == 0.0


def test_capture_fact_already_in_chunks_not_queued(base, monkeypatch):
    _llm(monkeypatch, {"style_rules": [], "tone_sample": "", "facts": ["周末也发货"]})
    res = learn.capture("周末发货吗", "草稿", "周末也发货",
                        context_chunks=["营业时间：周末也发货，10点到17点"], base=base)
    assert res["facts"] == 0                     # 资料里已经有了，不是新知识


# ── 规则兜底（LLM 不可用） ───────────────────────────────────────────

def test_fallback_flags_new_numbers(base, no_llm):
    res = learn.capture("A7M4押金多少", "押金 4000 元", "押金 3500 元就行", base=base)
    assert res["used_llm"] is False
    assert res["facts"] == 1
    assert ls.facts("pending", base)[0]["claim"] == "押金 3500 元就行"


def test_fallback_no_new_numbers_no_facts(base, no_llm):
    res = learn.capture("有货吗", "有的", "有的哈", base=base)
    assert res["facts"] == 0
    assert res["tones"] == 1                     # 语气样本照样学到


def test_fallback_cannot_catch_non_numeric_facts(base, no_llm):
    """没网时的固有损失：不带数字的新说法抓不到，不装作学到了。"""
    res = learn.capture("周末发货吗", "您好，请咨询客服", "周末也发货的", base=base)
    assert res["facts"] == 0


def test_fallback_keeps_llm_rules_empty(base, no_llm):
    learn.capture("问", "草稿内容", "终稿内容", base=base)
    assert ls.style_rules(base) == []


# ── 注入 ──────────────────────────────────────────────────────────────

def test_learned_block_empty_when_nothing_learned(base):
    assert learn.learned_block(base=base) == ""


def test_learned_block_has_rules_and_samples(base):
    ls.add_style_rule("句子拆短，句尾加「哈」", base=base)
    ls.add_tone_sample("在的在的，你说", base=base)
    block = learn.learned_block(base=base)
    assert "句子拆短，句尾加「哈」" in block
    assert "在的在的，你说" in block
    assert "不得改变任何事实" in block
    assert "不要照抄其中的数字或型号" in block


def test_learned_block_includes_before_after_pairs(base):
    ls.add_tone_sample("我看看哈", question="A7M4有货吗", draft="您好，我为您查询",
                       base=base)
    block = learn.learned_block(base=base)
    assert "客户问：A7M4有货吗" in block
    assert "AI 原来写：您好，我为您查询" in block
    assert "店主改成：我看看哈" in block


def test_learned_block_caps_rules_and_samples(base):
    for i in range(30):
        ls.add_style_rule(f"习惯{i}要多说一句", base=base)
        ls.add_tone_sample(f"样本{i}句人话", base=base)
    block = learn.learned_block(base=base)
    assert block.count("- 习惯") == learn.MAX_RULES_IN_BLOCK
    assert block.count("- 样本") == learn.MAX_TONES_IN_BLOCK


def test_learned_block_respects_switch(base):
    ls.add_tone_sample("在的在的", base=base)
    assert learn.learned_block(cfg={"kb": {"learn": {"enabled": False}}},
                              base=base) == ""
    assert learn.learned_block(cfg={"kb": {"learn": {"enabled": True}}}, base=base) != ""


def test_learned_block_masks_numbers(base):
    """注入的内容里不能出现价格 —— 遮过了才算安全。"""
    ls.add_tone_sample("押金 4000 还回来就退你", base=base)
    assert "4000" not in learn.learned_block(base=base)


def test_append_to_system_no_block_returns_original(base):
    assert learn.append_to_system("原提示词", base=base) == "原提示词"


def test_append_to_system_adds_block(base):
    ls.add_tone_sample("在的在的", base=base)
    got = learn.append_to_system("原提示词", base=base)
    assert got.startswith("原提示词")
    assert "在的在的" in got


# ── 接进生成路径 ──────────────────────────────────────────────────────

def test_build_prompt_injects_learned(base, monkeypatch):
    """学到的内容要真的进 system 提示词，否则白学。"""
    monkeypatch.setattr(ls, "LEARN_DIR", base)
    ls.add_tone_sample("在的在的，你说", base=base)
    from rag.generator import build_prompt
    msgs = build_prompt("A7M4有货吗", ["资料片段"])
    assert "在的在的，你说" in msgs[0]["content"]
    assert "资料片段" in msgs[0]["content"]          # 原来的东西没被弄坏
    assert msgs[1]["content"] == "A7M4有货吗"


def test_build_prompt_without_learning_unchanged(base, monkeypatch):
    monkeypatch.setattr(ls, "LEARN_DIR", base)
    from rag.generator import build_prompt
    msgs = build_prompt("问一句", ["片段"])
    assert "【店主说话的习惯" not in msgs[0]["content"]


def test_smalltalk_path_injects_learned(base, monkeypatch):
    import inspect
    from rag import generator
    src = inspect.getsource(generator.generate_smalltalk_reply)
    assert "append_to_system" in src
    assert "append_to_system" in inspect.getsource(generator.build_prompt)


# ── 主程序接线 ────────────────────────────────────────────────────────

def _main_src():
    from pathlib import Path
    return (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")


def test_edit_send_carries_learn_context():
    """编辑发送必须把「客户原话 + AI 草稿」带进队列，否则学不了。"""
    s = _main_src()
    assert '"draft": item.ai_reply' in s
    assert '"question": item.customer_message' in s
    # 2026-10-03：队列项多了第 5 项「日志上下文」（给运营看板统计用），
    # 学习仍只看第 4 项 ctx —— 两者互不影响
    assert 'send_queue.put(("send", item.customer_name, edited_text, ctx, log_ctx))' in s


def test_learning_triggered_only_after_send_ok():
    """学习挂在真的发出去之后 —— 发失败（发错人中止）的话不该被学走。"""
    s = _main_src()
    assert "if send_ok:" in s
    assert "_learn_from_edit(learn_ctx, reply_text)" in s
    idx_ok = s.index("if send_ok:")
    idx_learn = s.index("_learn_from_edit(learn_ctx, reply_text)")
    assert idx_ok < idx_learn


def test_direct_send_counts_accept_but_does_not_learn():
    """直接发 AI 草稿＝采纳，但绝不学自己的话。"""
    s = _main_src()
    block = s[s.index("def _handle_pending_send"):s.index("def _handle_pending_edit")]
    assert "bump_accept" in block
    assert "capture" not in block


def test_no_direct_send_in_learning_path():
    """学习只能走 send_queue，不能自己发消息。"""
    import inspect
    src = inspect.getsource(learn)
    assert "send_queue" not in src
    assert "send_message" not in src


def test_send_queue_accepts_old_three_tuple():
    """老的 3 元组（自动回复那条路）不能因为加了第 4 项就崩。"""
    s = _main_src()
    assert "row[3] if len(row) > 3 else None" in s


# ── 「学到的」界面接线 ────────────────────────────────────────────────

def _kb_src():
    from pathlib import Path
    return (Path(__file__).resolve().parent.parent / "gui" / "kb_dialog.py").read_text(
        encoding="utf-8")


def test_kb_dialog_has_learn_tab():
    s = _kb_src()
    assert 'nb.add(f_learn, text="学到的")' in s
    assert "_build_learn_page" in s


def test_learn_page_shows_facts_rules_and_samples():
    s = _kb_src()
    assert "待确认的说法" in s
    assert "已学到的语气" in s
    assert "采纳进资料库" in s
    assert "这条别学" in s


def test_learn_page_shows_fact_source():
    """要分得清「改稿学到」和「直发确认」—— 来源不同，处理时心里有数。"""
    s = _kb_src()
    assert '"src", "来源"' in s
    assert 'f.get("source", "改稿学到")' in s


def test_direct_send_records_qa_pair():
    """待人工页直接发送 → 问答对进「学到的」待确认（且必须在发送成功之后）。"""
    import inspect
    from rag import learn
    src = inspect.getsource(learn.record_direct_send)
    assert 'source="直发确认"' in src
    main_src = _main_src()
    assert '"direct_confirm": True' in main_src
    assert "_record_direct_confirm(learn_ctx, reply_text)" in main_src
    i_ok = main_src.index("if send_ok:")
    i_rec = main_src.index("_record_direct_confirm(learn_ctx, reply_text)")
    assert i_ok < i_rec, "没发出去就不算店主认可过"


def test_direct_send_does_not_learn_tone():
    """直发学的是**问答对**，不是语气 —— 学 AI 自己写的话会越学越偏。"""
    import inspect
    from rag import learn
    src = inspect.getsource(learn.record_direct_send)
    assert "ls.add_tone_sample(" not in src, "不能把 AI 自己的话当口吻样本"
    assert "ls.add_fact(" in src


def test_learn_page_can_preview_injected_text():
    """透明：用户要能看见 AI 到底学到了什么。"""
    s = _kb_src()
    assert "预览实际注入给 AI 的内容" in s
    assert "learned_block" in s


def test_learn_page_has_master_switch_writes_config():
    s = _kb_src()
    assert 'text="自动学习（人工改完发出去就学）"' in s
    assert "save_config" in s


def test_fact_accept_goes_through_add_entry_and_history():
    """采纳＝写进资料库，走手动新增那条路（可撤销）。"""
    s = _kb_src()
    block = s[s.index("def _fact_accept"):s.index("def _fact_reject")]
    assert "kb_tools.add_entry" in block
    assert "kb_history.record" in block
    assert "set_fact_status" in block


def test_learn_page_asks_before_writing_kb():
    s = _kb_src()
    block = s[s.index("def _fact_accept"):s.index("def _fact_reject")]
    assert "askyesno" in block
    assert "核对" in block


# ── 勾选确认窗（学习前必须过这一关） ──────────────────────────────────

def _dlg_src():
    from pathlib import Path
    return (Path(__file__).resolve().parent.parent / "gui" / "learn_dialog.py").read_text(
        encoding="utf-8")


def test_review_dialog_has_checkbox_per_candidate():
    s = _dlg_src()
    assert "BooleanVar" in s
    assert "Checkbutton" in s
    assert "self._vars[key] = var" in s


def test_review_dialog_defaults_all_checked():
    s = _dlg_src()
    assert "BooleanVar(value=True)" in s


def test_review_dialog_confirm_and_cancel():
    s = _dlg_src()
    assert "确认学习选中的" in s
    assert "取消不学习" in s
    assert "def _confirm" in s and "def _cancel" in s


def test_review_cancel_means_nothing_selected():
    s = _dlg_src()
    block = s[s.index("def _cancel"):s.index("def _finish")]
    assert "self._finish(set())" in block          # 取消＝这次什么都不学


def test_review_dialog_shows_the_conversation():
    """要先看见客户问了什么、AI 原来写什么，才判断得出该不该学。"""
    s = _dlg_src()
    for label in ("客户问", "AI 草稿", "你发出去"):
        assert label in s


def test_review_dialog_marks_facts_as_kb_writes():
    s = _dlg_src()
    assert "会写进资料库" in s
    assert "报错价" in s                            # 提醒核对数字


def test_main_shows_dialog_before_writing():
    """主程序必须先 propose（不写库），弹窗之后才 commit。"""
    s = _main_src()
    block = s[s.index("def _learn_from_edit"):s.index("def _offer_learn")]
    assert "propose(" in block
    assert "commit" not in block                    # 弹窗之前不许写
    assert "root.after(0" in block                  # 弹窗必须回 Tk 线程
    offer = s[s.index("def _offer_learn"):s.index("def _commit_learn")]
    assert "open_review" in offer
    assert "commit(" not in offer                   # 勾完在 done 回调里才写


def test_main_queues_a_second_proposal_instead_of_two_windows():
    s = _main_src()
    assert '_learn_state = {"busy": False, "queue": []}' in s
    assert "_drain_learn_queue" in s
    assert 'if _learn_state["busy"]:' in s


def test_cancel_path_writes_nothing():
    s = _main_src()
    block = s[s.index("def done(keys)"):s.index("def _commit_learn")]
    assert "if keys:" in block
    assert "你取消了" in block


def test_learn_commit_runs_in_background_thread():
    """写资料库要跑嵌入，放主线程会假死。"""
    s = _main_src()
    block = s[s.index("def _commit_learn"):s.index("def _drain_learn_queue")]
    assert "threading.Thread(target=work, daemon=True).start()" in block


def test_learning_uses_responder_qdrant():
    s = _main_src()
    block = s[s.index("def _commit_learn"):s.index("def _drain_learn_queue")]
    assert 'getattr(responder, "qdrant", None)' in block


# ── 出厂默认关闭：文案与配置要一致（2026-10-04 定的产品口径） ────────────
# 决定"默认关"没问题，但**不能说成开着**：发布说明写着"人工改稿确认后它还会学"，
# 而 config.json 里是 false，用户开箱就用不上，会以为功能坏了。

def _kb_dialog_src() -> str:
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    return (root / "gui/kb_dialog.py").read_text(encoding="utf-8")


def test_config_ships_with_learning_on():
    """★ 2026-10-06 策略变更：出厂**开启**学习。

    原先出厂关闭的理由是"防口误入库"。但这条闭环本身已经有一道人工确认
    （学到的东西先进「学到的」，点「采纳进资料库」才真正写进知识库），
    而关着的代价是：店主在待人工页改完稿发出去，以为知识库会自动更新，
    结果什么都没有、界面也没提示（店主真实经历）。
    所以默认改成开，并且**关着时必须提示** —— 见
    tests/test_learn_switch.py::test_offer_learn_tells_the_user_when_learning_is_off。
    """
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    cfg = json.loads((root / "config.json").read_text(encoding="utf-8"))
    assert cfg["kb"]["learn"]["enabled"] is True
    assert "默认" in cfg["kb"]["learn"]["_comment"], "配置注释要说清这是默认值"


def test_kb_dialog_always_shows_switch_state():
    """顶部要**一直显示**开关状态，不能只在切换时闪一下。

    ★ 2026-10-06：这条原来叫 `test_kb_dialog_says_default_off`（断言界面写着
    "出厂默认是关闭的"）。策略改成默认开启后，那句文案就是**错的**了 ——
    说明"文案要跟默认值一致"这件事得有人看着。现在断言的是状态显示本身。
    """
    s = _kb_dialog_src()
    assert "学习开关" in s, "顶部要一直显示开关状态，不能只在切换时闪一下"
    assert "出厂默认是关闭的" not in s, "默认值已改成开启，这句文案过时了"


def test_kb_dialog_toggle_updates_the_state_line():
    """切换后要重跑 _learn_load 刷新状态行，不能只在 _toggle_learn 里写死一句。"""
    s = _kb_dialog_src()
    block = s[s.index("def _toggle_learn"):s.index("def _learn_preview")]
    assert "self._learn_load()" in block
