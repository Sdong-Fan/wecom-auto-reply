"""逐行取舍策略：什么时候允许**点开**一个会话。

2026-09-25 事故的直接防线：切到微信 PC 后机器人一轮点了 6 个会话
（点开＝清掉对方未读，不可逆），并往一个群聊自动发了占位语。
"""
import pytest

from wxbot.scan_policy import (OK, SKIP_COOLDOWN, SKIP_NOT_CUSTOMER,
                               SKIP_NOT_IN_WHITELIST, parse_whitelist,
                               row_eligibility)


# ── 基本分支 ──────────────────────────────────────────────────────────

def test_non_customer_is_never_opened():
    ok, why = row_eligibility(False, "文件传输助手")
    assert ok is False and why == SKIP_NOT_CUSTOMER


def test_customer_without_whitelist_is_opened():
    ok, why = row_eligibility(True, "张三@微信")
    assert ok is True and why == OK


def test_cooling_customer_is_skipped():
    ok, why = row_eligibility(True, "张三", in_cooldown=True)
    assert ok is False and why == SKIP_COOLDOWN


# ── 白名单（微信个人号的关键防线）─────────────────────────────────────

def test_empty_whitelist_opens_nothing():
    """空名单 = 谁都不点 —— 这是微信 PC 模式的出厂默认，必须绝对可靠。"""
    for name in ("张三", "客户D", "客户A @某集团", "客户F-年... @悟空", "大"):
        ok, why = row_eligibility(True, name, whitelist=set())
        assert ok is False, f"{name} 不该被点开"
        assert why == SKIP_NOT_IN_WHITELIST


def test_whitelist_only_allows_named_conversations():
    wl = {"张三", "李四"}
    assert row_eligibility(True, "张三", whitelist=wl)[0] is True
    assert row_eligibility(True, "王五", whitelist=wl)[1] == SKIP_NOT_IN_WHITELIST


def test_none_whitelist_means_disabled():
    assert row_eligibility(True, "任何人", whitelist=None)[0] is True


def test_whitelist_check_happens_before_click():
    """顺序：白名单比冷却更"硬"，但两者都在点击之前。"""
    ok, why = row_eligibility(True, "王五", whitelist={"张三"}, in_cooldown=True)
    assert ok is False
    assert why == SKIP_COOLDOWN, "冷却先判（更省事），但结论都是不点"


# ── parse_whitelist ───────────────────────────────────────────────────

def test_parse_whitelist_disabled_by_default():
    assert parse_whitelist({}) is None
    assert parse_whitelist(None) is None
    assert parse_whitelist({"whitelist": ["张三"]}) is None, "没启用就是 None，不参与判断"


def test_parse_whitelist_enabled_gives_set():
    got = parse_whitelist({"require_whitelist": True, "whitelist": [" 张三 ", "李四", "", None]})
    assert got == {"张三", "李四"}, "要去空白、丢空项"


def test_parse_whitelist_enabled_but_empty_is_empty_set():
    got = parse_whitelist({"require_whitelist": True, "whitelist": []})
    assert got == set(), "空集合 ≠ None：前者是'谁都不点'"


# ── 与真实 profile 的一致性 ───────────────────────────────────────────

def test_wechat_profile_only_touches_new_messages():
    """微信侧的防线从"白名单"换成了"只处理新消息"（更好用）：
    首次扫描建基线，之后只有内容变化的会话才会被点开。"""
    import json
    from pathlib import Path
    from wxbot.profile import apply_to_config, load_profile
    from wxbot.new_message_tracker import resolve_only_new_messages
    root = Path(__file__).resolve().parent.parent
    cfg = json.loads((root / "config.json").read_text(encoding="utf-8"))
    out = apply_to_config(cfg, load_profile("wechat_pc"))
    assert resolve_only_new_messages(out) is True
    assert parse_whitelist(out.get("customers", {})) is None, "白名单退回可选、不启用"


def test_wecom_profile_has_no_whitelist_gate():
    from wxbot.profile import apply_to_config, load_profile
    assert parse_whitelist(apply_to_config({}, load_profile("wecom")).get("customers", {})) is None


# ── main.py 接线 ──────────────────────────────────────────────────────

def test_main_uses_the_shared_policy():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")
    assert "from wxbot.scan_policy import parse_whitelist, row_eligibility" in src
    assert "ok_to_open, why = row_eligibility(" in src
    # 白名单判定必须在点击之前
    assert src.index("ok_to_open, why = row_eligibility(") < \
        src.index("scanner.click_col2_row(y)", src.index("ok_to_open, why = row_eligibility(") - 2000)
