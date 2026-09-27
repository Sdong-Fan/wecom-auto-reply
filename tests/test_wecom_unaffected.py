"""企业微信模式回归：微信那边的改动不能碰到它。

分两层守：
  * 参数级 —— 套用 wecom profile 后，config 必须与"没有 profile 时"逐项一致
  * 旁路级 —— 微信专属的开关（白名单/关红点/仅人工）在企微模式下必须都是"未启用"
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def raw_cfg():
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def test_wecom_profile_is_byte_for_byte_noop(raw_cfg):
    """核心不变量：套用企业微信 profile 后，所有影响行为的字段都不能变。"""
    from wxbot.profile import apply_to_config, load_profile
    out = apply_to_config(raw_cfg, load_profile("wecom"))
    paths = [
        ("wecom", "window_class"), ("wecom", "process_names"),
        ("wecom", "min_title_length"),
        ("columns", "col1_width_px"), ("columns", "col2_width_px"),
        ("fallback", "start_y_ratio"), ("fallback", "row_height_px"),
        ("fallback", "max_scan_rows"),
    ]
    for a, b in paths:
        assert out[a][b] == raw_cfg[a][b], f"{a}.{b} 被 profile 改动了"
    assert out["crop"]["chat_area"]["top_margin_px"] == \
        raw_cfg["crop"]["chat_area"]["top_margin_px"]
    assert out["crop"]["chat_area"]["bottom_margin_px"] == \
        raw_cfg["crop"]["chat_area"]["bottom_margin_px"]


def test_wechat_only_switches_are_off_in_wecom(raw_cfg):
    """白名单、关红点、仅人工 —— 这三个是微信侧的，企微模式必须不启用。"""
    from wxbot.profile import apply_to_config, load_profile
    from wxbot.scan_policy import parse_whitelist
    out = apply_to_config(raw_cfg, load_profile("wecom"))
    assert parse_whitelist(out.get("customers", {})) is None, "企微不该有白名单"
    assert out.get("detection", {}).get("red_dot", {}).get("enabled", True) is True
    assert out["rag"]["allow_auto_send"] is True
    assert "start_y_px" not in out["fallback"], "企微继续用比例，别被绝对像素覆盖"


def test_no_customer_match_override_for_wecom(raw_cfg):
    """`customer_match` 只能由微信 profile 设置；企微继续用 @微信 后缀。"""
    from wxbot.profile import apply_to_config, load_profile
    out = apply_to_config(raw_cfg, load_profile("wecom"))
    assert "customer_match" not in out["ocr"]


def test_profile_apply_does_not_mutate_original(raw_cfg):
    from wxbot.profile import apply_to_config, load_profile
    snapshot = json.dumps(raw_cfg, ensure_ascii=False, sort_keys=True)
    apply_to_config(raw_cfg, load_profile("wecom"))
    assert json.dumps(raw_cfg, ensure_ascii=False, sort_keys=True) == snapshot


def test_main_impl_defines_gates_before_use():
    """whitelist / use_red_dot 必须在 _main_impl 里定义，两个后台函数共用。"""
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    assert src.count("whitelist = parse_whitelist(") == 1
    assert src.count("use_red_dot = bool(") == 1
    # 定义位置在 _run_scan / _scan_fallback 之前
    d_wl = src.index("whitelist = parse_whitelist(")
    assert d_wl < src.index("def _run_scan")
    assert d_wl < src.index("def _scan_fallback")
