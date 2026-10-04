"""窗口尺寸：要够宽装得下内容，而且要**跟着缩放缓动**而不是写死像素。

2026-10-04 更新：原来这里是 grep 字面量 `geometry(720x300)` 并断言 >=700。
改成"最小宽度用 px(760) 表达 + 按内容自适应"之后字面量没了 —— 但意图更强了：
* 最小宽度 >= 700 设计像素（px(760) 保证）
* 且不能再写死：高 DPI（125%/150%）下字号会放大，写死宽度会把「知识库/设置」裁掉
  （打包版实测过）
"""

from pathlib import Path

SOURCE = Path("gui/main_window.py").read_text(encoding="utf-8")


def test_window_min_width_at_least_700():
    assert "px(760)" in SOURCE or "px(700)" in SOURCE, \
        "最小宽度要有 700 设计像素以上"
    assert "px(320)" in SOURCE, "初始高度也要过缩放"


def test_window_fits_content_instead_of_hardcoding():
    """窗口有**紧凑上限**，且窄了要能换行重排（而不是把按钮裁掉）。"""
    assert "_fit_window" in SOURCE
    block = SOURCE[SOURCE.index("def _fit_window"):]
    end = block.find("\n    def ", 10)
    block = block[:end] if end != -1 else block
    assert "px(820)" in block, "要有紧凑上限（以前按内容无限撑开，会占掉半个屏幕）"
    assert "screen_w" in block, "还要受屏幕宽度约束"
    # 窄窗口时按钮换行，而不是被裁
    assert "_reflow_header" in SOURCE
    assert "grid_configure" in SOURCE, "换行要用 grid 挪行列（pack 做不到）"
    assert "minsize" in SOURCE, "要设最小尺寸，否则能拖到布局散架"
