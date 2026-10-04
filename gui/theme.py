# gui/theme.py
"""设计系统（单一事实来源）：颜色 / 字号 / 间距 / 圆角 + ttk 样式 + 常用控件。

**设计参考**：`VoltAgent/awesome-design-md` 里的 Stripe 与 Linear 两套规范。
* 底色与正文取自 **Stripe**：白画布 `#ffffff`、冷白底 `#f6f9fc`、深海军蓝正文
  `#0d253d`（**不用纯黑**）、发丝线 `#e3e8ee`、靛蓝主色 `#533afd`。
  选浅色是因为这个软件是店主白天在店里用的，浅色更友好，表格与数字也更清楚
  （Stripe 那套规范自己写着"dashboard 才翻成深色"，我们不做深色）。
* 结构与密度取自 **Linear**：surface 分层（画布 → 卡片）、发丝线分隔、紧凑行高、
  6–8px 圆角、正文用中等字号而不是大字号。
* 间距严格走 **8px 栅格**（2/4/8/12/16/24/32），不出现 5px、7px、13px 这种随手值。

**tkinter 的先天限制与我们的近似做法**（这条很关键，不然会照着 web 规范写出四不像）：
* 没有圆角、没有阴影、没有渐变 → 圆角用 Canvas 自绘（`rounded_rect`），
  阴影用"发丝线边框 + 略深的底色"代替（`card()`）；
* 字重只有 normal/bold（没有 300/500）→ 层级靠**字号 + 颜色**做，不靠字重；
* 中文字体在 Windows 上固定用「微软雅黑」，不要用 Inter（中文会掉回宋体，很丑）；
* ttk 默认的 `vista` 主题**不响应大部分颜色配置** → 统一切到 `clam` 再逐类上色。

用法：
    from gui.theme import apply_theme, card, section_title, kpi_card, COLORS
    apply_theme(root)            # 在创建窗口之后、创建控件之前调一次
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# ── 颜色（取自 Stripe 规范，另按需要补了浅底与语义色）────────────────────────
COLORS = {
    # 表面
    "canvas": "#ffffff",          # 卡片/内容底
    "canvas_soft": "#f6f9fc",     # 页面底（比卡片略冷）
    "canvas_sunken": "#eef2f7",   # 更沉的底（用于表头、分区带）
    "hairline": "#e3e8ee",        # 1px 分隔线/边框
    "hairline_strong": "#cfd7df", # 需要更清楚的边（输入框）
    # 文字（深海军蓝系，不用纯黑）
    "ink": "#0d253d",
    "ink_secondary": "#273951",
    "ink_mute": "#64748d",
    "ink_faint": "#8a94a6",
    "on_primary": "#ffffff",
    # 品牌/强调：靛蓝只用于**主要动作**与强调，不拿来做正文色
    "primary": "#533afd",
    "primary_deep": "#4434d4",
    "primary_press": "#2e2b8c",
    "primary_soft": "#665efd",
    "primary_subtle": "#eef0fe",  # 浅底：选中行/hover/标签底色
    # 语义色（文字色都压暗过，保证在白底上够清楚）
    "success": "#0f7b46",
    "success_bg": "#e9f6ee",
    "warning": "#8a5a17",
    "warning_bg": "#fdf4e3",
    "danger": "#b3244a",
    "danger_bg": "#fdeef2",
    "info": "#1f5f9e",
    "info_bg": "#eaf2fb",
}

FONT_FAMILY = "微软雅黑"

# ── 字号（8px 栅格之外的唯一例外是字号，按"能否一眼分清层级"定）─────────────
# tkinter 只有 normal/bold，所以层级 = 字号 + 颜色，别指望字重
SIZES = {
    "kpi": 26,       # 大数字
    "h1": 17,        # 页面标题
    "h2": 14,        # 卡片标题
    "h3": 13,        # 小节标题
    "body": 12,      # 正文（Windows 100% 缩放下最耐看的中文大小）
    "small": 11,     # 辅助说明
    "micro": 10,     # 标签/角标
}


def font(size_key: str = "body", bold: bool = False):
    """按语义取字体元组。用 (family, size, style) —— tkinter 的标准写法。"""
    return (FONT_FAMILY, SIZES.get(size_key, SIZES["body"]),
            "bold" if bold else "normal")


# ── 间距（8px 栅格）────────────────────────────────────────────────────────
SPACE = {"xxs": 2, "xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32}

RADIUS = {"card": 8, "control": 6, "pill": 999}   # 自绘圆角用


# ═══ ttk 样式 ═════════════════════════════════════════════════════════════

def apply_theme(root: tk.Misc) -> ttk.Style:
    """给整个应用上色。**必须在创建控件之前调用**（之后创建的控件才会用上新样式）。

    返回 ttk.Style 方便调用方继续加样式。
    """
    style = ttk.Style(root)
    # ★ 必须切 clam：Windows 默认的 vista 主题把大部分颜色配置忽略掉，
    #   只改 foreground/background 是没反应的（这是 tkinter 上色最常见的坑）
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    c = COLORS
    style.configure(".", background=c["canvas_soft"], foreground=c["ink"],
                    font=font("body"))

    # ── 容器 ────────────────────────────────────────────────────────────
    style.configure("TFrame", background=c["canvas_soft"])
    style.configure("Card.TFrame", background=c["canvas"])
    style.configure("Soft.TFrame", background=c["canvas_soft"])
    style.configure("Sunken.TFrame", background=c["canvas_sunken"])
    style.configure("Header.TFrame", background=c["canvas"])
    style.configure("Divider.TFrame", background=c["hairline"])

    # ── 文本 ────────────────────────────────────────────────────────────
    style.configure("TLabel", background=c["canvas_soft"], foreground=c["ink"])
    style.configure("Card.TLabel", background=c["canvas"], foreground=c["ink"])
    style.configure("H1.TLabel", background=c["canvas"], foreground=c["ink"],
                    font=font("h1", True))
    style.configure("H2.TLabel", background=c["canvas"], foreground=c["ink"],
                    font=font("h2", True))
    style.configure("H3.TLabel", background=c["canvas"], foreground=c["ink_secondary"],
                    font=font("h3", True))
    style.configure("Body.TLabel", background=c["canvas"], foreground=c["ink_secondary"])
    style.configure("Mute.TLabel", background=c["canvas"], foreground=c["ink_mute"],
                    font=font("small"))
    style.configure("Micro.TLabel", background=c["canvas"], foreground=c["ink_faint"],
                    font=font("micro"))
    style.configure("Kpi.TLabel", background=c["canvas"], foreground=c["ink"],
                    font=font("kpi", True))
    style.configure("KpiUnit.TLabel", background=c["canvas"], foreground=c["ink_mute"],
                    font=font("small"))
    style.configure("Link.TLabel", background=c["canvas"], foreground=c["primary"],
                    font=font("small"))

    # ── 按钮：pill 形（靠 padding 做"胶囊"的观感，tkinter 无圆角）──────────
    _btn = dict(borderwidth=0, focusthickness=0, relief="flat", padding=(14, 7))
    style.configure("Primary.TButton", background=c["primary"], foreground=c["on_primary"],
                    font=font("body", True), **_btn)
    style.map("Primary.TButton",
              background=[("pressed", c["primary_press"]), ("active", c["primary_deep"]),
                          ("disabled", c["hairline_strong"])],
              foreground=[("disabled", c["ink_mute"])])
    style.configure("Secondary.TButton", background=c["canvas"], foreground=c["primary"],
                    font=font("body"), **_btn)
    style.map("Secondary.TButton",
              background=[("pressed", c["primary_subtle"]), ("active", c["primary_subtle"])],
              foreground=[("disabled", c["ink_faint"])])
    style.configure("Ghost.TButton", background=c["canvas_soft"], foreground=c["ink_secondary"],
                    font=font("body"), **_btn)
    style.map("Ghost.TButton", background=[("active", c["canvas_sunken"])])
    style.configure("Danger.TButton", background=c["canvas"], foreground=c["danger"],
                    font=font("body"), **_btn)
    style.map("Danger.TButton", background=[("active", c["danger_bg"])])

    # 分段控件（今日 / 近 7 天 / 自定义）—— 选中态要**一眼看得出来**：
    # clam 主题的按钮边框由 light/dark/bordercolor 三个色控制，
    # 只设 background 会被边框盖住（第一版就吃了这个亏：选中态几乎看不出来）
    style.configure("Segment.TButton", background=c["canvas"], foreground=c["ink_mute"],
                    font=font("body"), borderwidth=1, relief="solid",
                    bordercolor=c["hairline"], lightcolor=c["canvas"],
                    darkcolor=c["canvas"], padding=(14, 6))
    style.map("Segment.TButton",
              background=[("active", c["primary_subtle"])],
              foreground=[("active", c["primary_deep"])])
    style.configure("SegmentOn.TButton", background=c["primary_subtle"],
                    foreground=c["primary_deep"], font=font("body", True),
                    borderwidth=1, relief="solid", bordercolor=c["primary_soft"],
                    lightcolor=c["primary_subtle"], darkcolor=c["primary_subtle"],
                    padding=(14, 6))
    style.map("SegmentOn.TButton",
              background=[("active", c["primary_subtle"])],
              foreground=[("active", c["primary_press"])])

    # ── 表格 ────────────────────────────────────────────────────────────
    style.configure("Treeview", background=c["canvas"], fieldbackground=c["canvas"],
                    foreground=c["ink"], rowheight=24, borderwidth=0,
                    font=font("body"))
    style.configure("Treeview.Heading", background=c["canvas_sunken"],
                    foreground=c["ink_mute"], font=font("small", True),
                    relief="flat", padding=(6, 5))
    style.map("Treeview.Heading", background=[("active", c["canvas_sunken"])])
    style.map("Treeview", background=[("selected", c["primary_subtle"])],
              foreground=[("selected", c["ink"])])

    # ── 输入 ────────────────────────────────────────────────────────────
    style.configure("TEntry", fieldbackground=c["canvas"], foreground=c["ink"],
                    bordercolor=c["hairline_strong"], lightcolor=c["hairline_strong"],
                    darkcolor=c["hairline_strong"], insertcolor=c["ink"], padding=4)
    style.configure("TCombobox", fieldbackground=c["canvas"], background=c["canvas"],
                    foreground=c["ink"], arrowcolor=c["ink_mute"], padding=3)
    style.configure("TCheckbutton", background=c["canvas"], foreground=c["ink_secondary"])
    style.configure("TRadiobutton", background=c["canvas"], foreground=c["ink_secondary"])

    # ── 滚动条：细一点，别抢视觉 ────────────────────────────────────────
    style.configure("Vertical.TScrollbar", background=c["canvas_sunken"],
                    troughcolor=c["canvas_soft"], bordercolor=c["canvas_soft"],
                    arrowcolor=c["ink_mute"], borderwidth=0, width=10)
    style.configure("Horizontal.TScrollbar", background=c["canvas_sunken"],
                    troughcolor=c["canvas_soft"], bordercolor=c["canvas_soft"],
                    arrowcolor=c["ink_mute"], borderwidth=0)

    # ── 标签页 ──────────────────────────────────────────────────────────
    style.configure("TNotebook", background=c["canvas_soft"], borderwidth=0)
    style.configure("TNotebook.Tab", background=c["canvas_sunken"],
                    foreground=c["ink_mute"], padding=(14, 7), font=font("body"))
    style.map("TNotebook.Tab",
              background=[("selected", c["canvas"])],
              foreground=[("selected", c["ink"])])

    return style


# ═══ 常用控件（自绘圆角 / 卡片 / 条形图）═══════════════════════════════════

def rounded_rect(canvas: tk.Canvas, x1, y1, x2, y2, r: int = RADIUS["card"], **kw):
    """在 Canvas 上画圆角矩形（tkinter 没有原生圆角，用平滑多边形近似）。

    做法：取 4 个圆角的点，用 `smooth=True` 让 Tk 自己插值成弧。
    """
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(pts, smooth=True, **kw)


def card(parent, *, padding: int = SPACE["lg"], bg: str | None = None) -> ttk.Frame:
    """白底卡片：发丝线边框 + 内边距。tkinter 没有阴影，用边框+底色差做层次。"""
    outer = tk.Frame(parent, background=COLORS["hairline"],
                     highlightthickness=0, bd=0)
    inner = tk.Frame(outer, background=bg or COLORS["canvas"],
                     highlightthickness=0, bd=0)
    inner.pack(fill="both", expand=True, padx=1, pady=1)   # 1px 边 = 发丝线
    inner._pad = padding  # type: ignore[attr-defined]
    return outer


def card_body(outer) -> tk.Frame:
    """取卡片里真正放内容的那层（配合 card() 用）。"""
    return outer.winfo_children()[0]


def section_title(parent, text: str, *, bg: str | None = None) -> tk.Label:
    """小节标题：小字大写感的"标签"，比正文更靠近说明文字。"""
    return tk.Label(parent, text=text, font=font("h3", True),
                    background=bg or COLORS["canvas"],
                    foreground=COLORS["ink"], anchor="w")


def hint(parent, text: str, *, bg: str | None = None) -> tk.Label:
    return tk.Label(parent, text=text, font=font("small"),
                    background=bg or COLORS["canvas"],
                    foreground=COLORS["ink_mute"], anchor="w")


def kpi_card(parent, label: str, value: str, *, unit: str = "",
             tone: str = "ink", note: str = "") -> tk.Frame:
    """KPI 卡：大数字 + 标签 + 可选单位/备注。返回卡片（用 card_body 拿内容层）。"""
    outer = card(parent)
    body = card_body(outer)
    tk.Label(body, text=label, font=font("small"), background=COLORS["canvas"],
             foreground=COLORS["ink_mute"], anchor="w").pack(
        anchor="w", padx=SPACE["lg"], pady=(SPACE["md"], 0))
    row = tk.Frame(body, background=COLORS["canvas"])
    row.pack(anchor="w", padx=SPACE["lg"], pady=(0, SPACE["md"]))
    tk.Label(row, text=value, font=font("kpi", True), background=COLORS["canvas"],
             foreground=COLORS.get(tone, COLORS["ink"])).pack(side="left")
    if unit:
        tk.Label(row, text=unit, font=font("small"), background=COLORS["canvas"],
                 foreground=COLORS["ink_mute"]).pack(side="left", padx=(3, 0), pady=(9, 0))
    if note:
        tk.Label(body, text=note, font=font("micro"), background=COLORS["canvas"],
                 foreground=COLORS["ink_faint"], anchor="w").pack(
            anchor="w", padx=SPACE["lg"], pady=(0, SPACE["md"]))
    return outer


def bar_row(parent, name: str, value: int, total: int, *, width: int = 108,
            color: str | None = None) -> tk.Frame:
    """分布条：名称 + 迷你条形 + 数值（看板的"客户在问什么"用它）。"""
    row = tk.Frame(parent, background=COLORS["canvas"])
    tk.Label(row, text=name, font=font("body"), background=COLORS["canvas"],
             foreground=COLORS["ink_secondary"], anchor="w",
             width=10).pack(side="left")
    cv = tk.Canvas(row, width=width, height=8, background=COLORS["canvas"],
                   highlightthickness=0, bd=0)
    cv.pack(side="left", padx=(SPACE["sm"], SPACE["sm"]))
    ratio = (value / total) if total else 0
    cv.create_rectangle(0, 0, width, 8, fill=COLORS["canvas_sunken"], outline="")
    if ratio > 0:
        cv.create_rectangle(0, 0, max(2, int(width * ratio)), 8,
                            fill=color or COLORS["primary_soft"], outline="")
    tk.Label(row, text=str(value), font=font("body", True),
             background=COLORS["canvas"], foreground=COLORS["ink"],
             anchor="e", width=4).pack(side="left")
    pct = f"{ratio * 100:.0f}%" if total else "—"
    tk.Label(row, text=pct, font=font("small"), background=COLORS["canvas"],
             foreground=COLORS["ink_faint"], anchor="e", width=4).pack(side="left")
    return row


def badge(parent, text: str, tone: str = "info") -> tk.Label:
    """小标签（如"实时"、护栏名）。tone: info/success/warning/danger/neutral"""
    bg = {"info": COLORS["info_bg"], "success": COLORS["success_bg"],
          "warning": COLORS["warning_bg"], "danger": COLORS["danger_bg"],
          "neutral": COLORS["canvas_sunken"]}.get(tone, COLORS["info_bg"])
    fg = {"info": COLORS["info"], "success": COLORS["success"],
          "warning": COLORS["warning"], "danger": COLORS["danger"],
          "neutral": COLORS["ink_mute"]}.get(tone, COLORS["info"])
    return tk.Label(parent, text=f" {text} ", font=font("micro"),
                    background=bg, foreground=fg)


def divider(parent, *, pady: int = SPACE["md"]) -> tk.Frame:
    """1px 发丝线分隔（Linear 的做法：靠线分层，不靠阴影）。"""
    line = tk.Frame(parent, height=1, background=COLORS["hairline"])
    line.pack(fill="x", pady=pady)
    return line
