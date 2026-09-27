# tests/test_packaging_privacy.py
"""打包的隐私底线：**真实凭据和客户数据绝不能进包**。

起因：`WeComBot.spec` 里原本有 `("data", "data")` —— 会把整个 data 目录
（含 `data/context/` 里 8 份真实客户聊天记录、待人工队列）打进 exe。
这类错误一次就够毁掉信任，所以用测试钉住。
"""

import re
from pathlib import Path

import pytest

from scripts import build as build_mod
from scripts import privacy_check as pc

ROOT = Path(__file__).resolve().parent.parent
SPEC_RAW = (ROOT / "WeComBot.spec").read_text(encoding="utf-8")
# 注释里会提到这条禁令本身，比对前先去掉注释
SPEC = "\n".join(ln.split("#")[0] for ln in SPEC_RAW.splitlines())
BUILD_RAW = (ROOT / "scripts" / "build.py").read_text(encoding="utf-8")
BUILD = "\n".join(ln.split("#")[0] for ln in BUILD_RAW.splitlines())


# ── spec：不许打包客户数据，必须打包提示词与标定 ──────────────────────

def test_spec_does_not_bundle_whole_data_dir():
    """★ 这条是这次审计的核心：整个 data/ 里有真实客户聊天记录。"""
    assert '("data", "data")' not in SPEC, \
        "spec 不许再把整个 data 目录打进包（含 data/context 客户记录）"
    assert '("data", "data")' in SPEC_RAW, "注释里要写明这条禁令（后人别再犯）"


def test_spec_bundles_prompts_and_profiles():
    """提示词与窗口标定要进包，否则对方拿到就跑不起来。"""
    assert '("prompts", "prompts")' in SPEC
    assert '("profiles", "profiles")' in SPEC


def test_spec_still_has_bge_model():
    assert '("bge_model", "bge_model")' in SPEC, "向量模型要内置（离线可用）"


# ── build.py：白名单组装 + 强制隐私自检 ───────────────────────────────

def test_content_sources_exclude_customer_data():
    """要复制进包的只有"内容资产"；客户数据一个都不许在里面。"""
    srcs = [str(p) for p in build_mod.CONTENT_SOURCES]
    for bad in ("context", "state", "learned", "logs", ".env"):
        assert not any(bad in s for s in srcs), f"{bad} 不许进包：{srcs}"
    # 空目录是**创建**的，不是复制的
    assert "data/context" in build_mod.RUNTIME_DIRS
    assert "logs" in build_mod.RUNTIME_DIRS


def test_extras_include_env_example_not_env():
    assert ".env.example" in build_mod.EXTRAS
    assert ".env" not in build_mod.EXTRAS, "绝不能把真实 .env 发出去"


def test_build_copies_content_assets():
    for frag in ("prompts", "profiles", "meta.json", "collection",
                 "knowledge_base", "使用说明"):
        assert frag in BUILD_RAW, f"build.py 应该复制/生成 {frag}"
    assert "privacy_gate" in BUILD_RAW, "打包必须带隐私自检这一步"
    assert "NEVER_SHIP" in BUILD_RAW


def test_usage_txt_mentions_privacy_and_key():
    assert "使用说明" in BUILD_RAW
    assert "API Key" in BUILD_RAW, "使用说明要告诉对方怎么填自己的 Key"
    assert "不上传" in BUILD_RAW or "只存在本机" in BUILD_RAW, "要写清隐私口径"


# ── 打包后 .env / config 必须落在 exe 同级目录（否则重启丢配置）───────

def test_frozen_paths_point_next_to_exe(monkeypatch):
    """★ 打包版踩过：设置面板写进 _internal/，而程序按 CWD 读 exe 同级的文件
    —— 结果"填好的 Key 重启就没了""改的软件选择重启就还原"。"""
    import importlib
    import sys
    from config import settings_store as ss

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable",
                        str(ROOT / "dist" / "WeComBot" / "启动.exe"),
                        raising=False)
    try:
        importlib.reload(ss)
        assert ss.ENV_PATH == ROOT / "dist" / "WeComBot" / ".env"
        assert ss.CONFIG_PATH == ROOT / "dist" / "WeComBot" / "config.json"
    finally:
        monkeypatch.undo()
        importlib.reload(ss)          # 恢复源码模式的路径
    assert ss.CONFIG_PATH == ROOT / "config.json"


# ── privacy_check：真的能抓到东西 ─────────────────────────────────────

def _mk(tmp_path, rel, text):
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_detects_env_file(tmp_path):
    _mk(tmp_path, ".env", "LLM_API_KEY=sk-abcdefghijklmnopqrstuvwxyz\n")
    bad, _ = pc.check_files(tmp_path)
    assert any("env" in w for _, w, _ in bad)


def test_allows_env_example(tmp_path):
    _mk(tmp_path, ".env.example", "LLM_API_KEY=\n")
    bad, _ = pc.check_files(tmp_path)
    assert not bad, bad


def test_detects_customer_context_and_state(tmp_path):
    _mk(tmp_path, "data/context/客户A_微信.jsonl", "{}\n")
    _mk(tmp_path, "data/state/pending_queue.json", "{}\n")
    bad, _ = pc.check_files(tmp_path)
    whys = " ".join(w for _, _, w in bad)
    assert "客户聊天记录" in whys
    assert "运行状态" in whys


def test_detects_logs_dir(tmp_path):
    _mk(tmp_path, "logs/monitor.log", "客户说：押金多少\n")
    bad, _ = pc.check_files(tmp_path)
    assert any(r.startswith("logs/") for _, r, _ in bad)


def test_detects_real_secret_in_text(tmp_path):
    # ★ 用**不在白名单里**的假值：白名单里的示例值（wwe0123456789abcd 等）
    #   是允许出现在文档/测试里的，拿它们做"能否检测出来"的样本会永远通过
    _mk(tmp_path, "config/notes.md", "corp id: wweffffffffffffffff\n")
    _mk(tmp_path, "config/key.md", "key: sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123\n")
    bad = pc.check_text(tmp_path)
    whys = " ".join(w for _, _, w in bad)
    assert "corp id" in whys
    assert "API Key" in whys


def test_placeholder_key_not_flagged(tmp_path):
    _mk(tmp_path, "a.md", "DEEPSEEK_API_KEY=sk-your-deepseek-key\n")
    assert pc.check_text(tmp_path) == []


def test_allowed_examples_not_flagged(tmp_path):
    """我们自己写在文档/测试里的合成值不算泄漏（否则自检天天误报）。"""
    _mk(tmp_path, "doc.md",
        "corp id 填 wwe0123456789abcd，IP 是 203.0.113.10，"
        "路径 D:\\your-project\n")
    assert pc.check_text(tmp_path) == []


def test_detects_customer_name_from_local_needles(tmp_path, monkeypatch):
    """真实姓名**不写在仓库里**（上传 GitHub 要匿名化），
    靠本地私密名单抓：`.privacy_needles`（已 gitignore）。"""
    monkeypatch.setattr(pc, "NEEDLE_FILE", tmp_path / ".privacy_needles")
    (tmp_path / ".privacy_needles").write_text("张三\n", encoding="utf-8")
    _mk(tmp_path, "n.md", "客户：张三 说……\n")
    whys = " ".join(w for _, _, w in pc.check_text(tmp_path))
    assert "本地私密名单" in whys


def test_detects_dev_path(tmp_path):
    _mk(tmp_path, "p.md", "路径 E:\\dev-machine\\proj\n")   # 非白名单的绝对路径
    whys = " ".join(w for _, _, w in pc.check_text(tmp_path))
    assert "绝对路径" in whys


def test_phone_number_whitelist(tmp_path):
    _mk(tmp_path, "kb.md", "客服电话 13800001234\n")     # 示例资料里的号码，允许
    assert pc.check_text(tmp_path) == []
    _mk(tmp_path, "real.md", "客户电话 15912345678\n")
    assert pc.check_text(tmp_path), "真实手机号要报出来"


def test_gitkeep_and_tests_not_flagged(tmp_path):
    _mk(tmp_path, "data/pending/.gitkeep", "")
    _mk(tmp_path, "tests/test_guard.py", "def test_x(): pass\n")
    bad, _ = pc.check_files(tmp_path)
    assert not bad, bad


def test_binary_scan_finds_needle(tmp_path):
    p = tmp_path / "启动.exe"
    p.write_bytes(b"\x00" * 100 + b"UfKoc" + b"\x00" * 100)
    bad = pc.check_binary(tmp_path)
    assert bad and "二进制" in bad[0][0]
