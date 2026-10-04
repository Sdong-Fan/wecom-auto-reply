# config/settings_store.py
"""设置面板的读写层 —— 把"界面上改的东西"安全地落到两个文件里。

分工（刻意分开）：
    .env          只放**密钥类**（LLM key、企业微信 Secret）。已被 .gitignore 忽略。
    config.json   放**非密钥**（选哪个软件 / profile / open_kfid 等），跟着程序分发。

两条硬要求：
1. **`.env` 写回不能毁掉用户的文件** —— 保留原有注释与顺序，只替换/追加目标键。
2. **密钥不进日志** —— 只打键名和脱敏值。
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent.parent


def _app_dir() -> Path:
    """"程序目录" —— 打包后指 **exe 所在目录**。

    ★ 为什么必须这样：`main.py` 的 `load_dotenv()` 与 `ConfigManager("config.json")`
    都是按 **CWD（exe 同级目录）** 找文件的，而 PyInstaller 下 `__file__`
    指向 `_internal/`：
      * 设置面板把 `.env` 写进 `_internal/` → 用户看不见，**重启就丢 Key**
      * 面板把 `config.json` 写进 `_internal/` → 程序读的是 exe 那份，
        **改完的软件选择/阈值重启就还原**
    实测打包版两个坑都踩到了，所以统一到 exe 目录。源码模式下 HERE 就是项目根目录，行为不变。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return HERE


APP_DIR = _app_dir()
ENV_PATH = APP_DIR / ".env"
CONFIG_PATH = APP_DIR / "config.json"

# 面板会写的密钥键（其余键一律不动）
SECRET_KEYS = ("LLM_API_KEY", "WECOM_CORP_ID", "WECOM_KF_SECRET", "WECOM_OPEN_KFID")

_KEY_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")

# 软件选择 → (id, 显示名, channel, profile, 备注)
# ★ 这张表只是**内置推荐项**，不是"只支持这三种"：
#   profiles/ 目录里任何 *.json 都会被自动发现并出现在设置面板里
#   （见 discover_profiles / all_choices）—— 接新软件不用改代码，
#   跑一次 scripts/calibrate_chat_app.py 生成 profile 就多一个选项。
BUILTIN_CHOICES = [
    ("wecom_screenshot", "企业微信 · 截图模式", "screenshot", "wecom", ""),
    ("wecom_api", "企业微信 · API 模式", "wecom_api", "wecom", ""),
    ("wechat_pc", "微信 PC · 截图模式", "screenshot", "wechat_pc",
     "还没标定：先跑 scripts/calibrate_chat_app.py 生成 profiles/wechat_pc.json"),
]

# 兼容旧名字（测试与其它模块按这个名字引用）
SOFTWARE_CHOICES = BUILTIN_CHOICES

PROFILES_DIR = HERE / "profiles"


def _profile_display_name(profile_id: str) -> str:
    """从 profile 文件里取个好听的名字（没有就用文件名）。"""
    p = PROFILES_DIR / f"{profile_id}.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return profile_id
    for key in ("display_name", "name"):
        if data.get(key):
            return str(data[key])
    win = data.get("window") or {}
    hint = win.get("title_hint") or win.get("prefer_title")
    return f"{hint}" if hint else profile_id


def discover_profiles() -> list:
    """扫 profiles/ 目录，返回**内置项之外**的 profile：[(id, 显示名, profile_id)]。

    跳过 .template（那是给人抄的模板）与内置项已经用到的 profile。
    """
    used = {prof for _sid, _l, _ch, prof, _n in BUILTIN_CHOICES if prof}
    out = []
    if not PROFILES_DIR.is_dir():
        return out
    for f in sorted(PROFILES_DIR.glob("*.json")):
        pid = f.stem
        if pid in used or f.name.endswith(".template"):
            continue
        out.append((f"profile:{pid}", f"其它软件 · {_profile_display_name(pid)}", pid))
    return out


def all_choices() -> list:
    """内置项 + 自动发现的 profile，统一成与 BUILTIN_CHOICES 相同的五元组。"""
    out = list(BUILTIN_CHOICES)
    for sid, label, pid in discover_profiles():
        out.append((sid, label, "screenshot", pid, ""))
    return out


def profile_available(profile_id: str) -> bool:
    """该 profile 标定好了吗（文件在就算好）。空 profile＝不需要标定。"""
    if not profile_id:
        return True
    return (PROFILES_DIR / f"{profile_id}.json").is_file()


def software_options() -> list:
    """设置面板用：[(id, 显示名, 可选?, 备注), …]。不可选时备注一定有内容。"""
    out = []
    for sid, label, _ch, prof, note in all_choices():
        ok = profile_available(prof)
        if ok:
            out.append((sid, label, True, ""))
        else:
            out.append((sid, label, False,
                        note or f"还没标定（缺 profiles/{prof}.json）"))
    return out


def mask(value: str) -> str:
    if not value:
        return "(空)"
    return f"…{value[-4:]}" if len(value) > 8 else "已设置"


def read_env(path: os.PathLike = None) -> Dict[str, str]:
    """读 .env 成 dict（不注入进程环境）。"""
    out: Dict[str, str] = {}
    p = Path(path or ENV_PATH)
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        m = _KEY_RE.match(line)
        if not m:
            continue
        k, _, v = line.partition("=")
        out[m.group(1)] = v.strip().strip('"').strip("'")
    return out


def update_env(updates: Dict[str, str], path: os.PathLike = None) -> None:
    """把 updates 写进 .env：已有的键就地替换，没有的追加；**注释与其它行原样保留**。

    空字符串也会写进去（＝清空该项），这样用户能通过清空来撤销。
    """
    p = Path(path or ENV_PATH)
    lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    seen = set()
    out = []
    for line in lines:
        m = _KEY_RE.match(line)
        if m and m.group(1) in updates:
            key = m.group(1)
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, val in updates.items():
        if key not in seen:
            if out and out[-1].strip():
                out.append("")
            out.append(f"{key}={val}")
    text = "\n".join(out).rstrip("\n") + "\n"

    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)
    # 只记键名 + 脱敏值，绝不记明文
    logger.info("已写入 .env: " + ", ".join(f"{k}={mask(updates[k])}" for k in updates))


def load_config(path: os.PathLike = None) -> dict:
    p = Path(path or CONFIG_PATH)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def save_config(cfg: dict, path: os.PathLike = None) -> None:
    """原子写 config.json（先写 .tmp 再 replace，避免写一半断电留个坏文件）。"""
    p = Path(path or CONFIG_PATH)
    if p.exists():
        try:
            shutil.copy2(p, p.with_suffix(p.suffix + ".bak"))
        except Exception:
            pass
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)
    logger.info(f"已写入 {p}（channel={cfg.get('channel')} profile={cfg.get('profile')}）")


def software_from_config(cfg: dict) -> str:
    """config → 当前选中的软件 id（面板高亮用）。"""
    channel = str(cfg.get("channel", "screenshot")).lower()
    profile = str(cfg.get("profile", "") or "")
    for sid, _label, ch, prof, _ok in all_choices():
        if ch == channel and prof == profile:
            return sid
    return "custom"


def apply_software(cfg: dict, software_id: str) -> dict:
    """软件选择 → 写回 channel / profile（就地改并返回）。"""
    for sid, _label, ch, prof, _ok in all_choices():
        if sid == software_id:
            cfg["channel"] = ch
            cfg["profile"] = prof
            return cfg
    logger.warning(f"未知的软件选择: {software_id}")
    return cfg


def read_current_settings(env_path: os.PathLike = None,
                          config_path: os.PathLike = None) -> dict:
    """面板初始值：把 .env 与 config.json 合成一份（密钥原样返回给输入框）。"""
    env = read_env(env_path)
    cfg = load_config(config_path)
    return {
        "llm_api_key": env.get("LLM_API_KEY") or env.get("DEEPSEEK_API_KEY", ""),
        "llm_base_url": env.get("LLM_BASE_URL") or env.get("DEEPSEEK_BASE_URL", ""),
        "llm_model": env.get("LLM_MODEL", ""),
        "wecom_corp_id": env.get("WECOM_CORP_ID", ""),
        "wecom_kf_secret": env.get("WECOM_KF_SECRET", ""),
        "wecom_open_kfid": env.get("WECOM_OPEN_KFID", ""),
        "software": software_from_config(cfg),
    }


def save_settings(values: dict, env_path: os.PathLike = None,
                  config_path: os.PathLike = None) -> dict:
    """保存面板内容 → 返回一份"改了什么"的摘要（用于提示是否需要重启）。

    LLM 三项统一写 ``LLM_*``（同时清掉旧的 ``DEEPSEEK_*``，免得两套并存互相干扰）；
    企业微信三项写 ``WECOM_*``；软件选择写 config.json。
    """
    env_updates = {
        "LLM_API_KEY": values.get("llm_api_key", ""),
        "LLM_BASE_URL": values.get("llm_base_url", ""),
        "LLM_MODEL": values.get("llm_model", ""),
        "WECOM_CORP_ID": values.get("wecom_corp_id", ""),
        "WECOM_KF_SECRET": values.get("wecom_kf_secret", ""),
        "WECOM_OPEN_KFID": values.get("wecom_open_kfid", ""),
        "DEEPSEEK_API_KEY": "",
        "DEEPSEEK_BASE_URL": "",
    }
    update_env(env_updates, env_path)

    cfg = load_config(config_path)
    before = software_from_config(cfg)
    apply_software(cfg, values.get("software", before))
    after = software_from_config(cfg)
    save_config(cfg, config_path)
    return {
        "software_changed": before != after,
        "before": before,
        "after": after,
    }
