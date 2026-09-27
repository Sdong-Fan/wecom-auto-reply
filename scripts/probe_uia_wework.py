# -*- coding: utf-8 -*-
"""企业微信（WXWork.exe）UIA 探针 —— 回答唯一那个问题：

    企业微信 Windows 客户端的聊天界面，能不能用 UI Automation 读到文字？

结论决定采集层走哪条路：
  路线 A：UIA 读节点（毫秒级、零 OCR、不抢焦点、不点击）→ 采集层可以整个换掉
  路线 B：只能截屏 + 本地 OCR（帧停稳、像素锚点、颜色分边）

参考 jev-chat-windows 的 probe/probe_win2.py。只读，绝不写：
不点击、不输入、不 Invoke、不自动发送、不碰任何按钮。

用法:
    python scripts/probe_uia_wework.py                  # 全量探测
    python scripts/probe_uia_wework.py --keyword 缺保安   # 顺带搜关键词
    python scripts/probe_uia_wework.py --hint            # 树空时怎么办
"""
from __future__ import annotations

import argparse
import ctypes
import io
import json
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TARGET_EXES = {"wxwork.exe"}          # 企业微信主进程
RELATED_EXES = {"wxworkweb.exe"}      # 内嵌浏览器（部分面板）
MAX_DEPTH = 40
NODE_CAP = 20000


def exe_of_pid(pid: int) -> str:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_uint(1024)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        k32.CloseHandle(h)


def enum_child_hwnds(hwnd: int):
    """递归列子窗口（Win32 HWND 层），看聊天区是不是独立的渲染子窗口 —— 采集层选句柄要用。
    用 GetWindow(GW_CHILD/GW_HWNDNEXT) 逐层走：EnumChildWindows 会把全部后代都吐一遍，
    递归用它会重复。"""
    u32 = ctypes.windll.user32
    GW_CHILD, GW_HWNDNEXT = 5, 2
    out = []

    def _rect(h):
        try:
            import ctypes.wintypes as wt
            rr = wt.RECT()
            u32.GetWindowRect(h, ctypes.byref(rr))
            return [rr.left, rr.top, rr.right, rr.bottom]
        except Exception:
            return None

    def _walk(parent, depth):
        if depth > 6:
            return
        h = u32.GetWindow(parent, GW_CHILD)
        while h:
            buf = ctypes.create_unicode_buffer(256)
            u32.GetClassNameW(h, buf, 256)
            title = ctypes.create_unicode_buffer(256)
            u32.GetWindowTextW(h, title, 256)
            out.append({"depth": depth, "hwnd": int(h), "class": buf.value,
                        "title": title.value, "rect": _rect(h),
                        "visible": bool(u32.IsWindowVisible(h))})
            _walk(h, depth + 1)
            h = u32.GetWindow(h, GW_HWNDNEXT)

    _walk(hwnd, 1)
    return out


def safe(fn, default=None):
    """每项独立取值，一项失败不影响其他项。"""
    try:
        v = fn()
        return v if v is not None else default
    except Exception:
        return default


def node_text(c):
    """四路独立取文字：UIA Name / ValuePattern / LegacyIAccessible Name / Value。"""
    name = safe(lambda: (c.Name or "").strip(), "")
    val = ""
    vp = safe(lambda: c.GetValuePattern(), None)
    if vp is not None:
        val = safe(lambda: (vp.Value or "").strip(), "")
    lname = lval = ""
    lp = safe(lambda: c.GetLegacyIAccessiblePattern(), None)
    if lp is not None:
        lname = safe(lambda: (lp.Name or "").strip(), "")
        lval = safe(lambda: (lp.Value or "").strip(), "")
    return name, val, lname, lval


def rect_of(c):
    r = safe(lambda: c.BoundingRectangle, None)
    if r is None:
        return None
    try:
        return [int(r.left), int(r.top), int(r.right), int(r.bottom)]
    except Exception:
        return None


def walk(c, depth, stats, nodes, max_depth=MAX_DEPTH, cap=NODE_CAP):
    if stats["total"] >= cap:
        return
    stats["total"] += 1
    stats["by_depth"][depth] = stats["by_depth"].get(depth, 0) + 1

    name, val, lname, lval = node_text(c)
    best = name or val or lname or lval
    ct = safe(lambda: c.ControlTypeName, "?")
    cls = safe(lambda: c.ClassName, "?")
    rid = safe(lambda: getattr(c, "AutomationId", ""), "")
    editable = safe(lambda: bool(c.IsEnabled) and c.ControlType in (50004,), False)  # 50004=Edit
    stats["by_type"][ct] = stats["by_type"].get(ct, 0) + 1
    if best:
        stats["with_text"] += 1
        nodes.append({
            "depth": depth,
            "control_type": ct,
            "class": cls,
            "automation_id": rid,
            "text": best[:200],
            "rect": rect_of(c),
            "editable": editable,
        })
    if depth >= max_depth:
        return
    for ch in safe(lambda: c.GetChildren(), []) or []:
        walk(ch, depth + 1, stats, nodes, max_depth, cap)


def walk_all(c, depth, out, max_depth=MAX_DEPTH, cap=NODE_CAP):
    """全量节点（含无文字的），带缩进结构 —— 看树长什么样、每块在屏幕哪个位置。"""
    if len(out) >= cap:
        return
    name, val, lname, lval = node_text(c)
    out.append({
        "depth": depth,
        "control_type": safe(lambda: c.ControlTypeName, "?"),
        "class": safe(lambda: c.ClassName, "?"),
        "text": (name or val or lname or lval)[:120],
        "rect": rect_of(c),
    })
    if depth >= max_depth:
        return
    for ch in safe(lambda: c.GetChildren(), []) or []:
        walk_all(ch, depth + 1, out, max_depth, cap)


def has_cn(s: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in s)


def new_stats():
    return {"total": 0, "with_text": 0, "by_depth": {}, "by_type": {}}


def probe_window(w, exe: str, keyword: str, dump_dir: str, index: int, show_tree: bool = False):
    title = safe(lambda: w.Name, "")
    cls = safe(lambda: w.ClassName, "")
    hwnd = safe(lambda: w.NativeWindowHandle, 0)
    print(f"\n{'=' * 72}")
    print(f"[{index}] {exe}  窗口标题={title!r}  class={cls}  hwnd={hwnd}")

    t0 = time.perf_counter()
    stats = new_stats()
    nodes = []
    walk(w, 0, stats, nodes)
    ms = int((time.perf_counter() - t0) * 1000)

    cn = [n for n in nodes if has_cn(n["text"])]
    print(f"    节点总数={stats['total']}  有文字={stats['with_text']}  含中文={len(cn)}  遍历耗时={ms}ms")
    print(f"    每层节点数: {dict(sorted(stats['by_depth'].items()))}")
    print(f"    控件类型: {dict(sorted(stats['by_type'].items(), key=lambda kv: -kv[1])[:8])}")

    verdict = ""
    if stats["total"] <= 1:
        verdict = "空树：只有窗口本身，没有子节点 → 无障碍未激活/未实现"
        print(f"    >>> {verdict}")
    elif not cn:
        verdict = "有树无中文文字：桥半通，节点在但文字被挡"
        print(f"    >>> {verdict}")
    else:
        verdict = f"有文字：含中文节点 {len(cn)} 个"
        print(f"    >>> {verdict}")
        print("    --- 前 30 条含中文文字 ---")
        for n in cn[:30]:
            print(f"      d{n['depth']:<2} {n['control_type']:18} {n['class'][:22]:22} "
                  f"{n['text'][:50]!r}")

    if keyword:
        hits = [n for n in nodes if keyword in n["text"]]
        print(f"    --- 关键词 {keyword!r} 命中 {len(hits)} 处 ---")
        for h in hits[:10]:
            print(f"      d{h['depth']:<2} {h['control_type']:18} {h['text'][:60]!r}  rect={h['rect']}")

    if show_tree:
        allnodes = []
        walk_all(w, 0, allnodes)
        print(f"    --- 全量节点树（{len(allnodes)} 个，含无文字节点）---")
        for n in allnodes:
            print(f"      {'  ' * n['depth']}d{n['depth']:<2} {n['control_type']:17} "
                  f"{n['class'][:20]:20} rect={str(n['rect']):26} {n['text'][:50]!r}")

    os.makedirs(dump_dir, exist_ok=True)
    path = os.path.join(dump_dir, f"uia_probe_{index}_{time.strftime('%Y%m%d_%H%M%S')}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"exe": exe, "title": title, "class": cls, "hwnd": hwnd,
                   "stats": stats, "verdict": verdict, "nodes_with_text": nodes},
                  f, ensure_ascii=False, indent=1)
    print(f"    原始 dump: {path}")
    return {"exe": exe, "title": title, "hwnd": hwnd, "stats": stats, "verdict": verdict}


HINT = """
--- 树是空的时候，按顺序排查（都不改企业微信、不注入）---

1) 开讲述人强制唤醒无障碍树（Qt/自绘 UI 是懒加载，见到读屏器才填充树）：
       Win + Ctrl + Enter        # 开讲述人
   然后【重新跑一遍本探针】，看节点数变没变。跑完再按一次关掉。

2) 用微软官方工具交叉验证，排除是我代码的问题：
   Accessibility Insights for Windows（免费）https://accessibilityinsights.io/
   或 Windows SDK 的 inspect.exe（UIA）与 MSAA 的 accChecker。
   鼠标悬到【聊天气泡】上，看 Inspect 能不能读出文字。
   Inspect 读得到 = UIA 有戏，是我代码问题；也读不到 = 企业微信没实现无障碍。

3) 若 1、2 都空 → 企业微信客户端没暴露控件树，UIA 路线到此为止，
   只剩截屏 + 本地 OCR。（注入无障碍插件属于 hook，违反非侵入约束，不做。）
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keyword", default="", help="顺带在树里搜这个关键词（如客户名或消息片段）")
    ap.add_argument("--hint", action="store_true", help="只打印树空时的排查步骤")
    ap.add_argument("--include-web", action="store_true", help="同时探测 WXWorkWeb.exe 窗口")
    ap.add_argument("--tree", action="store_true", help="打印全量节点树（含无文字节点，带 rect）")
    ap.add_argument("--hwnds", action="store_true", help="只列 Win32 子窗口（采集层选句柄用）")
    args = ap.parse_args()
    if args.hint:
        print(HINT)
        return

    try:
        import uiautomation as auto
    except ImportError:
        sys.exit("缺依赖：pip install uiautomation  （装到项目 venv 里）")

    want = set(TARGET_EXES) | (RELATED_EXES if args.include_web else set())

    print("枚举顶层窗口 …")
    root = auto.GetRootControl()
    wins = []
    for w in safe(lambda: root.GetChildren(), []) or []:
        exe = exe_of_pid(safe(lambda: w.ProcessId, 0))
        if exe in want:
            wins.append((w, exe))

    if not wins:
        sys.exit(f"没找到 {sorted(want)} 的顶层窗口。企业微信开着吗？")

    if args.hwnds:
        for w, exe in wins:
            hwnd = safe(lambda: w.NativeWindowHandle, 0)
            if not hwnd:
                continue
            print(f"\n{'=' * 72}\n{exe} hwnd={hwnd} 的 Win32 子窗口：")
            for h in enum_child_hwnds(hwnd):
                print(f"    d{h['depth']} hwnd={h['hwnd']:<10} vis={int(h['visible'])} "
                      f"{h['class'][:34]:34} rect={str(h['rect']):28} {h['title'][:24]!r}")
        return

    dump_dir = os.path.join(HERE, "logs", "uia_probe")
    results = [probe_window(w, exe, args.keyword, dump_dir, i + 1, args.tree)
               for i, (w, exe) in enumerate(wins)]

    print(f"\n{'=' * 72}")
    print("汇总：")
    for r in results:
        print(f"  {r['title']!r:28} 节点={r['stats']['total']:>6}  有文字={r['stats']['with_text']:>6}  {r['verdict']}")
    print("\n判定路线：任一窗口出现「含中文节点 > 0 且能看到聊天气泡文字」→ 路线 A（UIA）；"
          "全部空树/无文字 → 路线 B（截屏 + 本地 OCR）。")
    print("被打回时跑：python scripts/probe_uia_wework.py --hint")


if __name__ == "__main__":
    main()
