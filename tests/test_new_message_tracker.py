"""「只回复点开始之后收到的新消息」+ 多人同时发消息的处理。

用户提的规则：微信收到消息会把会话顶到最上面，所以只要管**点开始之后才有变化**的会话。
比白名单实用得多：不用手工维护名单，也不会去翻以前的聊天记录。
"""
from PIL import Image

import pytest

from wxbot.new_message_tracker import (NewMessageTracker, resolve_only_new_messages,
                                       row_fp, row_key)


def _img(seed: int, size=(200, 72)) -> Image.Image:
    """造一张内容确定的图（同 seed → 同样的像素）。"""
    return Image.new("RGB", size, (seed % 256, (seed * 7) % 256, (seed * 13) % 256))


# ── 核心语义 ──────────────────────────────────────────────────────────

def test_first_sighting_is_not_new():
    """没建基线之前一律不算新消息 —— 免得一按开始就去翻旧聊天。"""
    t = NewMessageTracker()
    assert t.is_new("k1", "fp1") is False


def test_prime_then_unchanged_is_not_new():
    t = NewMessageTracker()
    t.prime([("k1", "fp1"), ("k2", "fp2")])
    assert t.primed is True
    assert t.is_new("k1", "fp1") is False, "内容没变，不算新消息"
    assert t.is_new("k2", "fp2") is False


def test_changed_content_is_new():
    t = NewMessageTracker()
    t.prime([("k1", "fp1")])
    assert t.is_new("k1", "fp1_new") is True, "有变化＝收到新消息"


def test_brand_new_conversation_is_new():
    """有人第一次给你发消息 → 会话是全新的，也算新消息。"""
    t = NewMessageTracker()
    t.prime([("k1", "fp1")])
    assert t.is_new("k9", "fpX") is True


def test_accept_advances_baseline_so_our_reply_does_not_retrigger():
    """关键：我们发出去的回复会改列表预览，不推进基线就会反复点开同一个会话。"""
    t = NewMessageTracker()
    t.prime([("k1", "fp_before")])
    assert t.is_new("k1", "fp_after_reply") is True     # 我们的回复改了预览
    t.accept("k1", "fp_after_reply")                    # 处理完推进基线
    assert t.is_new("k1", "fp_after_reply") is False    # 不再当成新消息
    assert t.is_new("k1", "fp_they_sent_again") is True  # 对方再发 → 又是新的


def test_disabled_tracker_never_reports_new():
    t = NewMessageTracker(enabled=False)
    t.prime([("k1", "fp1")])
    assert t.is_new("k1", "changed") is False


def test_reset_clears_baseline():
    t = NewMessageTracker()
    t.prime([("k1", "fp1")])
    t.reset()
    assert t.primed is False and t.size == 0
    assert t.is_new("k1", "fp1") is False


# ── 指纹：key 不能随消息变化，fp 要变 ──────────────────────────────────

def test_row_key_uses_name_area_only():
    """key 用名字区：新消息只改预览，不改名字，所以 key 要稳定。"""
    name = _img(1, (200, 32))
    assert row_key(name) == row_key(_img(1, (200, 32)))
    assert row_key(name) != row_key(_img(2, (200, 32)))


def test_row_fp_changes_with_preview():
    row_a = _img(1, (200, 72))
    row_b = _img(1, (200, 72))
    assert row_fp(row_a) == row_fp(row_b)
    assert row_fp(row_a) != row_fp(_img(3, (200, 72)))


# ── 多人同时发消息 ────────────────────────────────────────────────────

def test_multiple_senders_are_all_flagged_and_survive_until_handled():
    """三个人同时发：三个会话都要被标成"有新消息"，没处理完的下轮还在。"""
    t = NewMessageTracker()
    t.prime([("kA", "a0"), ("kB", "b0"), ("kC", "c0"), ("kD", "d0")])
    # A、B、C 同时来了新消息
    changed = [k for k in ("kA", "kB", "kC", "kD")
               if t.is_new(k, {"kA": "a1", "kB": "b1", "kC": "c1", "kD": "d0"}[k])]
    assert changed == ["kA", "kB", "kC"], "D 没变化，不该被处理"

    # 本轮只处理 A（一次只回一个人是刻意的：只有一个输入框）
    t.accept("kA", "a1")
    rest = [k for k in changed if t.is_new(k, {"kA": "a1", "kB": "b1", "kC": "c1"}[k])]
    assert rest == ["kB", "kC"], "没轮到的 B、C 必须留在下轮，不能丢"


def test_reordered_rows_do_not_mix_up_conversations():
    """新消息会把会话顶到最上面 —— 用行号当 key 就会串到别人身上。"""
    t = NewMessageTracker()
    # 基线：kA 在第0行、kB 在第1行（key 是名字指纹，跟行号无关）
    t.prime([("kA", "a0"), ("kB", "b0")])
    # B 来了新消息被顶到最上面：行序变了，但 key 不变
    assert t.is_new("kB", "b1") is True
    assert t.is_new("kA", "a0") is False, "A 只是被挤下去了，不是有新消息"


# ── 开关解析 ──────────────────────────────────────────────────────────

def test_resolve_flag_defaults_off():
    assert resolve_only_new_messages({}) is False
    assert resolve_only_new_messages(None) is False


def test_resolve_flag_reads_config():
    assert resolve_only_new_messages({"scan": {"only_new_messages": True}}) is True


# ── profile 接线 ──────────────────────────────────────────────────────

def test_wechat_profile_enables_only_new_messages():
    from wxbot.profile import apply_to_config, load_profile
    out = apply_to_config({}, load_profile("wechat_pc"))
    assert out["scan"]["only_new_messages"] is True
    assert out["customers"]["require_whitelist"] is False, "白名单退回可选"


def test_wecom_profile_does_not_enable_it():
    from wxbot.profile import apply_to_config, load_profile
    out = apply_to_config({"scan": {"only_new_messages": False}}, load_profile("wecom"))
    assert out["scan"]["only_new_messages"] is False, "企业微信不受影响"


# ── main.py 接线 ──────────────────────────────────────────────────────

def test_main_primes_baseline_before_processing():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")
    assert "new_tracker.prime(" in src
    # 建基线那一轮必须直接返回，不处理任何会话
    i = src.index("new_tracker.prime(")
    seg = src[i:i + 400]
    assert 'return False, "", None' in seg, "建基线那一轮不能顺手回消息"
    # 未建基线时不处理
    assert "only_new_messages and not new_tracker.primed" in src


def test_main_settles_baseline_on_every_exit_path():
    """每条"这一行处理完了"的出口都要推进基线，否则会反复点开。"""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")
    seg = src[src.index("def _scan_fallback"):src.index("if first is not None:")]
    assert seg.count("_settle(") >= 5, "无气泡/OCR空/非客户/已处理过/取中 都要 settle"
