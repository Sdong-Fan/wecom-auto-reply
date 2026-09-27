"""设置持久化：.env 写回（不毁文件）+ config.json 原子写 + 软件选择映射。"""
import json
from pathlib import Path

import pytest

from config import settings_store as ss


@pytest.fixture
def env_file(tmp_path):
    p = tmp_path / ".env"
    p.write_text(
        "# === 我的注释，别删 ===\n"
        "DEEPSEEK_API_KEY=sk-old\n"
        "\n"
        "# === 微信客服 ===\n"
        "WECOM_CORP_ID=\n"
        "WECOM_KF_SECRET=\n"
        "OTHER_KEY=保持不变\n",
        encoding="utf-8")
    return p


@pytest.fixture
def cfg_file(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"channel": "screenshot", "profile": "wecom",
                             "rag": {"high_confidence_threshold": 0.65}},
                            ensure_ascii=False, indent=2), encoding="utf-8")
    return p


# ── .env 写回 ─────────────────────────────────────────────────────────

def test_update_env_replaces_in_place_and_preserves_comments(env_file):
    ss.update_env({"DEEPSEEK_API_KEY": "sk-new"}, env_file)
    text = env_file.read_text(encoding="utf-8")
    assert "sk-new" in text and "sk-old" not in text
    assert "# === 我的注释，别删 ===" in text, "注释必须保留"
    assert "OTHER_KEY=保持不变" in text, "别的键不能动"
    assert text.index("DEEPSEEK_API_KEY") < text.index("WECOM_CORP_ID"), "顺序要保住"


def test_update_env_appends_missing_keys(env_file):
    ss.update_env({"LLM_MODEL": "qwen-plus"}, env_file)
    text = env_file.read_text(encoding="utf-8")
    assert "LLM_MODEL=qwen-plus" in text
    assert text.rstrip().endswith("LLM_MODEL=qwen-plus")


def test_update_env_can_clear_a_key(env_file):
    ss.update_env({"WECOM_KF_SECRET": ""}, env_file)
    assert ss.read_env(env_file)["WECOM_KF_SECRET"] == ""


def test_update_env_creates_file_when_missing(tmp_path):
    p = tmp_path / ".env"
    ss.update_env({"LLM_API_KEY": "sk-a"}, p)
    assert ss.read_env(p)["LLM_API_KEY"] == "sk-a"


def test_update_env_handles_spaces_around_equals(env_file):
    env_file.write_text("DEEPSEEK_API_KEY = sk-old\n", encoding="utf-8")
    ss.update_env({"DEEPSEEK_API_KEY": "sk-new"}, env_file)
    assert ss.read_env(env_file)["DEEPSEEK_API_KEY"] == "sk-new"


def test_update_env_logs_masked_only(env_file, caplog):
    import logging
    with caplog.at_level(logging.INFO):
        ss.update_env({"LLM_API_KEY": "sk-super-secret-value"}, env_file)
    logged = caplog.text
    assert "sk-super-secret-value" not in logged, "密钥明文绝不能进日志"
    assert "LLM_API_KEY" in logged


def test_read_env_strips_quotes(tmp_path):
    p = tmp_path / ".env"
    p.write_text('A="quoted value"\nB=\'single\'\n', encoding="utf-8")
    env = ss.read_env(p)
    assert env["A"] == "quoted value" and env["B"] == "single"


# ── config.json ───────────────────────────────────────────────────────

def test_save_config_is_atomic_and_backs_up(cfg_file):
    cfg = ss.load_config(cfg_file)
    cfg["channel"] = "wecom_api"
    ss.save_config(cfg, cfg_file)
    assert ss.load_config(cfg_file)["channel"] == "wecom_api"
    assert (cfg_file.parent / "config.json.bak").exists(), "改前要留备份"
    assert not (cfg_file.parent / "config.json.tmp").exists(), "临时文件不该留下"


def test_save_config_preserves_other_sections(cfg_file):
    cfg = ss.load_config(cfg_file)
    cfg["channel"] = "wecom_api"
    ss.save_config(cfg, cfg_file)
    assert ss.load_config(cfg_file)["rag"]["high_confidence_threshold"] == 0.65


# ── 软件选择映射 ──────────────────────────────────────────────────────

def test_software_choices_cover_three_options():
    ids = [c[0] for c in ss.SOFTWARE_CHOICES]
    assert ids == ["wecom_screenshot", "wecom_api", "wechat_pc"]


def test_availability_is_derived_from_profile_file(monkeypatch, tmp_path):
    """可选性不写死在表里：profiles/<id>.json 在就能选，不在就置灰。

    刚标定完微信 PC，面板里它应当自动从灰变亮 —— 这条就是守着那个行为。
    """
    monkeypatch.setattr(ss, "PROFILES_DIR", tmp_path)
    assert ss.profile_available("wechat_pc") is False
    assert ss.profile_available("wecom") is False

    (tmp_path / "wechat_pc.json").write_text("{}", encoding="utf-8")
    assert ss.profile_available("wechat_pc") is True
    assert ss.profile_available("wecom") is False

    opts = {o[0]: o for o in ss.software_options()}
    assert opts["wechat_pc"][2] is True
    assert opts["wecom_screenshot"][2] is False      # wecom.json 还没建
    assert opts["wecom_screenshot"][3], "不可选时要给出原因说明"


def test_empty_profile_id_needs_no_calibration():
    assert ss.profile_available("") is True


def test_software_from_config_roundtrip():
    for sid, _label, ch, prof, _ok in ss.SOFTWARE_CHOICES:
        cfg = {"channel": ch, "profile": prof}
        assert ss.software_from_config(cfg) == sid


def test_software_from_config_unknown():
    assert ss.software_from_config({"channel": "x", "profile": "y"}) == "custom"


def test_apply_software_writes_channel_and_profile():
    cfg = {}
    ss.apply_software(cfg, "wecom_api")
    assert cfg["channel"] == "wecom_api" and cfg["profile"] == "wecom"
    ss.apply_software(cfg, "wechat_pc")
    assert cfg["channel"] == "screenshot" and cfg["profile"] == "wechat_pc"


# ── save_settings 总入口 ──────────────────────────────────────────────

def test_save_settings_writes_both_files_and_reports_switch(env_file, cfg_file):
    out = ss.save_settings({
        "llm_api_key": "sk-new", "llm_base_url": "https://api.deepseek.com/v1",
        "llm_model": "deepseek-chat",
        "wecom_corp_id": "ww123", "wecom_kf_secret": "sec", "wecom_open_kfid": "wk1",
        "software": "wecom_api",
    }, env_path=env_file, config_path=cfg_file)

    env = ss.read_env(env_file)
    assert env["LLM_API_KEY"] == "sk-new"
    assert env["WECOM_KF_SECRET"] == "sec"
    assert env["DEEPSEEK_API_KEY"] == "", "旧键要清掉，免得两套并存"
    assert ss.load_config(cfg_file)["channel"] == "wecom_api"
    assert out["software_changed"] is True
    assert out["after"] == "wecom_api"


def test_save_settings_reports_no_switch_when_same(env_file, cfg_file):
    out = ss.save_settings({"software": "wecom_screenshot",
                            "llm_api_key": "sk-a"},
                           env_path=env_file, config_path=cfg_file)
    assert out["software_changed"] is False


def test_read_current_settings_prefers_llm_over_legacy(env_file, cfg_file):
    """.env 里同时有新旧两套键时，面板要显示新的一套。"""
    ss.update_env({"LLM_API_KEY": "sk-llm", "LLM_MODEL": "qwen-plus"}, env_file)
    cur = ss.read_current_settings(env_path=env_file, config_path=cfg_file)
    assert cur["llm_api_key"] == "sk-llm"
    assert cur["llm_model"] == "qwen-plus"
    assert cur["software"] == "wecom_screenshot"


def test_read_current_settings_falls_back_to_legacy(env_file, cfg_file):
    cur = ss.read_current_settings(env_path=env_file, config_path=cfg_file)
    assert cur["llm_api_key"] == "sk-old", "只有 DEEPSEEK_* 时也要能读出来"


def test_read_current_settings_reflects_saved_software(env_file, cfg_file):
    ss.save_settings({"software": "wecom_api", "llm_api_key": "sk-a"},
                     env_path=env_file, config_path=cfg_file)
    cur = ss.read_current_settings(env_path=env_file, config_path=cfg_file)
    assert cur["software"] == "wecom_api"
