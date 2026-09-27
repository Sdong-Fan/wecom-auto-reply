"""两条点击路径都要过白名单 —— 少拦一条等于没拦。

2026-09-25 事故复核时发现的漏洞：白名单只加在兜底扫描里，
**红点路径**（`_run_scan` 的 ══4/5 段）也会点开会话。
微信个人号里红头像会被企业微信的红点规则误判成"有未读"，
于是红点路径成了绕过白名单的后门。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "main.py").read_text(encoding="utf-8")


def test_whitelist_computed_once_in_main_impl():
    """红点路径与兜底路径必须共用同一份白名单。"""
    assert SRC.count("whitelist = parse_whitelist(") == 1, \
        "只该算一次，两处各算一遍迟早会不一致"


def test_red_dot_path_is_gated():
    seg = SRC[SRC.index("4. 预过滤"):SRC.index("6. 聊天区截图")]
    assert "row_eligibility(" in seg, "红点路径也要过白名单"
    assert "不在客户白名单里" in seg


def test_fallback_path_is_gated():
    seg = SRC[SRC.index("def _scan_fallback"):]
    assert "row_eligibility(" in seg


def test_both_gates_precede_their_click():
    for marker in ("4. 预过滤", "def _scan_fallback"):
        seg = SRC[SRC.index(marker):]
        seg = seg[:seg.index("click_col2_row") + 200]
        assert "row_eligibility(" in seg, f"{marker} 的判定必须在点击之前"


def test_red_dot_can_be_disabled_by_profile():
    """微信个人号里红头像会被误判：profile 要能关掉红点检测。"""
    assert 'if use_red_dot else []' in SRC
    assert 'red_dot", {}).get("enabled"' in SRC


def test_wecom_behavior_unchanged():
    """企业微信侧：不启用白名单、红点照旧、允许自动发。"""
    import json
    from wxbot.profile import apply_to_config, load_profile
    import sys
    sys.path.insert(0, str(ROOT))
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    out = apply_to_config(cfg, load_profile("wecom"))
    assert "require_whitelist" not in out.get("customers", {})
    assert out.get("detection", {}).get("red_dot", {}).get("enabled", True) is True
    assert out["rag"]["allow_auto_send"] is True
