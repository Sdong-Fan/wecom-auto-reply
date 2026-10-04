# tests/test_ui_scale.py
"""界面缩放：窗口变大变小，框与字号跟着等比缩放（而不是写死像素）。

背景：第一版把图高、面板高、列宽、字号全写成固定像素，于是
* 高 DPI（125%/150%）下「近 7 天/自定义」直接被裁掉；
* 窗口拉大之后中间一坨空白，字还是那么小。
现在所有尺寸都过 theme.px()/font()，由 theme.set_scale() 统一控制。
"""

import tkinter as tk
from pathlib import Path

import pytest

from gui import theme


@pytest.fixture(autouse=True)
def _reset_scale():
    yield
    theme.set_scale(1.0)


def test_px_scales_with_factor():
    theme.set_scale(1.0)
    assert theme.px(100) == 100
    theme.set_scale(1.5)
    assert theme.px(100) == 150
    theme.set_scale(0.9)
    assert theme.px(100) == 90


def test_scale_is_clamped():
    """缩放要夹在合理区间：太小字看不清，太大一屏放不下。"""
    assert theme.set_scale(0.1) == theme.MIN_SCALE
    assert theme.set_scale(9.9) == theme.MAX_SCALE


def test_font_size_scales():
    theme.set_scale(1.0)
    assert theme.font("body")[1] == theme.SIZES["body"]
    theme.set_scale(1.5)
    assert theme.font("body")[1] == int(round(theme.SIZES["body"] * 1.5))
    # 再小也不能小于 8pt（否则中文糊成一团）
    theme.set_scale(0.8)
    assert theme.font("micro")[1] >= 8


def test_detect_dpi_scale_bounds():
    root = tk.Tk()
    root.withdraw()
    try:
        s = theme.detect_dpi_scale(root)
        assert theme.MIN_SCALE <= s <= theme.MAX_SCALE
    finally:
        root.destroy()


def test_apply_theme_sets_scale():
    root = tk.Tk()
    root.withdraw()
    try:
        theme.apply_theme(root, 1.25)
        assert theme.scale() == 1.25
        style = theme.apply_theme(root, 1.0)          # 可以重复调用
        assert theme.scale() == 1.0
        assert style.theme_use() == "clam", "必须切 clam，否则颜色配置不生效"
    finally:
        root.destroy()


def test_dashboard_layout_has_no_hardcoded_toplevel_sizes():
    """看板里不该再出现写死的窗口/控件尺寸（列的宽度走 px() 或按比例算）。"""
    src = (Path(__file__).resolve().parent.parent / "gui/dashboard.py").read_text(
        encoding="utf-8")
    assert 'geometry("1220x860")' not in src, "窗口尺寸不能写死"
    assert "height=132" not in src, "图表高度要过 px()"
    assert "height=330)" not in src or "px(330)" in src, "面板高度要过 px()"
    # 缩放后必须重建内容区（tk 控件不会自动改字号）
    assert "_rebuild_body" in src
