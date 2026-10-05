# tests/test_pending_edit_layout.py
"""「待人工 → 编辑」窗口的布局 —— 按钮**永远不能被挤没**。

店主反馈："编辑的框太小了，下面的两个按钮都被吞了"。

根因（已复现）：
`_handle_pending_edit` 里的窗口**把按钮条最后 pack**。Tk 按 pack 顺序切空间，
窗口高度不够时最后那个只剩 1px —— 等于消失。而它同时又是**硬编码 480x280、
完全不走 `px()` 缩放**，字体却跟着全局缩放走（现场 1.5 倍）→ 内容需要的高度
超过窗口高度，被牺牲的正是按钮。

实测对照（窗口故意只给 180 高）：
    老写法（按钮最后 pack）      按钮条高 = 1   ← 被吞
    修好（按钮先 pack side=BOTTOM）按钮条高 = 43  ← 编辑框让位，可滚动

所以这里钉两条：
  · 默认尺寸下，编辑框和按钮**都**要看得见；
  · 窗口被压得很小时，**按钮必须还在**（让编辑框去缩）。
"""

import tkinter as tk

import pytest

from gui.pending_edit import PendingEditor


@pytest.fixture
def root():
    tk = pytest.importorskip("tkinter")
    try:
        r = tk.Tk()
    except Exception as e:
        pytest.skip("没有可用的显示环境: %s" % e)
    r.withdraw()
    from gui.theme import apply_theme
    apply_theme(r, 1.0)
    yield r
    try:
        r.destroy()
    except Exception:
        pass


def _editor(root, **kw):
    ed = PendingEditor(root, kw.pop("customer", "客户A"),
                       kw.pop("question", "我想修改收货地址"),
                       kw.pop("current", "好的，这边帮您看下～"))
    # 要拿到**真实的分配高度**必须让窗口真的映射出来（没映射时 winfo_height 恒为 1）。
    # 但父窗口是 withdrawn 的，而 transient 窗口会被父窗口压住 → 先解开 transient。
    # 位置挪到屏幕外，所以测试期间不会闪窗。
    ed.win.transient("")
    ed.win.geometry("+5000+5000")
    ed.win.deiconify()
    ed.win.update()
    return ed


def test_buttons_are_visible_at_default_size(root):
    ed = _editor(root)
    ed.win.update()
    assert ed._btn_frame.winfo_height() > 1, "默认尺寸下按钮条就不见了"
    assert ed.text_box.winfo_height() > 1, "默认尺寸下编辑框就不见了"
    ed.win.destroy()


def test_buttons_survive_a_too_short_window(root):
    """★ 核心回归：窗口被压到装不下时，**按钮必须还在**（编辑框让位）。"""
    ed = _editor(root)
    ed.win.geometry("480x150")          # 故意不够高
    ed.win.update()
    assert ed._btn_frame.winfo_height() > 1, (
        "窗口不够高时按钮被挤没了 —— 这正是店主看到的'两个按钮被吞了'")
    ed.win.destroy()


def test_buttons_are_packed_before_the_text_box(root):
    """结构性保证：按钮条先 pack、且 side=BOTTOM。

    只靠"测出来的高度"不够稳（跟字体/DPI 有关）；顺序才是根因，
    所以直接钉住顺序 —— 以后有人把按钮挪到编辑框后面，这条会红。
    """
    ed = _editor(root)
    ed.win.update_idletasks()
    order = ed.win.pack_slaves()
    btn_idx = order.index(ed._btn_frame)
    box_idx = order.index(ed.text_box)
    assert btn_idx < box_idx, "按钮条必须排在编辑框**前面** pack"
    assert ed._btn_frame.pack_info().get("side") == "bottom"
    ed.win.destroy()


def test_confirm_returns_edited_text(root):
    ed = _editor(root, current="原始草稿")
    ed.text_box.delete("1.0", tk.END)
    ed.text_box.insert("1.0", "  改好的回复  ")
    ed._confirm()
    assert ed.result == "改好的回复", "结果要去掉首尾空白"


def test_cancel_returns_empty(root):
    ed = _editor(root)
    ed.win.destroy()
    assert ed.wait() == ""


def test_empty_draft_shows_explanation(root):
    """没有草稿时要说明白"这题资料里没有"，别让用户以为坏了。"""
    from gui.pending_edit import EMPTY_HINT
    ed = _editor(root, current="")
    ed.win.update()
    texts = []

    def walk(w):
        for c in w.winfo_children():
            try:
                texts.append(str(c.cget("text")))
            except Exception:
                pass
            walk(c)

    walk(ed.win)
    assert any("资料库里找不到依据" in t for t in texts), texts
    assert "资料库里找不到依据" in EMPTY_HINT
    ed.win.destroy()


def test_long_question_does_not_blow_up_the_width(root):
    """客户消息很长时要换行，不能把窗口横向撑爆（撑爆就被裁掉，看不见）。"""
    ed = _editor(root, question="我想问一下" + "这个问题很长" * 30)
    ed.win.update_idletasks()
    assert ed.win.winfo_reqwidth() <= 1200, "长消息把窗口宽度撑爆了"
    ed.win.destroy()
