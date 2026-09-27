"""设置面板的结构与安全性（不实际弹 Tk 窗口，只测可测的部分）。"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "gui/settings_dialog.py").read_text(encoding="utf-8")


# ── 面板内容 ──────────────────────────────────────────────────────────

def test_has_llm_section_with_preset_url_model_key():
    for field in ("llm_base_url", "llm_model", "llm_api_key"):
        assert field in SRC, f"LLM 区必须有 {field}"
    assert "PRESETS" in SRC and "Combobox" in SRC, "要有预设下拉"
    assert 'text="测试连接"' in SRC


def test_api_key_entry_is_masked():
    assert '("llm_api_key", "密钥", "•")' in SRC, "密钥输入框必须打码"
    assert '("wecom_kf_secret", "客服 Secret", "•")' in SRC


def test_has_three_software_options_from_store():
    assert "software_options()" in SRC, "软件列表要来自 settings_store，别各写一份"
    import config.settings_store as ss
    assert len(ss.SOFTWARE_CHOICES) == 3
    assert {o[0] for o in ss.software_options()} == {
        "wecom_screenshot", "wecom_api", "wechat_pc"}


def test_availability_is_dynamic_not_hardcoded():
    """可选性按 profiles/<id>.json 是否存在决定 —— 标定完微信 PC 自动变可选。"""
    assert "software_options()" in SRC
    assert "profile_available" in SRC or "ok" in SRC


def test_unavailable_software_is_disabled_not_hidden():
    """微信 PC 还没标定：要看得见但选不了（用户才知道将来支持）。"""
    assert 'state="normal" if ok else "disabled"' in SRC


def test_api_credentials_only_shown_in_api_mode():
    assert "def _on_software" in SRC
    assert "self._api_frame.pack_forget()" in SRC
    assert "self._api_frame.pack(" in SRC


def test_has_wecom_connection_test():
    assert 'text="测试企业微信连接"' in SRC
    assert "selftest" in SRC


# ── 安全 / 体验 ───────────────────────────────────────────────────────

def test_network_calls_run_off_main_thread():
    """连通测试要放后台线程，否则 Tk 界面会僵住。"""
    assert "threading.Thread(target=work, daemon=True).start()" in SRC
    assert "def _run_async" in SRC


def test_save_writes_through_settings_store():
    assert "store.save_settings" in SRC
    assert "messagebox.showerror" in SRC, "保存失败要有提示"


def test_llm_takes_effect_without_restart():
    """LLM 三项保存后立即生效：重载 .env + 重建 client。"""
    assert "load_dotenv(store.ENV_PATH, override=True)" in SRC
    assert "llm_client.reset_client()" in SRC


def test_software_switch_tells_user_to_restart():
    assert "需要重启" in SRC
    assert "software_changed" in SRC
    assert 'text="立即重启"' in SRC


def test_restart_button_disabled_until_needed():
    assert 'state="disabled"' in SRC


def test_restart_uses_real_launcher_and_waits_for_lock_release():
    """重启要让本进程先退出把 Qdrant 文件锁放掉，所以延迟几秒再拉起。"""
    assert "ping -n 4" in SRC, "要留时间给旧进程释放 data/qdrant 的文件锁"
    assert "启动.bat" in SRC


def test_no_hardcoded_secret_in_source():
    import re
    assert not re.search(r"sk-[A-Za-z0-9]{16,}", SRC), "源码里不能出现真实密钥"


# ── 与 main.py 的接线 ─────────────────────────────────────────────────

def test_main_opens_the_real_dialog():
    m = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "from gui.settings_dialog import open_settings" in m
    assert "open_settings(window.root" in m
    # 打开失败不能静默
    assert "设置面板打开失败" in m


def test_dialog_import_is_lazy():
    """面板 import 放在函数里：免得 Tk/网络依赖拖慢主程序启动。"""
    m = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "from gui.settings_dialog import open_settings" in m
    idx = m.index("def _open_settings")
    assert idx < m.index("from gui.settings_dialog import open_settings")


# ── 真建一次窗口（源文本测试抓不到 Tk 控件参数写错）────────────────────

def test_dialog_actually_builds(tmp_path):
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except Exception as e:                      # 无显示环境就跳过
        pytest.skip(f"没有可用的显示环境: {e}")
    root.withdraw()
    try:
        from config import settings_store as ss
        from gui.settings_dialog import SettingsDialog
        env = tmp_path / ".env"
        cfgf = tmp_path / "config.json"
        env.write_text("LLM_API_KEY=sk-test\n", encoding="utf-8")
        cfgf.write_text('{"channel": "screenshot", "profile": "wecom"}', encoding="utf-8")

        with patch.object(ss, "ENV_PATH", env), patch.object(ss, "CONFIG_PATH", cfgf):
            dlg = SettingsDialog(root, {"channel": "screenshot", "profile": "wecom"},
                                 on_saved=MagicMock())
        root.update()                            # 真正走一遍布局
        assert dlg._software.get() == "wecom_screenshot"
        assert dlg._get("llm_api_key") == "sk-test"
        # 非 API 模式下凭据区应当收起
        # （用 winfo_manager 判断：测试里窗口是 withdraw 的，ismapped 恒为 0）
        assert dlg._api_frame.winfo_manager() == ""
        # 切到 API 模式后应当显示
        dlg._software.set("wecom_api")
        dlg._on_software()
        root.update()
        assert dlg._api_frame.winfo_manager() == "pack"
        dlg.win.destroy()
    finally:
        root.destroy()


def test_llm_section_switch_to_api_mode_shows_credentials(tmp_path):
    """选 API 模式时凭据区必须出现（用户看不到就没法填）。"""
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except Exception as e:
        pytest.skip(f"没有可用的显示环境: {e}")
    root.withdraw()
    try:
        from config import settings_store as ss
        from gui.settings_dialog import SettingsDialog
        env = tmp_path / ".env"; env.write_text("", encoding="utf-8")
        cfgf = tmp_path / "config.json"
        cfgf.write_text('{"channel": "wecom_api", "profile": "wecom"}', encoding="utf-8")
        with patch.object(ss, "ENV_PATH", env), patch.object(ss, "CONFIG_PATH", cfgf):
            dlg = SettingsDialog(root, {}, on_saved=MagicMock())
        root.update()
        assert dlg._software.get() == "wecom_api"
        assert dlg._api_frame.winfo_manager() == "pack", "API 模式必须显示凭据输入框"
        dlg.win.destroy()
    finally:
        root.destroy()
