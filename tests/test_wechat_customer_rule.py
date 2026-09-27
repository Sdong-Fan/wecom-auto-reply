"""微信 PC 客户判定：微信里没有 "@微信" 后缀，必须换成排除法。

背景：`@微信` 后缀是"微信用户加了企业微信"才显示的（企业微信专属）。
微信 PC 客户端里所有会话都是微信用户，没有这个后缀 —— 沿用旧规则会导致
**一个客户都判不出来**，机器人安静地什么都不干。
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

EXCLUDE = ["文件传输助手", "微信团队", "订阅号", "群聊"]


def _detector(**ocr):
    from wxbot.detector import MessageDetector
    return MessageDetector({"ocr": ocr})


# ── 两种规则 ──────────────────────────────────────────────────────────

def test_suffix_rule_is_default_for_wecom():
    d = _detector()
    assert d.is_customer_name(None, "张三@微信") is True
    assert d.is_customer_name(None, "李四") is False, "企业微信里不带后缀的算同事，不接"


def test_any_rule_accepts_plain_names():
    """微信 PC：普通昵称就是客户。"""
    d = _detector(customer_match="any", non_customer_keywords=EXCLUDE)
    assert d.is_customer_name(None, "张三") is True
    assert d.is_customer_name(None, "老王") is True
    assert d.is_customer_name(None, "客户A") is True


def test_any_rule_still_excludes_system_chats():
    d = _detector(customer_match="any", non_customer_keywords=EXCLUDE)
    assert d.is_customer_name(None, "文件传输助手") is False
    assert d.is_customer_name(None, "微信团队") is False
    assert d.is_customer_name(None, "订阅号消息") is False


def test_any_rule_empty_text_is_not_customer():
    d = _detector(customer_match="any")
    assert d.is_customer_name(None, "") is False


def test_legacy_config_without_customer_match_still_suffix():
    """老 config.json 没有 customer_match 字段，行为必须不变。"""
    d = _detector(customer_name_suffixes=["@微信"])
    assert d.is_customer_name(None, "张三@微信") is True
    assert d.is_customer_name(None, "张三") is False


# ── profile 映射 ──────────────────────────────────────────────────────

def test_wechat_profile_maps_to_any_rule():
    from wxbot.profile import apply_to_config, load_profile
    prof = load_profile("wechat_pc")
    assert prof, "微信 PC profile 应当已经标定出来了"
    out = apply_to_config({}, prof)
    assert out["ocr"]["customer_match"] == "any"
    assert "文件传输助手" in out["ocr"]["non_customer_keywords"]
    assert out["wecom"]["window_class"] == "Qt51514QWindowIcon"
    assert out["wecom"]["min_title_length"] <= 1, "「微信」只有两个字，阈值必须是 1"
    assert out["detection"]["bubble"]["blue"]["my_rgb"], "气泡颜色要来自标定"


def test_wecom_profile_keeps_suffix_rule():
    from wxbot.profile import apply_to_config, load_profile
    out = apply_to_config({}, load_profile("wecom"))
    # 企业微信 profile 没写 customers 段 → 不动 ocr 规则，沿用 config.json 的后缀规则
    assert "customer_match" not in out.get("ocr", {})


def test_wechat_profile_title_preference():
    t = json.loads((ROOT / "profiles" / "wechat_pc.json").read_text(encoding="utf-8"))
    assert t["window"]["prefer_title"] == "微信", \
        "同进程的 'Weixin' 工具窗、'图片和视频' 看图窗面积更大，必须按标题挑"
