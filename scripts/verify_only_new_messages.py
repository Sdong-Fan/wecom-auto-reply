# -*- coding: utf-8 -*-
"""只读验证「只处理新消息」在真实微信上不误触发。

做两件事（都不点击、不发送）：
  1. 按 _scan_fallback 同样的方式逐行算 key/fp，建基线
  2. 立刻再扫一遍 —— 期望"新消息 = 0"（没变化就不该被当成新消息）

用法: python scripts/verify_only_new_messages.py
"""
from __future__ import annotations

import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from wxbot.native_win import ensure_dpi_aware                    # noqa: E402
from wxbot.new_message_tracker import (NewMessageTracker, resolve_only_new_messages,
                                       row_fp, row_key)          # noqa: E402
from wxbot.profile import apply_to_config, load_profile          # noqa: E402
from wxbot.scanner import Scanner                                # noqa: E402
from wxbot.detector import MessageDetector                       # noqa: E402

ensure_dpi_aware()
cfg = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
cfg = apply_to_config(cfg, load_profile("wechat_pc"))
print(f"only_new_messages = {resolve_only_new_messages(cfg)}")
print(f"allow_auto_send   = {cfg['rag'].get('allow_auto_send')}")
print(f"whitelist 启用     = {cfg.get('customers', {}).get('require_whitelist')}\n")

scanner = Scanner(cfg)
detector = MessageDetector(cfg)
hwnd = scanner.find_window()
print(f"窗口 hwnd={hwnd}")
if not hwnd:
    sys.exit("✗ 没找到微信窗口")

f = cfg["fallback"]
crop = cfg["crop"]["name_row"]
row_top, row_bot = crop["top_offset"], crop["bottom_offset"]


def scan_rows(c2_img):
    """和 _scan_fallback 一样地逐行算 (i, y, key, fp)。"""
    x0 = int(crop.get("x_from_ratio", 0.0) * c2_img.size[0])
    x1 = int(crop.get("x_to_ratio", 1.0) * c2_img.size[0])
    start_y = f.get("start_y_px") or int(c2_img.size[1] * f.get("start_y_ratio", 0.09))
    out = []
    for i in range(f.get("max_scan_rows", 6)):
        y = start_y + i * f.get("row_height_px", 98)
        if y + row_bot > c2_img.size[1] or y + row_top < 0:
            continue
        img = c2_img.crop((x0, y + row_top, x1, y + row_bot))
        if img.size[0] <= 0 or img.size[1] <= 0:
            continue
        name_area = img.crop((0, 0, max(1, int(img.size[0] * 0.72)),
                              max(1, int(img.size[1] * 0.45))))
        out.append((i, y, row_key(name_area), row_fp(
            img.crop((0, 0, max(1, int(img.size[0] * 0.72)), img.size[1])))))
    return out


t = NewMessageTracker(enabled=True)
c2 = scanner.capture_chat_list()
print(f"会话列表 {c2.size}")
rows1 = scan_rows(c2)
n = t.prime((k, fp) for _i, _y, k, fp in rows1)
print(f"1) 建基线：{n} 行 → {n} 个会话")
for i, y, k, fp in rows1:
    print(f"     行{i} y={y:<4} key={k} fp={fp}")

print("\n2) 立刻再扫一遍（期望：新消息 = 0）")
c2b = scanner.capture_chat_list()
rows2 = scan_rows(c2b)
new = [(i, y) for i, y, k, fp in rows2 if t.is_new(k, fp)]
print(f"      判定为新消息的行 = {new}")
print(f"      {'✓ 没有误触发' if not new else '✗ 有误触发！需要检查指纹区域'}")

print("\n3) 模拟『对方发来新消息』（用另一串 fp 代替，不改任何状态）")
k0 = rows2[0][2] if rows2 else None
if k0:
    print(f"      同一个会话 key={k0} 换成不同 fp → 判为新消息 = "
          f"{t.is_new(k0, 'DIFFERENT_CONTENT')}")

print("\n只读验证结束（没有点击、没有发送）。")
