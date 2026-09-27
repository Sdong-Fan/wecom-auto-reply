"""截图模式的软件 profile：加载、映射到 config、气泡颜色规则。

核心不变量：**套用现在这份 wecom profile 之后，config 必须和改造前一样** ——
不能让"支持多软件"这件事把正在跑的企业微信改坏。
"""
import json
from pathlib import Path

import pytest

from wxbot.profile import apply_to_config, list_profiles, load_profile


# ── 加载 ──────────────────────────────────────────────────────────────

def test_list_profiles_contains_builtin_wecom():
    ids = [pid for pid, _ in list_profiles()]
    assert "wecom" in ids


def test_load_missing_profile_returns_none():
    assert load_profile("不存在的东西") is None
    assert load_profile("") is None


def test_load_wecom_profile_has_required_sections():
    p = load_profile("wecom")
    assert p["window"]["process_names"]
    assert p["window"]["window_class"]
    assert p["layout"]["chat_x"] > 0
    assert p["bubbles"]["my_color_rgb"]


# ── 映射到 config ─────────────────────────────────────────────────────

@pytest.fixture
def base_cfg():
    return json.loads(Path("config.json").read_text(encoding="utf-8"))


def test_applying_wecom_profile_reproduces_current_geometry(base_cfg):
    """关键回归：套用 profile 后，聊天区起点/宽度/上下边距与现网一致。"""
    p = load_profile("wecom")
    out = apply_to_config(base_cfg, p)

    def chat_left(c):
        return int(c["columns"]["col1_width_px"]) + int(c["columns"]["col2_width_px"])

    before = chat_left(base_cfg)
    after = chat_left(out)
    assert abs(after - p["layout"]["chat_x"]) <= 3, "聊天区左边界要落在标定值附近"
    assert abs(after - before) <= 3, f"套用 profile 后聊天区起点变了: {before} → {after}"

    assert out["crop"]["chat_area"]["top_margin_px"] == \
        base_cfg["crop"]["chat_area"]["top_margin_px"]
    assert out["crop"]["chat_area"]["bottom_margin_px"] == \
        base_cfg["crop"]["chat_area"]["bottom_margin_px"]


def test_applying_profile_sets_window_match(base_cfg):
    p = load_profile("wecom")
    out = apply_to_config(base_cfg, p)
    assert out["wecom"]["window_class"] == p["window"]["window_class"]
    assert out["wecom"]["process_names"] == p["window"]["process_names"]


def test_applying_profile_sets_bubble_color(base_cfg):
    p = load_profile("wecom")
    out = apply_to_config(base_cfg, p)
    blue = out["detection"]["bubble"]["blue"]
    assert blue["my_rgb"] == p["bubbles"]["my_color_rgb"]
    assert blue["my_tolerance"] <= 30, "容差必须小，否则对方灰气泡会被当成我方"


def test_apply_does_not_mutate_input(base_cfg):
    snapshot = json.dumps(base_cfg, ensure_ascii=False, sort_keys=True)
    apply_to_config(base_cfg, load_profile("wecom"))
    assert json.dumps(base_cfg, ensure_ascii=False, sort_keys=True) == snapshot


def test_apply_with_none_profile_is_noop(base_cfg):
    assert apply_to_config(base_cfg, None) is base_cfg


def test_other_config_keys_untouched(base_cfg):
    out = apply_to_config(base_cfg, load_profile("wecom"))
    assert out["rag"] == base_cfg["rag"]
    assert out["ocr"] == base_cfg["ocr"]
    assert out["fallback"] == base_cfg["fallback"]


# ── 气泡颜色规则 ──────────────────────────────────────────────────────

def test_my_rgb_matches_wecom_blue_and_rejects_customer_gray():
    """企业微信实测：我方 (201,231,255)，对方 (228,231,235)。"""
    from wxbot.detector.bubble import is_blue_pixel
    cfg = {"my_rgb": [201, 231, 255], "my_tolerance": 20}
    assert is_blue_pixel(201, 231, 255, cfg) is True
    assert is_blue_pixel(228, 231, 235, cfg) is False, "对方灰气泡不能算我方"


def test_my_rgb_supports_wechat_green():
    """微信 PC 是绿气泡：换成绿色 profile 也能判对。"""
    from wxbot.detector.bubble import is_blue_pixel
    cfg = {"my_rgb": [149, 236, 105], "my_tolerance": 20}
    assert is_blue_pixel(149, 236, 105, cfg) is True
    assert is_blue_pixel(255, 255, 255, cfg) is False
    assert is_blue_pixel(228, 231, 235, cfg) is False


def test_without_my_rgb_falls_back_to_legacy_blue_rule():
    """没标定色的软件（或没套 profile）保持原有蓝气泡判据不变。"""
    from wxbot.detector.bubble import is_blue_pixel
    assert is_blue_pixel(201, 231, 255) is True     # 蓝色
    assert is_blue_pixel(149, 236, 105) is False    # 绿色：旧规则不认
    assert is_blue_pixel(228, 231, 235) is False


# ── main.py 接线 ──────────────────────────────────────────────────────

def test_main_applies_profile_before_scanner():
    src = Path("main.py").read_text(encoding="utf-8")
    assert 'cfg.get("profile"' in src
    assert "apply_to_config" in src
    # 必须在构造 Scanner 之前套用，否则 geometry 不会被读到
    assert src.index("apply_to_config") < src.index("scanner = Scanner(cfg)")
