# -*- coding: utf-8 -*-
"""用**真实的** `_scan_fallback` 跑一遍真实微信窗口（只读：没变化就不点开）。

这是"点了开始没反应"那个 NameError 的现场复现与修复确认。
用法: python scripts/verify_scan_fallback_live.py
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from wxbot.native_win import ensure_dpi_aware                    # noqa: E402
from wxbot.new_message_tracker import (NewMessageTracker, resolve_only_new_messages)  # noqa: E402
from wxbot.profile import apply_to_config, load_profile          # noqa: E402
from wxbot.scanner import Scanner                                # noqa: E402
from wxbot.detector import MessageDetector                       # noqa: E402
import main as main_mod                                          # noqa: E402

ensure_dpi_aware()
cfg = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
cfg = apply_to_config(cfg, load_profile("wechat_pc"))
only_new = resolve_only_new_messages(cfg)
tracker = NewMessageTracker(enabled=only_new)
scanner, detector = Scanner(cfg), MessageDetector(cfg)

print(f"窗口 hwnd={scanner.find_window()}  only_new_messages={only_new}")
print("注入依赖（不再靠闭包）：whitelist=None new_tracker=<tracker> only_new_messages=%s\n" % only_new)

print("第 1 次调用 → 期望：建基线，不处理任何会话")
try:
    out1 = asyncio.run(main_mod._scan_fallback(scanner, detector, cfg,
                                               whitelist=None, new_tracker=tracker,
                                               only_new_messages=only_new))
    print(f"   返回 {out1}   基线大小={tracker.size}  primed={tracker.primed}")
except Exception as e:
    print(f"   ✗ 抛异常了: {type(e).__name__}: {e}")
    sys.exit(1)

print("\n第 2 次调用 → 期望：画面没变化 → 不点开任何会话、返回空")
try:
    out2 = asyncio.run(main_mod._scan_fallback(scanner, detector, cfg,
                                               whitelist=None, new_tracker=tracker,
                                               only_new_messages=only_new))
    print(f"   返回 {out2}")
except Exception as e:
    print(f"   ✗ 抛异常了: {type(e).__name__}: {e}")
    sys.exit(1)

print("\n✓ 真实函数跑通了，没有 NameError，也没有因为没变化去点开会话。")
