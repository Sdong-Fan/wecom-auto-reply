# -*- coding: utf-8 -*-
"""只读验证：把 wechat_pc profile 套用后，扫描链路在微信 PC 上能不能走通。

只做"读"：找窗口 → 抓会话列表 → 读行名 → 判客户 → 抓聊天区 → 找未回复灰气泡。
**不回复、不发送、不点击**（除了切换会话用的 PostMessage 也先不发）。

用法: python scripts/verify_wechat_readonly.py
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
from wxbot.scanner import Scanner                       # noqa: E402
from wxbot.detector import MessageDetector              # noqa: E402

ensure_dpi_aware()

cfg = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
prof = load_profile("wechat_pc")
assert prof, "没有 profiles/wechat_pc.json"
cfg = apply_to_config(cfg, prof)
print(f"套用 profile: {prof['name']}")
print(f"  窗口匹配: {cfg['wecom']['process_names']} / {cfg['wecom']['window_class']}"
      f" / 标题>={cfg['wecom']['min_title_length']} / 优先标题={prof['window'].get('prefer_title')!r}")
print(f"  列宽: col1={cfg['columns']['col1_width_px']} col2={cfg['columns']['col2_width_px']}")
print(f"  聊天区边距: top={cfg['crop']['chat_area']['top_margin_px']} "
      f"bottom={cfg['crop']['chat_area']['bottom_margin_px']}")
print(f"  我方气泡色: {cfg['detection']['bubble']['blue'].get('my_rgb')}")
print(f"  客户判定: {cfg['ocr'].get('customer_match')}"
      f"  排除={cfg['ocr'].get('non_customer_keywords')[:4]}…")
print()

scanner = Scanner(cfg)
detector = MessageDetector(cfg)

hwnd = scanner.find_window()
print(f"1) 找窗口: hwnd={hwnd}")
if not hwnd:
    sys.exit("✗ 没找到微信窗口")
print(f"   区域: {scanner.get_region()}")
print(f"   col2(会话列表): {scanner.get_col2_region()}")
print(f"   col3(聊天区):   {scanner.get_col3_region()}")

c2 = scanner.capture_chat_list()
print(f"\n2) 抓会话列表: {c2.size if c2 else None}")
if c2 is None:
    sys.exit("✗ 抓会话列表失败")

print("\n3) 逐行读会话名 + 判客户：")
row_top = cfg.get("crop", {}).get("name_row", {}).get("top_offset", -16)
row_bot = cfg.get("crop", {}).get("name_row", {}).get("bottom_offset", 56)
start_y = int(c2.size[1] * cfg["fallback"]["start_y_ratio"])
row_h = cfg["fallback"]["row_height_px"]
cw2 = c2.size[0]
for i in range(cfg["fallback"]["max_scan_rows"]):
    y = start_y + i * row_h
    if y + row_bot > c2.size[1]:
        break
    row_img = c2.crop((0, max(0, y + row_top), cw2, y + row_bot))
    name_crop, name_text = detector.read_row_name(row_img)
    is_cust = detector.is_customer_name(name_crop, name_text)
    flag = "客户" if is_cust else "非客户"
    print(f"   行{i} y={y:<4} [{flag}] {name_text[:60]!r}")

print("\n4) 抓聊天区 + 找未回复气泡（当前打开的会话）：")
chat = scanner.capture_chat_area()
print(f"   聊天区: {chat.size if chat else None}")
if chat:
    unreplied, has_blue = detector.extract_unreplied_bubbles(chat)
    print(f"   未回复灰气泡: {len(unreplied)} 个  我方有蓝/绿气泡={has_blue}")
    for u in unreplied:
        print(f"      {u!r}" if not isinstance(u, tuple) else f"      y={u[0]} 文本={u[1]!r}")
    txt = detector.extract_text(chat)
    print(f"   OCR 全文前 200 字: {txt[:200]!r}")

print("\n只读验证结束（没有发送任何消息）。")
