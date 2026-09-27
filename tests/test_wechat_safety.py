"""B+C 的安全改动测试：行几何进 profile、白名单默认不点开、微信侧只进待人工。

背景（真实事故）：切到微信 PC 模式后，机器人
  * 把个人微信里的朋友/群/营销号全判成客户（`customer_match=any` 排除法）
  * 用企业微信的行高（94）去扫微信的列表（真实 98）→ 每行偏 4px、第 5 行偏 29px
    → 用 A 的名字配上 B 的内容
  * 一轮点开了 6 个会话（点开＝清掉对方未读，不可逆），12 次
  * 还自动发了一条占位语到一个群聊里
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


# ── 行几何必须走绝对像素 ──────────────────────────────────────────────

def test_profile_maps_absolute_row_geometry():
    from wxbot.profile import apply_to_config, load_profile
    out = apply_to_config({}, load_profile("wechat_pc"))
    assert out["fallback"]["start_y_px"] == 139
    assert out["fallback"]["row_height_px"] == 98
    assert out["crop"]["name_row"]["x_from_ratio"] == 0.20


def test_wecom_profile_does_not_override_row_geometry():
    """企业微信没标定行几何时，不能把 config.json 原有值冲掉。"""
    from wxbot.profile import apply_to_config, load_profile
    out = apply_to_config({"fallback": {"start_y_ratio": 0.0747, "row_height_px": 94}},
                          load_profile("wecom"))
    assert "start_y_px" not in out["fallback"]
    assert out["fallback"]["row_height_px"] == 94
    assert out["fallback"]["start_y_ratio"] == 0.0747


def test_scan_uses_absolute_start_y_when_present():
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    assert 'fallback_cfg.get("start_y_px")' in src
    idx = src.index('fallback_cfg.get("start_y_px")')
    seg = src[idx:idx + 400]
    assert "start_y_ratio" in seg, "绝对值优先、比例兜底"


# ── 白名单：默认谁都不点 ──────────────────────────────────────────────

def test_wechat_profile_only_new_messages_and_optional_whitelist():
    """安全防线：只处理点开始之后收到的新消息；白名单退回可选。"""
    t = json.loads((ROOT / "profiles/wechat_pc.json").read_text(encoding="utf-8"))
    assert t["scan"]["only_new_messages"] is True
    assert t["customers"]["require_whitelist"] is False
    assert t["customers"]["whitelist"] == []


def test_profile_maps_only_new_messages():
    from wxbot.profile import apply_to_config, load_profile
    from wxbot.new_message_tracker import resolve_only_new_messages
    out = apply_to_config({}, load_profile("wechat_pc"))
    assert resolve_only_new_messages(out) is True
    assert out["customers"]["require_whitelist"] is False


def test_whitelist_gate_exists_before_clicking():
    """白名单判断必须在 `click_col2_row` **之前** —— 否则已经点开了才发现不该点。"""
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    idx = src.index("不在客户白名单里")
    click = src.index("scanner.click_col2_row(y)", idx - 3000)
    assert idx < click, "白名单判定要放在点击之前"


def test_wecom_mode_has_no_whitelist_gate():
    """企业微信用 @微信 后缀规则，不该被白名单影响。"""
    from wxbot.profile import apply_to_config, load_profile
    out = apply_to_config({}, load_profile("wecom"))
    assert "require_whitelist" not in out.get("customers", {})


# ── 只点一个：找到就停 ────────────────────────────────────────────────

def test_stops_clicking_after_first_hit():
    """取到一个待回复客户后立即 break —— 不能把用户所有会话都点一遍。"""
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    i = src.index(">>> 兜底客户! y=")
    seg = src[i:i + 900]
    assert "break" in seg, "找到第一个待回复客户后必须停止点击后续行"
    assert "另有一个待回复客户" not in src, "旧行为（继续点完所有行）必须删掉"


# ── 仅人工模式（决定 C）───────────────────────────────────────────────

def test_wechat_profile_disables_auto_send():
    t = json.loads((ROOT / "profiles/wechat_pc.json").read_text(encoding="utf-8"))
    assert t["dispatch"]["allow_auto_send"] is False


def test_profile_maps_allow_auto_send():
    from wxbot.profile import apply_to_config, load_profile
    assert apply_to_config({}, load_profile("wechat_pc"))["rag"]["allow_auto_send"] is False
    assert "allow_auto_send" not in apply_to_config({}, load_profile("wecom")).get("rag", {}) or \
        apply_to_config({}, load_profile("wecom"))["rag"]["allow_auto_send"] is True


def test_responder_defaults_to_allowing_auto_send():
    """不传 config 时行为不变（企业微信那套照旧）。"""
    src = (ROOT / "rag/responder.py").read_text(encoding="utf-8")
    assert "self._allow_auto_send = True" in src
    assert "self._allow_auto_send and not llm_requests_human" in src, \
        "LLM 说'答不了'时绝不能直发"


def test_only_human_mode_sends_nothing_not_even_hold():
    """关掉自动发送时，连占位语都不能发 —— 只推进待人工。"""
    src = (ROOT / "rag/responder.py").read_text(encoding="utf-8")
    i = src.index("if not self._allow_auto_send:")
    seg = src[i:i + 900]
    assert 'hold_text=""' in seg, "仅人工模式下不能发占位语"
    assert "escalated=True" in seg
    assert "draft_reply=draft" in seg, "草稿还是要给人工看（但内部信号不能当草稿）"


def test_human_marker_is_never_a_draft():
    """「需要人工处理」是给程序看的信号，不能当草稿递给人工去发。

    实测踩过：老板在待人工页点了「发送」，客户真的收到了这六个字。
    """
    src = (ROOT / "rag/responder.py").read_text(encoding="utf-8")
    assert 'draft = "" if llm_requests_human else reply' in src
    main_src = (ROOT / "main.py").read_text(encoding="utf-8")
    block = main_src[main_src.index("def _handle_pending_send"):
                     main_src.index("def _handle_pending_edit")]
    assert "is_human_marker(draft)" in block, "待人工页发送前要拦一次"
    assert "没给草稿" in block


def test_human_marker_detector():
    from rag.responder import is_human_marker
    assert is_human_marker("需要人工处理") is True
    assert is_human_marker("需要人工处理。") is True
    assert is_human_marker("好的，需要人工处理一下") is True
    assert is_human_marker("需要帮您确认价格") is True
    assert is_human_marker("A7M4 日租 90 元") is False
    assert is_human_marker("") is False
    assert is_human_marker("在的在的，你说") is False


# ── OCR 退化裁剪防护 ──────────────────────────────────────────────────

def test_ocr_engine_guards_zero_size_image():
    """行偏移会裁出 0 尺寸的图，RapidOCR 直接抛 ResizeImgError，整轮扫描异常。"""
    from PIL import Image
    from wxbot.detector import ocr_engine
    for size in ((0, 0), (0, 50), (100, 0)):
        assert ocr_engine.extract_text(Image.new("RGB", size)) == ""


def test_ocr_engine_never_produces_zero_width_on_resize():
    """resize 时宽也要 max(1, ...)，否则极端窄图会算出 0 宽。"""
    src = (ROOT / "wxbot/detector/ocr_engine.py").read_text(encoding="utf-8")
    assert "if w <= 0 or h <= 0" in src
    assert "max(1, int(w * target_h / h))" in src
