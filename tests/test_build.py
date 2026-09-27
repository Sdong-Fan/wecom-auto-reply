"""TDD: PyInstaller build configuration must be complete and correct."""

from pathlib import Path
import json


def test_spec_file_exists():
    """PyInstaller spec file must exist."""
    spec = Path("WeComBot.spec")
    assert spec.exists(), \
        "WeComBot.spec must exist for PyInstaller build"


def test_spec_has_hidden_imports():
    """Spec must include hidden imports for all heavy dependencies.

    PyInstaller can't auto-detect imports in torch, rapidocr, sentence_transformers,
    and some other packages. They must be explicitly listed.
    """
    spec = Path("WeComBot.spec").read_text(encoding="utf-8")

    required_hidden_imports = [
        "torch",
        "rapidocr_onnxruntime",
        "onnxruntime",
        "sentence_transformers",
        "qdrant_client",
        "openai",
        "PIL",
        "pynput",
        "win32gui",
        "win32con",
        "win32process",
    ]

    for imp in required_hidden_imports:
        assert imp in spec, \
            f"PyInstaller spec must have hidden import for '{imp}'"


def test_spec_no_longer_bundles_paddle():
    """OCR 已换成 RapidOCR/ONNX，不该再往包里塞 ~2GB 的 paddle 运行时。

    顺带解决：paddle 与 torch 抢 DLL（torch/lib/shm.dll 加载失败）。
    """
    spec = Path("WeComBot.spec").read_text(encoding="utf-8")
    assert '"paddle"' not in spec, "不该再把 paddle 列进 hiddenimports"
    assert '"paddleocr"' not in spec, "不该再把 paddleocr 列进 hiddenimports"
    assert "paddle_dlls" not in spec, "不该再打包 paddle 的 DLL"
    assert "_PADDLE_LIBS" not in spec, "不该再引用 paddle 的 libs 目录"


def test_spec_excludes_test_dirs():
    """Spec must exclude tests/, docs/, eval/ from the bundle to reduce size."""
    spec = Path("WeComBot.spec").read_text(encoding="utf-8")

    excluded_dirs = ["tests", "docs", "eval"]
    for d in excluded_dirs:
        assert d in spec, \
            f"PyInstaller spec should reference '{d}' (either to include or exclude)"


def test_env_example_exists():
    """.env.example must exist so users know what API keys to fill in."""
    env_example = Path(".env.example")
    assert env_example.exists(), \
        ".env.example must exist for first-time setup"

    content = env_example.read_text(encoding="utf-8")
    assert "DEEPSEEK_API_KEY" in content, \
        ".env.example must include DEEPSEEK_API_KEY template"


def test_config_json_exists():
    """config.json must exist and have required sections."""
    config = Path("config.json")
    assert config.exists(), "config.json must exist"

    data = json.loads(config.read_text(encoding="utf-8"))
    required_keys = ["check_interval_seconds", "wecom", "ocr", "rag"]
    for key in required_keys:
        assert key in data, f"config.json must have '{key}' section"


def test_launcher_script_exists():
    """启动.bat must exist with .env check and startup logic."""
    launcher = Path("启动.bat")
    assert launcher.exists(), \
        "启动.bat must exist as the user-facing entry point"

    content = launcher.read_text(encoding="utf-8")
    assert ".env" in content, \
        "启动.bat must check for .env file"
    assert ".exe" in content, \
        "启动.bat must launch the main exe"


def test_launcher_sets_tcl_tk_env():
    """启动.bat must set TCL_LIBRARY and TK_LIBRARY for the bundled tkinter.

    PyInstaller's tkinter runtime hook sometimes fails to locate the
    _tcl_data/_tk_data directories correctly in COLLECT mode. Setting
    these explicitly in the launcher ensures tkinter can find init.tcl.
    """
    launcher = Path("启动.bat")
    content = launcher.read_text(encoding="utf-8")
    assert "TCL_LIBRARY" in content, \
        "启动.bat must set TCL_LIBRARY for tkinter to find init.tcl"
    assert "TK_LIBRARY" in content, \
        "启动.bat must set TK_LIBRARY for tkinter to find widget scripts"
    assert "_tcl_data" in content, \
        "TCL_LIBRARY must point to _internal/_tcl_data (contains init.tcl)"
    assert "_tk_data" in content, \
        "TK_LIBRARY must point to _internal/_tk_data (contains tk scripts)"


def test_batch_files_have_crlf_line_endings():
    """Windows batch files (.bat) MUST use CRLF line endings.

    LF-only files cause CMD to display garbled Chinese text because
    CMD reads lines terminated by CRLF. With LF only, the parser
    misinterprets multi-byte UTF-8 characters.
    """
    for bat_file in ["启动.bat", "setup.bat"]:
        path = Path(bat_file)
        assert path.exists(), f"{bat_file} must exist"

        raw = path.read_bytes()
        # Must contain CRLF (\r\n), not just LF (\n)
        has_crlf = b"\r\n" in raw
        # Check that first line break is CRLF
        assert has_crlf, (
            f"{bat_file} must use CRLF line endings for Windows CMD. "
            f"LF-only endings cause garbled Chinese text on display."
        )


def test_setup_bat_creates_shortcut():
    """setup.bat must contain desktop shortcut creation logic."""
    path = Path("setup.bat")
    content = path.read_text(encoding="utf-8")
    assert "Desktop" in content or "桌面" in content, \
        "setup.bat must create a desktop shortcut"
