# tests/test_reply_once.py
"""「已经回复过的问题不要再回复」。

现场（2026-09-26）：客户B 21:45 问了「调戏一下Ai 哈哈哈哈 有卖什么东西 1+2*3-1=多少」，
机器人 21:45:25 已经回过「帮您确认下卖什么和这道算式，稍等。」；14 分钟后（中途重启过
程序）同一段气泡又被读成"未回复"，21:59:40 又回了一遍。

三个原因：
1. 「这条已经回过」只存在内存里 —— 重启就忘
2. 有效期只有 300 秒 —— 14 分钟前的不算数
3. 记的是**整段合并文本** —— 多读进一个新气泡整段就变了
"""

import json
import time
from pathlib import Path

import pytest

from wxbot.detector import MessageDetector


def _det(tmp_path, **cfg):
    c = {"state_dir": str(tmp_path), "seen_message_ttl_seconds": 86400}
    c.update(cfg)
    return MessageDetector(c)


# ── 基本行为 ──────────────────────────────────────────────────────────

def test_first_time_not_seen(tmp_path):
    d = _det(tmp_path)
    assert d.is_message_seen("客户B", "有卖什么东西") is False


def test_marked_message_is_seen(tmp_path):
    d = _det(tmp_path)
    d.mark_message_seen("客户B", "有卖什么东西")
    assert d.is_message_seen("客户B", "有卖什么东西") is True


def test_seen_is_per_customer(tmp_path):
    d = _det(tmp_path)
    d.mark_message_seen("客户B", "有卖什么东西")
    assert d.is_message_seen("另一个客户", "有卖什么东西") is False


def test_unseen_texts_filters(tmp_path):
    d = _det(tmp_path)
    d.mark_message_seen("客户B", "第一条")
    assert d.unseen_texts("客户B", ["第一条", "第二条"]) == ["第二条"]


def test_mark_messages_seen_marks_each(tmp_path):
    """一批气泡要**逐条**记 —— 只记合并后那一段，下次就滤不掉旧气泡了。"""
    d = _det(tmp_path)
    d.mark_messages_seen("客户B", ["第一条", "第二条"])
    assert d.is_message_seen("客户B", "第一条") is True
    assert d.is_message_seen("客户B", "第二条") is True


def test_unseen_texts_all_seen_returns_empty(tmp_path):
    d = _det(tmp_path)
    d.mark_messages_seen("客户B", ["调戏一下Ai", "有卖什么东西", "1+2*3-1=多少"])
    assert d.unseen_texts("客户B", ["调戏一下Ai", "有卖什么东西", "1+2*3-1=多少"]) == []


def test_short_text_ignored(tmp_path):
    d = _det(tmp_path)
    assert d.is_message_seen("客户B", "在") is False      # 太短不做判断


# ── 重启不忘（这次的直接原因） ────────────────────────────────────────

def test_seen_survives_restart(tmp_path):
    """重启后必须还记得"这条回过了" —— 原来只存内存，重启就重复回复。"""
    d1 = _det(tmp_path)
    d1.mark_messages_seen("客户B", ["调戏一下Ai", "有卖什么东西"])
    d1._maybe_save_seen(force=True)

    d2 = _det(tmp_path)                                   # 模拟重启
    assert d2.is_message_seen("客户B", "有卖什么东西") is True
    assert d2.unseen_texts("客户B", ["调戏一下Ai", "有卖什么东西"]) == []


def test_state_file_written(tmp_path):
    d = _det(tmp_path)
    d.mark_message_seen("客户B", "有卖什么东西")
    d._maybe_save_seen(force=True)
    p = Path(tmp_path) / "seen_messages.json"
    assert p.is_file()
    saved = json.loads(p.read_text(encoding="utf-8"))
    assert saved["seen"]


def test_expired_records_not_loaded(tmp_path):
    """超过有效期的记录不再算数（客户隔天再问同一句要能回答）。"""
    p = Path(tmp_path) / "seen_messages.json"
    p.write_text(json.dumps({"seen": {"old": [time.time() - 90000, "客户B:老的"]}},
                            ensure_ascii=False), encoding="utf-8")
    d = _det(tmp_path)                                    # TTL 24h = 86400
    assert d._seen_messages == {}


def test_short_ttl_expires(tmp_path):
    """过了有效期就不再算"回过了"（客户隔天再问同一句要能回答）。"""
    d = _det(tmp_path, seen_message_ttl_seconds=1, message_dedup_seconds=0)
    d.mark_message_seen("客户B", "有卖什么东西")
    d._seen_messages = {h: (ts - 5, k) for h, (ts, k) in d._seen_messages.items()}
    assert d.is_message_seen("客户B", "有卖什么东西") is False


def test_corrupt_state_file_ignored(tmp_path):
    (Path(tmp_path) / "seen_messages.json").write_text("{坏掉的", encoding="utf-8")
    d = _det(tmp_path)                                    # 不该炸
    assert d.is_message_seen("客户B", "随便") is False


# ── 精确 vs 模糊：窗口不同 ────────────────────────────────────────────

def test_exact_match_uses_long_ttl(tmp_path):
    """同一句话：24 小时内都算"回过了"（这是本次要修的核心）。"""
    d = _det(tmp_path, message_dedup_seconds=300)
    d.mark_message_seen("客户B", "有卖什么东西")
    d._seen_messages = {h: (ts - 3600, k)          # 1 小时前
                        for h, (ts, k) in d._seen_messages.items()}
    assert d.is_message_seen("客户B", "有卖什么东西") is True


def test_fuzzy_match_stays_short_window(tmp_path):
    """"有货吗" 和 "A7M4 有货吗" 这种追问，不能因为 24 小时前的记录被吞掉。"""
    d = _det(tmp_path, message_dedup_seconds=300)
    d.mark_message_seen("客户B", "请问有货吗")
    # 把记录改成 1 小时前：精确匹配不中，模糊匹配也不该中（短窗口已过）
    d._seen_messages = {h: (ts - 3600, k)
                        for h, (ts, k) in d._seen_messages.items()}
    assert d.is_message_seen("客户B", "有货吗") is False


def test_fuzzy_match_within_short_window(tmp_path):
    """模糊匹配只对 4 字以上的文本生效（太短的不可靠）。"""
    d = _det(tmp_path, message_dedup_seconds=300)
    d.mark_message_seen("客户B", "请问有货吗现在")
    assert d.is_message_seen("客户B", "有货吗现在") is True


def test_short_question_not_fuzzy_matched(tmp_path):
    """"有货吗" 只有 3 个字 —— 不做模糊匹配，免得把新问题当成重复。"""
    d = _det(tmp_path, message_dedup_seconds=300)
    d.mark_message_seen("客户B", "请问有货吗")
    assert d.is_message_seen("客户B", "有货吗") is False


def test_fuzzy_does_not_swallow_growth(tmp_path):
    """OCR 累积：先看到 A，后来看到 "B A" —— 那是新消息，不是重复。"""
    d = _det(tmp_path, message_dedup_seconds=300)
    d.mark_message_seen("客户B", "有货吗")
    assert d.is_message_seen("客户B", "老板在吗 有货吗 顺便问下押金") is False


# ── 主程序接线 ────────────────────────────────────────────────────────

def _main_src():
    return (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")


def test_both_paths_filter_per_bubble():
    s = _main_src()
    assert s.count("detector.unseen_texts(") == 2, "红点路径和兜底路径都要逐条过滤"
    assert s.count("detector.mark_messages_seen(") >= 2, "两条路径处理完都要逐条记为已见"
    # 两条扫描路径 + 发送后确认（确认也用它看蓝泡在不在最下面）
    assert s.count("detector.extract_bubbles_detail(") == 3
    assert s.count("_mark_already_replied(") >= 3, "「人工回过也算回过」两条路径都要做"


def test_seen_filter_runs_before_reply():
    s = _main_src()
    i_filter = s.index("fresh = detector.unseen_texts(")
    i_reply = s.index("responder.handle_customer_message", i_filter)
    assert i_filter < i_reply


def test_fallback_returns_bubble_texts():
    """兜底要把气泡文本带回调用方，否则没法逐条记已见。"""
    s = _main_src()
    assert "return True, first[0], first[1], first[2]" in s
    assert "found, fb_text, fb_name, fb_bubbles = asyncio.run(" in s


def test_config_has_ttl():
    cfg = json.loads((Path(__file__).resolve().parent.parent
                      / "config.json").read_text(encoding="utf-8"))
    assert cfg.get("seen_message_ttl_seconds", 0) >= 86400 or \
        cfg.get("seen_message_ttl_seconds", 0) == 86400
    assert cfg.get("unreplied_dedup_seconds", 0) > 0


# ── 两本账：短窗口管「待回复」，长记忆管「已经回过」 ──────────────────
#
# 只用一本账必然二选一：长窗口 → 客户隔一小时又问同一句话被静音（漏答）；
# 短窗口 → 重启后重读到的旧气泡又被回一遍（21:59 那次）。

def test_repeat_question_after_short_window_is_answered(tmp_path):
    """客户隔一阵子又问**同一句话** → 要能正常回答，不能被 24h 记忆静音。"""
    d = _det(tmp_path, unreplied_dedup_seconds=600)
    d.mark_message_seen("客户B", "有货吗现在")
    d._seen_messages = {h: (ts - 1200, k)          # 20 分钟前
                        for h, (ts, k) in d._seen_messages.items()}
    assert d.unseen_texts("客户B", ["有货吗现在"]) == ["有货吗现在"]


def test_main_path_dedup_uses_short_window(tmp_path):
    """主路径那句「消息已处理过(去重)」也要用短窗口（long_term=False）。"""
    d = _det(tmp_path, unreplied_dedup_seconds=600)
    d.mark_message_seen("客户B", "有货吗现在")
    d._seen_messages = {h: (ts - 7200, k)
                        for h, (ts, k) in d._seen_messages.items()}
    assert d.is_message_seen("客户B", "有货吗现在", long_term=False) is False
    assert d.is_message_seen("客户B", "有货吗现在") is True, "长记忆那本账还在"


def test_already_replied_memory_survives_short_window(tmp_path):
    """21:59 事故：重启后旧气泡被读成"未回复"，长记忆必须拦住。"""
    d = _det(tmp_path)
    d.mark_messages_seen("客户B", ["有卖什么东西", "调戏一下Ai"],
                         already_replied=True)
    d._maybe_save_seen(force=True)

    d2 = _det(tmp_path)                                   # 模拟重启
    d2._seen_messages = {h: (ts - 7200, k)                # 短窗口已过期
                         for h, (ts, k) in d2._seen_messages.items()}
    assert d2.unseen_texts("客户B", ["有卖什么东西", "调戏一下Ai"]) == []


def test_replied_memory_persisted(tmp_path):
    d = _det(tmp_path)
    d.mark_messages_seen("客户B", ["押金多少"], already_replied=True)
    d._maybe_save_seen(force=True)
    saved = json.loads((Path(tmp_path) / "seen_messages.json")
                       .read_text(encoding="utf-8"))
    assert saved["replied"], "「已经有人回过」要单独存一份，重启才拦得住"


def test_clear_seen_keeps_replied_memory(tmp_path):
    """每次自动回复后都会调 clear_seen —— 它不能把 24h 的"回过了"一起抹掉。"""
    d = _det(tmp_path)
    d.mark_messages_seen("客户B", ["押金多少"], already_replied=True)
    d._seen_messages = {h: (ts - 200, k)
                        for h, (ts, k) in d._seen_messages.items()}
    d._replied_seen = {h: (ts - 200, k)
                       for h, (ts, k) in d._replied_seen.items()}
    d.clear_seen()
    assert d.is_message_seen("客户B", "押金多少") is True
    assert d.unseen_texts("客户B", ["押金多少"]) == []


def test_main_wiring_short_vs_long():
    """待回复气泡走短窗口；「人工回过也算回过」走长记忆。"""
    s = _main_src()
    assert s.count("long_term=False") >= 3, "两条扫描路径 + 兜底入口都要短窗口"
    assert "already_replied=True" in s, "「已经回过」要记进长记忆那本账"
    i = s.index("def _mark_already_replied")
    assert "is_message_seen(customer_name, t)" in s[i:i + 1500], \
        "_mark_already_replied 内部查重仍用长记忆（默认 long_term=True）"
