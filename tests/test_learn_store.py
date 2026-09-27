# tests/test_learn_store.py
"""学习记录的存储层：去重 / 遮蔽 / 上限 / 黑名单 / 采纳次数。

**遮蔽是重点**：学来的句子会直接进提示词，里面带的价格会被模型抄到别的回答里。
"""

import pytest

from rag import learn_store as ls


@pytest.fixture
def base(tmp_path, monkeypatch):
    monkeypatch.setattr(ls, "LEARN_DIR", tmp_path / "learned")
    return tmp_path / "learned"


# ── 遮蔽 ──────────────────────────────────────────────────────────────

def test_mask_money():
    assert ls.mask("押金 4000，还回来就退你") == "押金 ▢，还回来就退你"


def test_mask_keeps_model_numbers():
    """A7M4 里的数字是型号不是价格，遮了反而看不懂。"""
    assert ls.mask("A7M4 明天还剩一台") == "A7M4 明天还剩一台"
    assert ls.mask("F2.8 那颗") == "F2.8 那颗"


def test_mask_phone():
    assert ls.mask("打我电话 13800138000") == "打我电话 ▢"


def test_mask_at_nickname():
    assert ls.mask("好的 @客户D 我看看") == "好的 ▢ 我看看"


def test_mask_single_digit_untouched():
    """单个数字不动 —— 「还剩 3 台」遮掉反而失真。"""
    assert ls.mask("还剩 3 台") == "还剩 3 台"


def test_mask_collapses_whitespace():
    assert ls.mask("好的   我看看\n\n嗯") == "好的 我看看 嗯"


# ── 归一化 / 去重 ─────────────────────────────────────────────────────

def test_norm_ignores_punctuation_and_space():
    assert ls.norm("好的，我看看！") == ls.norm("好的 我看看")


def test_norm_detects_real_change():
    assert ls.norm("好的我看看") != ls.norm("可以的稍等")


def test_content_hash_stable():
    assert ls.content_hash("好的，我看看") == ls.content_hash("好的 我看看")


# ── 语气规则 ──────────────────────────────────────────────────────────

def test_add_and_list_rule(base):
    assert ls.add_style_rule("句子拆短，句尾加「哈」", base=base)
    rows = ls.style_rules(base)
    assert rows[0]["rule"] == "句子拆短，句尾加「哈」"
    assert rows[0]["hits"] == 1


def test_duplicate_rule_bumps_hits(base):
    ls.add_style_rule("句子拆短，句尾加「哈」", base=base)
    ls.add_style_rule("句子拆短，句尾加「哈」", base=base)
    rows = ls.style_rules(base)
    assert len(rows) == 1
    assert rows[0]["hits"] == 2


def test_rule_too_short_or_too_long_rejected(base):
    assert ls.add_style_rule("短", base=base) is None
    assert ls.add_style_rule("长" * 100, base=base) is None
    assert ls.style_rules(base) == []


def test_rule_sorting_by_hits(base):
    ls.add_style_rule("偶尔说一次的习惯", base=base)
    for _ in range(3):
        ls.add_style_rule("经常这么说的习惯", base=base)
    assert ls.style_rules(base)[0]["rule"] == "经常这么说的习惯"


def test_rule_evidence_is_masked(base):
    ls.add_style_rule("别用客套话开场", evidence="押金 4000 你直接说就行", base=base)
    assert "4000" not in ls.style_rules(base)[0]["evidence"]


# ── 口吻样本 ──────────────────────────────────────────────────────────

def test_add_tone_sample_masks(base):
    ls.add_tone_sample("押金 4000，还回来就退你，放心", base=base)
    t = ls.tone_samples(base)[0]
    assert t["text"] == "押金 ▢，还回来就退你，放心"
    assert "4000" not in t["text"]


def test_tone_sample_dedup(base):
    ls.add_tone_sample("在的在的，你说", base=base)
    ls.add_tone_sample("在的在的 你说", base=base)      # 只差标点
    rows = ls.tone_samples(base)
    assert len(rows) == 1 and rows[0]["hits"] == 2


def test_tone_sample_keeps_question_and_draft(base):
    ls.add_tone_sample("我看看哈", question="A7M4有货吗", draft="您好，我为您查询", base=base)
    t = ls.tone_samples(base)[0]
    assert t["question"] == "A7M4有货吗"
    assert "查询" in t["draft"]


def test_empty_tone_sample_rejected(base):
    assert ls.add_tone_sample("   ", base=base) is None


def test_tone_samples_newest_first(base):
    ls.add_tone_sample("第一条", base=base)
    ls.add_tone_sample("第二条", base=base)
    assert ls.tone_samples(base)[0]["text"] == "第二条"


# ── 待确认的新知识 ────────────────────────────────────────────────────

def test_add_fact_starts_pending(base):
    ls.add_fact("周末发货吗", "周末也发货", base=base)
    rows = ls.facts("pending", base)
    assert rows[0]["claim"] == "周末也发货"
    assert rows[0]["status"] == "pending"


def test_fact_already_in_retrieved_chunks_is_not_new(base):
    """资料里本来就有的说法不用确认。"""
    assert ls.add_fact("周末发货吗", "周末也发货", chunk_seen=True, base=base) is None
    assert ls.facts("pending", base) == []


def test_fact_status_change(base):
    ls.add_fact("周末发货吗", "周末也发货", base=base)
    fid = ls.facts("pending", base)[0]["id"]
    assert ls.set_fact_status(fid, "accepted", base) is True
    assert ls.facts("pending", base) == []
    assert ls.facts("accepted", base)[0]["claim"] == "周末也发货"


def test_fact_too_short_rejected(base):
    assert ls.add_fact("问", "嗯", base=base) is None


# ── 上限与删除 ────────────────────────────────────────────────────────

def test_max_items_drops_oldest(base):
    cfg = {"kb": {"learn": {"max_items": 10}}}       # 下限 10
    marks = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰"           # 15 个，避开数字（数字会被遮蔽）
    for m in marks:
        ls.add_tone_sample(f"{m}句人话", cfg=cfg, base=base)
    rows = ls.tone_samples(base)
    assert len(rows) == 10
    assert rows[0]["text"] == "辰句人话"              # 最新的在
    assert "甲句人话" not in [r["text"] for r in rows]  # 最旧的滚掉


def test_max_items_bounds():
    assert ls.max_items({"kb": {"learn": {"max_items": 1}}}) == 10      # 下限
    assert ls.max_items({"kb": {"learn": {"max_items": 9999}}}) == 500  # 上限
    assert ls.max_items({}) == ls.DEFAULT_MAX_ITEMS
    assert ls.max_items({"kb": {"learn": {"max_items": "坏了"}}}) == ls.DEFAULT_MAX_ITEMS


def test_delete_item(base):
    ls.add_tone_sample("一句人话", base=base)
    tid = ls.tone_samples(base)[0]["id"]
    assert ls.delete_item(ls.TONE_SAMPLES, tid, base) is True
    assert ls.tone_samples(base) == []
    assert ls.delete_item(ls.TONE_SAMPLES, "不存在", base) is False


# ── 黑名单 ────────────────────────────────────────────────────────────

def test_block_stops_relearning(base):
    ls.add_tone_sample("这句话学错了", base=base)
    tid = ls.tone_samples(base)[0]["id"]
    ls.drop_and_block(ls.TONE_SAMPLES, tid, base)

    assert ls.tone_samples(base) == []
    assert ls.add_tone_sample("这句话学错了", base=base) is None      # 不再学进来
    assert ls.add_tone_sample("这句话 学错了", base=base) is None      # 标点差异也算同一条


def test_blocked_count(base):
    ls.block(ls.STYLE_RULES, "别学这条", base)
    assert ls.blocked_count(base) == 1


def test_blocked_rule_rejected(base):
    ls.block(ls.STYLE_RULES, "句子拆短句尾加哈", base)
    assert ls.add_style_rule("句子拆短句尾加哈", base=base) is None


# ── 采纳次数 ──────────────────────────────────────────────────────────

def test_bump_accept_counts(base):
    ls.bump_accept("A7M4 有货吗", "有货的", base=base)
    ls.bump_accept("A7M4 有货吗", "有货的", base=base)
    rows = ls.accepts(base)
    assert rows[0]["count"] == 2
    assert rows[0]["question"] == "A7M4 有货吗"


def test_bump_accept_separates_questions(base):
    ls.bump_accept("问题一", base=base)
    ls.bump_accept("问题二", base=base)
    assert len(ls.accepts(base)) == 2


def test_bump_accept_empty_question(base):
    assert ls.bump_accept("", base=base) == {}
    assert ls.accepts(base) == []


# ── 统计 / 清空 / 容错 ────────────────────────────────────────────────

def test_counts(base):
    ls.add_style_rule("一条语气规则", base=base)
    ls.add_tone_sample("一句人话", base=base)
    ls.add_fact("问", "一条够长的新说法", base=base)
    c = ls.counts(base)
    assert c == {"rules": 1, "tones": 1, "pending_facts": 1, "blocked": 0, "accepts": 0}


def test_clear(base):
    ls.add_tone_sample("一句人话", base=base)
    ls.bump_accept("问题", base=base)
    ls.clear(base)
    assert ls.counts(base)["tones"] == 0
    assert ls.accepts(base) == []


def test_corrupt_line_ignored(base):
    base.mkdir(parents=True, exist_ok=True)
    (base / ls.TONE_SAMPLES).write_text(
        '{"id":"a","text":"好的","ts":"2026-01-01 00:00:00"}\n不是json\n', encoding="utf-8")
    assert [r["text"] for r in ls.tone_samples(base)] == ["好的"]


def test_reads_missing_dir(base):
    assert ls.tone_samples(base) == []
    assert ls.style_rules(base) == []
    assert ls.facts("pending", base) == []
    assert ls.counts(base)["tones"] == 0


# ── 开关：必须能从 config.json 读到 ──────────────────────────────────

def test_enabled_defaults_true():
    assert ls.enabled({}) is True
    assert ls.enabled({"kb": {"learn": {"enabled": False}}}) is False


def test_enabled_reads_config_file_when_cfg_none(tmp_path, monkeypatch):
    """生成回复时手上没有内存 config，关掉学习必须真的生效。"""
    import json
    (tmp_path / "config.json").write_text(
        json.dumps({"kb": {"learn": {"enabled": False}}}), encoding="utf-8")
    monkeypatch.setattr(ls, "HERE", tmp_path)
    monkeypatch.setattr(ls, "_cfg_cache", {"mtime": None, "cfg": {}})

    assert ls.enabled() is False
    assert ls.max_items() == ls.DEFAULT_MAX_ITEMS


def test_config_cache_invalidated_on_change(tmp_path, monkeypatch):
    import json
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"kb": {"learn": {"enabled": False}}}), encoding="utf-8")
    monkeypatch.setattr(ls, "HERE", tmp_path)
    monkeypatch.setattr(ls, "_cfg_cache", {"mtime": None, "cfg": {}})
    assert ls.enabled() is False

    import os
    import time
    time.sleep(0.01)
    p.write_text(json.dumps({"kb": {"learn": {"enabled": True}}}), encoding="utf-8")
    os.utime(p, (time.time() + 5, time.time() + 5))
    assert ls.enabled() is True


def test_config_missing_file():
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        import rag.learn_store as mod
        old = mod.HERE
        try:
            mod.HERE = Path(d)
            mod._cfg_cache = {"mtime": None, "cfg": {}}
            assert mod.enabled() is True        # 没配置也不能把学习搞坏
        finally:
            mod.HERE = old
