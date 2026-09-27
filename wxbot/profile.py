# wxbot/profile.py
"""截图模式的「软件 profile」—— 把"某个聊天软件长什么样"从代码里挪到 JSON。

一份 profile 描述三件事（都由 ``scripts/calibrate_chat_app.py`` 自动量出来）：

1. **窗口怎么找**：进程名 / 窗口类 / 标题最短长度
2. **区域在哪**：会话列表区、聊天区左右边界、聊天区上下边距
3. **我方气泡什么颜色**：主题色 RGB + 容差（微信 PC 是绿的、企业微信是蓝的）

用法：
    profile = load_profile("wecom")
    cfg = apply_to_config(cfg, profile)      # 就地覆盖 cfg 里的对应字段
    scanner = Scanner(cfg)

没有 profile（或 ``profile`` 字段为空）时行为与改造前**完全一致** —— 直接用
config.json 里原来那些值。这是刻意的：先保证现在能跑的企业微信不被改坏。
"""

from __future__ import annotations

import copy
import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILES_DIR = os.path.join(HERE, "profiles")


def list_profiles() -> list:
    """返回 [(id, 显示名), …]。"""
    out = []
    if not os.path.isdir(PROFILES_DIR):
        return out
    for fn in sorted(os.listdir(PROFILES_DIR)):
        if not fn.endswith(".json"):
            continue
        pid = fn[:-5]
        try:
            with open(os.path.join(PROFILES_DIR, fn), encoding="utf-8") as f:
                out.append((pid, json.load(f).get("name", pid)))
        except Exception:
            out.append((pid, pid))
    return out


def load_profile(pid: str) -> Optional[dict]:
    """按 id 载入 ``profiles/<id>.json``；也接受一个直接路径。"""
    if not pid:
        return None
    path = pid if os.path.isfile(pid) else os.path.join(PROFILES_DIR, f"{pid}.json")
    if not os.path.isfile(path):
        logger.warning(f"profile 不存在: {path}")
        return None
    try:
        with open(path, encoding="utf-8") as f:
            p = json.load(f)
        p.setdefault("_id", os.path.basename(path)[:-5])
        return p
    except Exception as e:
        logger.warning(f"profile 读取失败 {path}: {e}")
        return None


def apply_to_config(cfg: dict, profile: dict) -> dict:
    """把 profile 覆盖到 config 上（返回新的 dict，不改原对象）。

    映射关系（其它字段一律不动）：
        window.process_names      → wecom.process_names
        window.window_class       → wecom.window_class
        window.min_title_length   → wecom.min_title_length
        layout.chat_x             → columns.col1_width_px + columns.col2_width_px
        layout.chat_top_margin_px → crop.chat_area.top_margin_px
        layout.chat_bottom_margin_px → crop.chat_area.bottom_margin_px
        bubbles.my_color_rgb / my_color_tolerance
                                  → detection.bubble.blue.my_rgb / my_tolerance

    ``columns.col1_width_px`` 是"左侧导航图标栏"的宽度（截图时跳过它，免得图标
    混进会话名 OCR）。它量不出来，profile 里给了就用，没给就沿用 config 原值。
    """
    if not profile:
        return cfg
    out = copy.deepcopy(cfg)

    win = profile.get("window") or {}
    wc = out.setdefault("wecom", {})
    if win.get("process_names"):
        wc["process_names"] = list(win["process_names"])
    if win.get("window_class"):
        wc["window_class"] = win["window_class"]
    if win.get("min_title_length") is not None:
        wc["min_title_length"] = int(win["min_title_length"])
    # 发送时按标题找窗口（pyautogui.getWindowsWithTitle）—— 必须跟着 profile 走，
    # 否则微信模式会去激活企业微信、把回复粘错地方。
    if win.get("title_hint") or win.get("prefer_title"):
        wc["title_hint"] = win.get("title_hint") or win.get("prefer_title")

    lay = profile.get("layout") or {}
    cols = out.setdefault("columns", {})
    chat_x = lay.get("chat_x")
    if chat_x:
        nav = lay.get("col1_width_px", cols.get("col1_width_px", 0)) or 0
        cols["col1_width_px"] = int(nav)
        cols["col2_width_px"] = max(1, int(chat_x) - int(nav))

    ca = out.setdefault("crop", {}).setdefault("chat_area", {})
    if lay.get("chat_top_margin_px") is not None:
        ca["top_margin_px"] = int(lay["chat_top_margin_px"])
    if lay.get("chat_bottom_margin_px") is not None:
        ca["bottom_margin_px"] = int(lay["chat_bottom_margin_px"])

    # 会话列表的行几何：**绝对像素**优先（比例会随窗口高度飘，实测微信上偏 29px
    # 就跨到隔壁行，导致"用 A 的名字回复 B 的内容"）。
    fb = out.setdefault("fallback", {})
    if lay.get("list_start_y_px") is not None:
        fb["start_y_px"] = int(lay["list_start_y_px"])
    if lay.get("row_height_px") is not None:
        fb["row_height_px"] = int(lay["row_height_px"])
    if lay.get("max_scan_rows") is not None:
        fb["max_scan_rows"] = int(lay["max_scan_rows"])

    nr = out.setdefault("crop", {}).setdefault("name_row", {})
    if lay.get("name_row_x_from_ratio") is not None:
        nr["x_from_ratio"] = float(lay["name_row_x_from_ratio"])
    if lay.get("name_row_x_to_ratio") is not None:
        nr["x_to_ratio"] = float(lay["name_row_x_to_ratio"])

    bub = profile.get("bubbles") or {}
    if bub.get("my_color_rgb") and bub.get("my_side_rule", "color") == "color":
        blue = out.setdefault("detection", {}).setdefault("bubble", {}).setdefault("blue", {})
        blue["my_rgb"] = [int(v) for v in bub["my_color_rgb"]]
        blue["my_tolerance"] = int(bub.get("my_color_tolerance", 20))

    # 客户判定规则：企业微信用 "@微信" 后缀；微信 PC 根本没有这个后缀，用排除法。
    cust = profile.get("customers") or {}
    if cust.get("name_rule"):
        ocr = out.setdefault("ocr", {})
        ocr["customer_match"] = "any" if cust["name_rule"] == "any" else "suffix"
        if cust.get("suffixes") is not None:
            ocr["customer_name_suffixes"] = list(cust["suffixes"])
        if cust.get("exclude_keywords"):
            ocr["non_customer_keywords"] = list(cust["exclude_keywords"])
    # 白名单：自定义软件里"哪些会话算客户"必须用户指定；启用后不在名单里的**不点开**。
    if cust.get("require_whitelist") is not None:
        c = out.setdefault("customers", {})
        c["require_whitelist"] = bool(cust["require_whitelist"])
        c["whitelist"] = list(cust.get("whitelist") or [])

    # 是否允许自动发送：自定义软件（个人微信）默认**关掉**，只进待人工。
    disp = profile.get("dispatch") or {}
    if disp.get("allow_auto_send") is not None:
        out.setdefault("rag", {})["allow_auto_send"] = bool(disp["allow_auto_send"])

    # 「只处理点开始之后收到的新消息」开关（自定义软件的安全规则）
    scan = profile.get("scan") or {}
    if scan.get("only_new_messages") is not None:
        out.setdefault("scan", {})["only_new_messages"] = bool(scan["only_new_messages"])
    for k in ("dynamic_rows", "list_top_skip_px",
              "row_x_from_ratio", "row_x_to_ratio"):
        if scan.get(k) is not None:
            out.setdefault("scan", {})[k] = scan[k]

    # 红点检测开关：企业微信的红点规则在微信个人号上会把红头像误判成未读
    det = profile.get("detection") or {}
    if (det.get("red_dot") or {}).get("enabled") is not None:
        rd = out.setdefault("detection", {}).setdefault("red_dot", {})
        rd["enabled"] = bool(det["red_dot"]["enabled"])

    return out
