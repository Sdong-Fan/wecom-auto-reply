"""微信 PC 标定模板：已知的窗口三要素来自 jev-chat-windows 实测，区域/颜色待标定。

模板故意叫 `.json.template` —— `load_profile` 只认 `.json`，所以不存在
"误把没标定的占位 profile 加载进来、用一堆 0 当坐标" 的风险。
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "profiles" / "wechat_pc.json.template"


def test_template_is_not_a_loadable_profile_file():
    """模板必须是 .json.template —— load_profile 只认 .json。

    要守的不变量：**加载出来的 profile 永远不能是模板里那堆占位 0**。
    （微信 PC 现在已经真标定过了，所以这里改成校验"加载到的必须是实测值"。）
    """
    assert TPL.name.endswith(".json.template"), "模板不能被当成可用 profile"
    from wxbot.profile import load_profile
    prof = load_profile("wechat_pc")
    if prof is None:
        return                      # 还没标定的机器：合法，只要不是占位数据就行
    assert prof["layout"]["chat_x"] > 0, "加载到占位 0 说明模板被误当成 profile 了"
    assert prof["bubbles"]["my_color_rgb"], "气泡颜色必须是实测出来的"


def test_template_itself_keeps_placeholder_zeros():
    """模板里保持占位 0 —— 不能拿企业微信的数字冒充微信的。"""
    t = json.loads(TPL.read_text(encoding="utf-8"))
    assert t["layout"]["chat_x"] == 0
    assert not t["bubbles"]["my_color_rgb"]
    assert t.get("unverified") is True


def test_template_window_fields_match_jev_findings():
    """jev docs/KICKOFF.md：目标窗口 Weixin.exe / 类 Qt51514QWindowIcon / 标题「微信」。"""
    t = json.loads(TPL.read_text(encoding="utf-8"))
    w = t["window"]
    assert "Weixin.exe" in w["process_names"]
    assert "WeChat.exe" in w["process_names"], "老版本微信 PC 是 WeChat.exe"
    assert w["window_class"] == "Qt51514QWindowIcon"
    assert w["prefer_title"] == "微信", \
        "同进程还有 'Weixin' 工具窗与 '图片和视频' 看图窗，必须按标题挑"


def test_template_is_marked_unverified_and_has_howto():
    t = json.loads(TPL.read_text(encoding="utf-8"))
    assert t.get("unverified") is True
    assert t["_how_to_use"], "要写清楚怎么用它去标定"
    assert any("calibrate_chat_app" in s for s in t["_how_to_use"])
    assert any("windows-capture" in s or "Graphics" in s for s in t["_how_to_use"]), \
        "要提示 PrintWindow 黑帧时的退路"


def test_template_layout_is_placeholder_not_fake_numbers():
    """区域全是 0 —— 不能拿企业微信的数字冒充微信的，那会静默读错区域。"""
    t = json.loads(TPL.read_text(encoding="utf-8"))
    lay = t["layout"]
    for k in ("chat_x", "chat_w", "chat_top_margin_px", "chat_bottom_margin_px",
              "list_w", "col1_width_px"):
        assert lay[k] == 0, f"{k} 必须是占位 0，等标定"
    assert not t["bubbles"]["my_color_rgb"], "气泡颜色也要等标定器量"


# ── 标定完成后微信 PC 应当变成可选 ────────────────────────────────────

def test_wechat_pc_selectable_once_calibrated():
    """本机已经跑过标定（profiles/wechat_pc.json 存在）→ 面板里必须可选。"""
    from config import settings_store as ss
    ok = {o[0]: o[2] for o in ss.software_options()}
    assert ok["wechat_pc"] is True, "标定完了还是灰的，用户就没法选"


def test_uncalibrated_profile_is_disabled(monkeypatch, tmp_path):
    from config import settings_store as ss
    monkeypatch.setattr(ss, "PROFILES_DIR", tmp_path)   # 一个 profile 都没有
    ok = {o[0]: o[2] for o in ss.software_options()}
    assert ok["wechat_pc"] is False
    assert ok["wecom_screenshot"] is False
    notes = {o[0]: o[3] for o in ss.software_options()}
    assert notes["wechat_pc"], "置灰时要说明为什么"

