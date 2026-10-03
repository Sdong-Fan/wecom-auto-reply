# tests/test_window_pick.py
"""目标窗口选择：不许把机器人自己的界面当成企业微信。

现场事故（2026-10-03）：发送路径用 pyautogui.getWindowsWithTitle("企业微信") 取 [0]，
而机器人自己的 GUI 标题是「企业微信智能客服」——也含"企业微信"，且 z-order 在最前，
于是回复被粘进自己界面，企业微信里一条都没发出去：客户侧表现为"程序在跑但没反应"。
"""

from wxbot.scanner import pick_target_window

MY_PID = 74568
WECOM_PID = 12345
CLASS = "WeWorkWindow"


def test_picks_wecom_not_own_gui():
    """真实窗口列表（现场抓的）：[0] 是自己，[1] 才是企业微信。"""
    cands = [
        (30868142, "企业微信智能客服", MY_PID, "TkTopLevel"),
        (526648, "企业微信", WECOM_PID, CLASS),
        (33557142, "企业微信客服开发 — DeepSeek Harness - Google Chrome",
         999, "Chrome_WidgetWin_1"),
    ]
    assert pick_target_window(cands, MY_PID, CLASS) == 526648


def test_class_match_wins_over_z_order():
    """标题命中但窗口类不对的排在前面，也要挑类名匹配的那个。"""
    cands = [
        (1, "企业微信 - 浏览器", 111, "Chrome_WidgetWin_1"),
        (2, "企业微信智能客服", MY_PID, "TkTopLevel"),
        (3, "企业微信", WECOM_PID, CLASS),
    ]
    assert pick_target_window(cands, MY_PID, CLASS) == 3


def test_only_own_windows_returns_zero():
    """只剩自己的窗口 = 没找到，返回 0（调用方会跳过操作，不会乱粘）。"""
    cands = [
        (1, "企业微信智能客服", MY_PID, "TkTopLevel"),
        (2, "企业微信智能客服", MY_PID, "TkTopLevel"),
    ]
    assert pick_target_window(cands, MY_PID, CLASS) == 0


def test_no_candidates_returns_zero():
    assert pick_target_window([], MY_PID, CLASS) == 0


def test_falls_back_when_class_unknown():
    """别的软件（类名对不上）也能按标题兜底，但不能是自己。"""
    cands = [
        (1, "微信智能客服", MY_PID, "TkTopLevel"),
        (2, "微信", 222, "WeChatMainWndForPC"),
    ]
    assert pick_target_window(cands, MY_PID, CLASS) == 2
