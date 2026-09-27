# -*- coding: utf-8 -*-
"""聊天软件标定器（通用）：对着任意聊天软件的窗口，自动量出一份 profile。

这是"截图模式兼容其它软件"的核心工具 —— 企业微信/微信 PC/QQ/钉钉 都用它标定，
标定结果存 ``profiles/<名字>.json``，主程序按 profile 决定"窗口怎么找、区域在哪、
我方气泡什么颜色"。

自动量出：
  1. 窗口三要素（进程名 / 窗口类 / 标题长度）—— 用 WindowFromPoint 拾取或按进程名找
  2. 会话列表区 与 聊天区 的左右边界 —— 像素锚点（底色众数）
  3. 聊天区上下边距 —— 标题栏高度 + 输入框顶（整行单色分隔线）
  4. 我方气泡颜色 —— 聊天区里非灰非白的众数底色（主题色）
  5. 自检：按量出来的区域裁一遍、OCR 一遍，把我方/对方分好的结果打出来给你看

只读：PrintWindow 抓窗口，不点击、不输入、不发消息。

用法:
    python scripts/calibrate_chat_app.py --name 企业微信
    python scripts/calibrate_chat_app.py --name 微信PC --process Weixin.exe
    python scripts/calibrate_chat_app.py --name 微信PC --pick     # 把鼠标停在窗口上按回车拾取
"""
from __future__ import annotations

import argparse
import ctypes
import io
import json
import os
import sys
import time
from ctypes import wintypes

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from wxbot.native_win import (detect_panels, ensure_dpi_aware, find_main_hwnd,
                              grab_window, window_rect)  # noqa: E402

ensure_dpi_aware()

PROFILES_DIR = os.path.join(HERE, "profiles")


# ── 窗口拾取 ──────────────────────────────────────────────────────────

def pick_window():
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    u32.WindowFromPoint.argtypes = [wintypes.POINT]
    u32.WindowFromPoint.restype = wintypes.HWND
    u32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    u32.GetAncestor.restype = wintypes.HWND
    u32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    p = wintypes.POINT()
    u32.GetCursorPos(ctypes.byref(p))
    hwnd = u32.WindowFromPoint(wintypes.POINT(p.x, p.y))
    if not hwnd:
        return None
    return int(u32.GetAncestor(hwnd, 2) or hwnd)


def window_info(hwnd):
    from wxbot.native_win import _k32, _u32
    cls = ctypes.create_unicode_buffer(256)
    _u32.GetClassNameW(hwnd, cls, 256)
    title = ctypes.create_unicode_buffer(256)
    _u32.GetWindowTextW(hwnd, title, 256)
    pid = wintypes.DWORD()
    _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = ""
    h = _k32.OpenProcess(0x1000, False, pid.value)
    if h:
        try:
            b = ctypes.create_unicode_buffer(1024)
            n = wintypes.DWORD(1024)
            if _k32.QueryFullProcessImageNameW(h, 0, b, ctypes.byref(n)):
                exe = os.path.basename(b.value)
        finally:
            _k32.CloseHandle(h)
    return exe, cls.value, title.value


# ── 量区域 ────────────────────────────────────────────────────────────

def measure(arr, header_h=90):
    """像素锚点量出 列表区 / 聊天区 / 输入框顶。"""
    h, w, _ = arr.shape
    panels = detect_panels(Image.fromarray(arr))
    if not panels:
        return None
    chat_x = panels["x0"]
    # 聊天区右边界：底色占比掉下来的那一列（排除右侧侧边栏）
    right = arr[::8, w // 2::8].reshape(-1, 3)
    vals, cnt = np.unique(right, axis=0, return_counts=True)
    bg = vals[cnt.argmax()]
    isbg = np.abs(arr.astype(np.int16) - bg.astype(np.int16)).sum(-1) <= 6
    col = isbg[h // 4: h * 3 // 4].mean(0)
    # 从 chat_x 往右找第一段连续 <0.3 的区域起点（≈ 侧边栏/边界）
    below = np.where(col[chat_x:] < 0.30)[0]
    chat_right = chat_x + int(below[0]) if len(below) else w

    row = isbg[:, chat_x:chat_right].mean(1)
    band = arr[:, chat_x:chat_right].astype(np.int16)
    seps = np.where((band.std(axis=(1, 2)) < 4) & (row < 0.1))[0]
    seps = [int(s) for s in seps if s > chat_x and (not seps.tolist() or True)]
    seps = sorted(set(seps))
    seps = [s for i, s in enumerate(seps) if i == 0 or s - seps[i - 1] > 3]
    below_seps = [s for s in seps if s > 0.45 * h]
    above_seps = [s for s in seps if header_h < s < (below_seps[0] if below_seps else h) - 50]
    input_top = below_seps[0] if below_seps else h

    # 会话列表区：从窗口左边缘到 chat_x
    list_x = 0
    list_w = chat_x
    return {
        "window_w": w, "window_h": h,
        "list_x": list_x, "list_w": list_w,
        "chat_x": chat_x, "chat_w": chat_right - chat_x,
        "chat_top_margin_px": (above_seps[-1] if above_seps else header_h),
        "chat_bottom_margin_px": h - input_top,
        "bg": [int(v) for v in bg],
    }


def box_fill(chat, box):
    """一个 OCR 框的底色（框内众数色）与"底色纯度"。

    纯度低＝文字压在图片/照片/头像上，不是气泡正文，要丢掉。
    """
    xs = [p[0] for p in box]
    ys = [p[1] for p in box]
    reg = chat[int(min(ys)):int(max(ys)), int(min(xs)):int(max(xs))]
    if reg.size == 0:
        return None, 0.0
    reg = reg.astype(np.int16)
    vals, cnt = np.unique(reg.reshape(-1, 3), axis=0, return_counts=True)
    return tuple(int(v) for v in vals[cnt.argmax()]), float(cnt.max() / (reg.shape[0] * reg.shape[1]))


def learn_side_colors(chat, ocr_rows, pane_bg, min_unif=0.45):
    """用**位置**学出"我方/对方气泡各是什么颜色"。

    为什么不能只统计"聊天区里最常见的有彩色"：实测在微信 PC 上踩了大坑 ——
    微信转账卡片和头像的橙色 (253,206,157) 像素比绿色气泡还多，于是橙色被当成
    "我方颜色"，真正的绿色气泡（我的消息）全被判成对方，分边**整个反了**。

    换成：我方气泡靠右、对方靠左（微信/企业微信/QQ 都是这样），
    按几何把框分两堆，各取最多的底色 —— 学出来的颜色再用颜色去复核。

    Returns:
        (my_color, other_color, detail)  detail 是给用户核对的统计
    """
    from collections import Counter
    w = chat.shape[1]
    right, left = Counter(), Counter()
    for box, _text, _score in ocr_rows:
        bg, unif = box_fill(chat, box)
        if bg is None or unif < min_unif:
            continue                                    # 图内文字/头像
        if sum(abs(a - b) for a, b in zip(bg, pane_bg)) <= 6:
            continue                                    # 面板底色＝这行没气泡
        xs = [p[0] for p in box]
        cx = (min(xs) + max(xs)) / 2.0
        if cx > w * 0.55:
            right[bg] += 1
        elif cx < w * 0.45:
            left[bg] += 1
    my = right.most_common(1)[0][0] if right else None
    other = left.most_common(1)[0][0] if left else None
    detail = {
        "右半边(我方候选)": right.most_common(3),
        "左半边(对方候选)": left.most_common(3),
    }
    return my, other, detail


def selfcheck(profile, hwnd):
    """按量出来的参数裁一遍 + OCR 一遍，把我方/对方分好打出来。"""
    from rapidocr_onnxruntime import RapidOCR
    full = grab_window(hwnd)
    arr = np.asarray(full.convert("RGB"))
    L = profile["layout"]
    x0, x1 = L["chat_x"], L["chat_x"] + L["chat_w"]
    y0 = L["chat_top_margin_px"]
    y1 = arr.shape[0] - L["chat_bottom_margin_px"]
    chat = arr[y0:y1, x0:x1]
    eng = RapidOCR(intra_op_num_threads=4, det_limit_type="max", det_limit_side_len=4000)
    res, _ = eng(chat, use_cls=False)
    my = np.array(profile["bubbles"]["my_color_rgb"], dtype=np.int16)
    tol = profile["bubbles"]["my_color_tolerance"]

    rows = []
    for box, text, _ in sorted(res or [], key=lambda r: r[0][0][1]):
        bgr, unif = box_fill(chat, box)
        if bgr is None:
            continue
        bgr = np.array(bgr, dtype=np.int16)
        if unif < 0.45:
            side = "image"
        elif np.abs(bgr - my).sum() <= tol:
            side = "me"
        else:
            xs = [p[0] for p in box]
            reg = chat[int(min(p[1] for p in box)):int(max(p[1] for p in box)),
                       int(min(xs)):int(max(xs))].astype(np.int16)
            g = reg @ np.array([0.299, 0.587, 0.114])
            gb = float(bgr @ np.array([0.299, 0.587, 0.114]))
            side = "other" if float(np.abs(g - gb).max()) >= 150 else "gray"
        rows.append((side, tuple(int(x) for x in bgr), text))
    return chat.shape, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True, help="profile 显示名，如 企业微信 / 微信PC")
    ap.add_argument("--id", dest="pid", default="",
                    help="profile 文件名（ASCII），如 wecom / wechat_pc；默认用 --name")
    ap.add_argument("--process", default="", help="进程名，如 WXWork.exe / Weixin.exe")
    ap.add_argument("--class", dest="cls", default="", help="窗口类名（可选）")
    ap.add_argument("--title", default="",
                    help="主窗口标题（微信 PC 传「微信」，用来排除工具窗/看图窗）")
    ap.add_argument("--pick", action="store_true", help="用鼠标位置拾取窗口（把鼠标停在目标窗口上）")
    ap.add_argument("--no-write", action="store_true", help="只打印，不写 profiles/")
    ap.add_argument("--check", action="store_true",
                    help="校验模式：读取已有的 profiles/<id>.json，实测比对是否失效（不写文件）")
    args = ap.parse_args()

    if args.pick:
        input("把鼠标移到目标聊天软件窗口上，然后回车…")
        hwnd = pick_window()
    else:
        exe = (args.process or "").lower()
        hwnd = None
        if exe:
            from wxbot.native_win import _u32
            found = []
            import ctypes as _c

            @_c.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            def cb(h, _):
                if not _u32.IsWindowVisible(h):
                    return True
                from wxbot.native_win import _exe_of_pid
                pid = wintypes.DWORD()
                _u32.GetWindowThreadProcessId(h, _c.byref(pid))
                if _exe_of_pid(pid.value) == exe:
                    clsb = _c.create_unicode_buffer(256)
                    _u32.GetClassNameW(h, clsb, 256)
                    if not args.cls or clsb.value == args.cls:
                        found.append(int(h))
                return True

            _u32.EnumWindows(cb, 0)
            if args.title and found:
                keep = [h for h in found if window_info(h)[2] == args.title]
                if keep:
                    found = keep
                else:
                    print("⚠ 没找到标题为 %r 的窗口，候选标题：%s"
                          % (args.title, [window_info(h)[2] for h in found]))
            hwnd = found[0] if found else None
        if not hwnd:
            hwnd = find_main_hwnd()
    if not hwnd:
        sys.exit("没找到窗口。用 --process 指定进程名，或 --pick 手动指。")

    exe, cls, title = window_info(hwnd)
    rect = window_rect(hwnd)
    print(f"窗口: hwnd={hwnd} 进程={exe} 类={cls} 标题={title!r} rect={rect}")

    # 抓图质量：jev 记过「微信是 GPU 合成窗口，PrintWindow 容易黑屏」。
    # 黑帧会让 OCR 一个字都读不到、扫描"正常地什么都不干"，所以先看一眼。
    probe_img = grab_window(hwnd, check_blank=False)
    if probe_img is None:
        sys.exit("✗ PrintWindow 抓窗口失败（返回 None）")
    import numpy as _np
    _arr = _np.asarray(probe_img.convert("RGB"))
    _max, _std = int(_arr.max()), float(_arr.std())
    print(f"抓图质量: 尺寸={probe_img.size} 最亮={_max} 标准差={_std:.1f}")
    if _max < 12:
        print("✗ 整张黑 —— 这个窗口是 GPU 合成自绘窗口，PrintWindow 抓不到。")
        print("  微信 PC 4.x 很可能就是这个情况（jev 实测风险），需改装 Windows Graphics Capture：")
        print("    .venv\\Scripts\\python.exe -m pip install windows-capture")
        print("  在此之前无法标定。")
        return 2
    if _std < 2:
        print("⚠ 画面几乎纯色 —— 窗口可能没渲染完或已最小化，确认界面正常显示后再跑。")

    # ── 校验模式：拿已有 profile 跟实测比，看有没有因为软件改版而失效 ──
    if args.check:
        pid = args.pid or args.name
        path = os.path.join(PROFILES_DIR, f"{pid}.json")
        if not os.path.isfile(path):
            sys.exit(f"没有 {path}，先跑一次标定（去掉 --check）。")
        with open(path, encoding="utf-8") as f:
            prof = json.load(f)
        full = grab_window(hwnd)
        if full is None:
            sys.exit("PrintWindow 抓窗口失败")
        arr = np.asarray(full.convert("RGB"))
        m = measure(arr)
        if not m:
            sys.exit("✗ 像素锚点认不出聊天面板 —— 软件界面可能改版了，需要重新标定。")
        L = prof.get("layout", {})
        print(f"\n校验 {path}")
        bad = []
        for key, tol in (("chat_x", 12), ("chat_w", 40),
                         ("chat_top_margin_px", 25), ("chat_bottom_margin_px", 25)):
            want, got = L.get(key), m.get(key)
            if want is None:
                continue
            ok = abs(int(want) - int(got)) <= tol
            print(f"    {key:24} profile={want:<6} 实测={got:<6} "
                  f"{'✓' if ok else '✗ 偏差 ' + str(abs(int(want)-int(got)))}")
            if not ok:
                bad.append(key)
        okw = (prof.get("window", {}).get("window_class") == cls)
        print(f"    {'window_class':24} profile={prof.get('window',{}).get('window_class')!r:<6} "
              f"实测={cls!r} {'✓' if okw else '✗'}")
        if not okw:
            bad.append("window_class")
        shape, rows = selfcheck(prof, hwnd)
        cnt = {}
        for side, _, _ in rows:
            cnt[side] = cnt.get(side, 0) + 1
        print(f"    分边自检: {cnt}（me 应为我方、other 为对方、gray 为时间戳/系统提示）")
        if bad:
            print(f"\n✗ profile 已失效：{bad} —— 重新标定："
                  f"python scripts/calibrate_chat_app.py --name {prof.get('name')} --id {pid}")
            return 1
        print("\n✓ profile 仍有效（区域与窗口类都没漂）")
        return 0

    full = grab_window(hwnd)
    if full is None:
        sys.exit("PrintWindow 抓窗口失败")
    arr = np.asarray(full.convert("RGB"))
    m = measure(arr)
    if not m:
        sys.exit("像素锚点认不出聊天面板（窗口太小/布局没铺好）。")
    print(f"\n量出来的区域：")
    for k, v in m.items():
        print(f"    {k:24} = {v}")

    x0, x1 = m["chat_x"], m["chat_x"] + m["chat_w"]
    y0 = m["chat_top_margin_px"]
    y1 = arr.shape[0] - m["chat_bottom_margin_px"]
    chat = arr[y0:y1, x0:x1]
    # 用**位置**学颜色（我方靠右），不再用"哪个颜色出现最多" ——
    # 微信上转账卡片/头像的橙色比绿色气泡还多，那样会学反。
    from rapidocr_onnxruntime import RapidOCR as _Rapid
    _eng = _Rapid(intra_op_num_threads=4, det_limit_type="max", det_limit_side_len=4000)
    _res, _ = _eng(chat, use_cls=False)
    my_color, other_color, detail = learn_side_colors(chat, _res or [], m["bg"])
    print(f"\n学到的气泡颜色（按左右位置学）：")
    print(f"    我方（靠右）  = {my_color}")
    print(f"    对方（靠左）  = {other_color}")
    for k, v in detail.items():
        print(f"    {k}: {[(tuple(c), n) for c, n in v]}")
    if my_color and other_color:
        d = sum(abs(a - b) for a, b in zip(my_color, other_color))
        print(f"    两者曼哈顿距离 = {d}"
              f"{'  ← 太近，靠颜色可能分不开，需要靠位置' if d < 40 else ''}")

    profile = {
        "_comment": "由 scripts/calibrate_chat_app.py 自动标定；手改后请重跑自检",
        "name": args.name,
        "mode": "screenshot",
        "window": {
            "process_names": [exe] if exe else [],
            "window_class": cls,
            "min_title_length": max(1, len(title) - 1),
            "title_hint": title,
            "prefer_title": args.title or title,
        },
        "layout": {
            "list_x": m["list_x"],
            "list_w": m["list_w"],
            "chat_x": m["chat_x"],
            "chat_w": m["chat_w"],
            "chat_top_margin_px": m["chat_top_margin_px"],
            "chat_bottom_margin_px": m["chat_bottom_margin_px"],
        },
        "bubbles": {
            "my_side_rule": "color",
            "my_color_rgb": my_color or [0, 0, 0],
            # 容差必须小：企微对方气泡 (228,231,235) 与我方 (201,231,255) 的
            # 曼哈顿距离只有 47，容差给 60 会把对方的话全判成我方（实测踩过）。
            "my_color_tolerance": 20,
            "min_uniformity": 0.45,
            "min_contrast": 150,
        },
        "calibrated": {
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "window_size": [m["window_w"], m["window_h"]],
            "selfcheck": "见标定时终端输出",
        },
    }

    print("\n自检（按上面参数裁一遍并分边）：")
    shape, rows = selfcheck(profile, hwnd)
    print(f"    聊天区裁块={shape[1]}x{shape[0]}  识别 {len(rows)} 框")

    # ── 别在空白屏上写出垃圾 profile ──────────────────────────────────
    # 实测踩到：微信 PC 没打开任何聊天时，右半边是纯白（暗像素 0），
    # 标定器照样量出一堆数字并写盘 —— 那种 profile 用起来就是静默读错区域。
    if my_color is None or not rows:
        print("\n✗ 聊天区里没有内容，拒绝写 profile。")
        if my_color is None:
            print("   没找到我方气泡的主题色 —— 说明右边没有'我发出的消息'。")
        if not rows:
            print("   聊天区一个字都没识别到 —— 说明右边是空白页。")
        print("  → 请先在该软件的窗口里打开一个**有聊天记录的会话**（最好包含你自己发过的消息），")
        print("    确认右边能看到气泡文字，再重新运行本命令。")
        return 3
    cnt = {}
    for side, bg, text in rows[:24]:
        cnt[side] = cnt.get(side, 0) + 1
        print(f"    {side:6} bg={str(bg):16} {text[:44]!r}")
    print(f"    汇总（前24框）: {cnt}")

    out = os.path.join(PROFILES_DIR, f"{args.pid or args.name}.json")
    if not args.no_write:
        os.makedirs(PROFILES_DIR, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(profile, f, ensure_ascii=False, indent=2)
        print(f"\n已写入: {out}")
    print("\n人工确认要点：")
    print("  1. 上面 me 的那几条，是不是你自己发的？")
    print("  2. other 的几条，是不是客户/对方发的？")
    print("  3. gray 里是不是时间戳/系统提示（应该被忽略）？")
    print("  如果 1/2 混了，改 profile 里的 my_color_rgb / my_color_tolerance 再跑 selfcheck。")


if __name__ == "__main__":
    main()
