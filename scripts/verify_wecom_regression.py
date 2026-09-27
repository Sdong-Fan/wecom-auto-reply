# -*- coding: utf-8 -*-
"""企业微信模式回归自检：确认微信那边的改动没碰到它。

分两步：
  1. 参数级：把 wecom profile 套进 config，跟"没有 profile 时的原始 config"逐项比
  2. 行为级：只读跑一遍读取链路（找窗口→抓列表→读行名→判客户→抓聊天区→找未回复气泡）
     **不点击、不发送**

用法: python scripts/verify_wecom_regression.py
"""
from __future__ import annotations

import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from wxbot.native_win import ensure_dpi_aware          # noqa: E402
from wxbot.profile import apply_to_config, load_profile  # noqa: E402
from wxbot.scan_policy import parse_whitelist          # noqa: E402

ensure_dpi_aware()
raw = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
applied = apply_to_config(raw, load_profile("wecom"))

print("═══ 1. 参数级对比（企业微信 profile 套用后 vs 原始 config）═══")
CHECKS = [
    ("wecom.window_class", raw["wecom"]["window_class"], applied["wecom"]["window_class"]),
    ("wecom.process_names", raw["wecom"]["process_names"], applied["wecom"]["process_names"]),
    ("wecom.min_title_length", raw["wecom"]["min_title_length"], applied["wecom"]["min_title_length"]),
    ("columns.col1_width_px", raw["columns"]["col1_width_px"], applied["columns"]["col1_width_px"]),
    ("columns.col2_width_px", raw["columns"]["col2_width_px"], applied["columns"]["col2_width_px"]),
    ("crop.chat_area.top", raw["crop"]["chat_area"]["top_margin_px"],
     applied["crop"]["chat_area"]["top_margin_px"]),
    ("crop.chat_area.bottom", raw["crop"]["chat_area"]["bottom_margin_px"],
     applied["crop"]["chat_area"]["bottom_margin_px"]),
    ("fallback.start_y_ratio", raw["fallback"]["start_y_ratio"],
     applied["fallback"]["start_y_ratio"]),
    ("fallback.row_height_px", raw["fallback"]["row_height_px"],
     applied["fallback"]["row_height_px"]),
]
bad = 0
for name, before, after in CHECKS:
    same = before == after
    bad += 0 if same else 1
    print(f"  {'✓' if same else '✗'} {name:26} {before!r} → {after!r}")

NEW_KEYS = [
    ("fallback.start_y_px", applied["fallback"].get("start_y_px", "(未设置→用 ratio)")),
    ("detection.red_dot.enabled", applied.get("detection", {}).get("red_dot", {}).get("enabled", "(未设置→默认 True)")),
    ("rag.allow_auto_send", applied.get("rag", {}).get("allow_auto_send")),
    ("customers 段", applied.get("customers", "(不存在)")),
    ("wecom.title_hint", applied["wecom"].get("title_hint", "(未设置)")),
    ("ocr.customer_match", applied["ocr"].get("customer_match", "(未设置→默认 suffix)")),
]
print("\n  微信那边的改动在企微模式下是否被关掉：")
for name, val in NEW_KEYS:
    print(f"      {name:30} = {val!r}")

wl = parse_whitelist(applied.get("customers", {}))
print(f"\n  白名单解析结果 = {wl!r}  → {'不启用（不拦任何会话）' if wl is None else '启用了'}")
print(f"  红点路径会执行吗 = {bool(applied.get('detection', {}).get('red_dot', {}).get('enabled', True))}")

print("\n═══ 2. 行为级对比（只读，不点击不发送）═══")
from wxbot.scanner import Scanner            # noqa: E402
from wxbot.detector import MessageDetector   # noqa: E402

scanner = Scanner(applied)
detector = MessageDetector(applied)
hwnd = scanner.find_window()
print(f"  找窗口: hwnd={hwnd}")
if not hwnd:
    print("  ✗ 没找到企业微信窗口 —— 它开着吗？")
    sys.exit(1)
print(f"  区域={scanner.get_region()}  col2={scanner.get_col2_region()}  col3={scanner.get_col3_region()}")

c2 = scanner.capture_chat_list()
print(f"  抓会话列表: {c2.size if c2 else None}")
if c2 is None:
    sys.exit("✗ 抓会话列表失败")

row_top = applied["crop"]["name_row"]["top_offset"]
row_bot = applied["crop"]["name_row"]["bottom_offset"]
x0 = int(applied["crop"]["name_row"].get("x_from_ratio", 0.0) * c2.size[0])
x1 = int(applied["crop"]["name_row"].get("x_to_ratio", 1.0) * c2.size[0])
start_y = int(applied["fallback"]["start_y_ratio"] * c2.size[1])
row_h = applied["fallback"]["row_height_px"]
print(f"  行几何: start_y={start_y}（用 ratio，因为 profile 没给绝对值） row_height={row_h}")

print("\n  逐行读会话名 + 判客户：")
cust_count = 0
for i in range(applied["fallback"]["max_scan_rows"]):
    y = start_y + i * row_h
    if y + row_bot > c2.size[1]:
        break
    row_img = c2.crop((x0, max(0, y + row_top), x1, y + row_bot))
    nc, nt = detector.read_row_name(row_img)
    is_cust = detector.is_customer_name(nc, nt)
    cust_count += 1 if is_cust else 0
    print(f"     行{i} y={y:<4} [{'客户' if is_cust else '非客户'}] {nt[:50]!r}")
print(f"  → 判出客户 {cust_count} 个（预期：只认带 @微信 的那些）")

chat = scanner.capture_chat_area()
print(f"\n  capture_chat_area → {None if chat is None else chat.size}")
if chat is not None:
    unreplied, has_blue = detector.extract_unreplied_bubbles(chat)
    n = 0 if unreplied is None else len(unreplied)
    print(f"  未回复灰气泡={n}"
          f"{'（None＝图太小，函数约定 w/h<20 直接返回 None）' if unreplied is None else ''}"
          f"  我方有蓝气泡={has_blue}")
    print(f"  OCR 前 120 字: {detector.extract_text(chat)[:120]!r}")

print(f"\n结论：参数差异 {bad} 处" + ("（全一致）" if bad == 0 else " ← 需要检查！"))
