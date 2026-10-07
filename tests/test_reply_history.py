# tests/test_reply_history.py
"""「已回复」这一栏：软件**发出去的所有消息 + 时间**。

店主："为什么全部和已回复不会有消息" + "取消全部这一栏，只需要两个格子，
第一个格子为待人工（功能和原来一致），第二个为已回复，记录所有该软件发出去的消息和时间"。

两个原因（都修了）：
1. `self._records` **只在内存里**，重启就清空 —— 店主重启几次后打开就是空的。
   而 `logs/auto_replies.jsonl` 是落盘的、每次发送都写，之前完全没被用上。
2. "已回复"的过滤条件是 `action == "replied"`，**占位语/欢迎语不算** ——
   可客户收到的就是占位语，凭什么不算"软件发出去的消息"。

另外原来的「全部」和「已回复」功能重叠，按要求去掉了。
"""
import json
import tkinter as tk
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


# ── 1. 读历史（纯函数，不依赖界面）──────────────────────────────────

def _write_log(p: Path, rows):
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows),
                 encoding="utf-8")
    return p


def test_loads_sent_messages_with_time(tmp_path):
    from gui.main_window import load_reply_history
    p = _write_log(tmp_path / "log.jsonl", [
        {"timestamp": "2026-10-06T09:00:00", "customer_name": "客户A",
         "ai_reply": "在的在的，你说 😄", "dispatch_level": "auto_send"},
        {"timestamp": "2026-10-06T09:01:00", "customer_name": "客户B",
         "ai_reply": "帮您问下押金的事，稍等～", "dispatch_level": "auto_send"},
    ])
    rows = load_reply_history(p)
    assert len(rows) == 2
    assert rows[0].timestamp == "10-06 09:00", "时间要带日期（跨天分得清）"
    assert rows[0].message == "在的在的，你说 😄", "要显示**发出去的原文**"
    assert rows[0].customer == "客户A"


def test_skips_never_sent_and_no_reply(tmp_path):
    """没发出去的不算「已回复」：空回复、no_reply 都要跳过。"""
    from gui.main_window import load_reply_history
    p = _write_log(tmp_path / "log.jsonl", [
        {"timestamp": "2026-10-06T09:00:00", "customer_name": "A",
         "ai_reply": "", "dispatch_level": "auto_send"},
        {"timestamp": "2026-10-06T09:01:00", "customer_name": "B",
         "ai_reply": "无需回复", "dispatch_level": "no_reply"},
        {"timestamp": "2026-10-06T09:02:00", "customer_name": "C",
         "ai_reply": "真的发出去了", "dispatch_level": "auto_send"},
    ])
    rows = load_reply_history(p)
    assert [r.customer for r in rows] == ["C"]


def test_missing_file_is_empty_not_crash(tmp_path):
    from gui.main_window import load_reply_history
    assert load_reply_history(tmp_path / "nope.jsonl") == []


def test_broken_lines_are_skipped(tmp_path):
    """日志里混进半行/坏行也不能崩（它是追加写的，断电就会有个半行）。"""
    from gui.main_window import load_reply_history
    p = tmp_path / "log.jsonl"
    p.write_text('{"timestamp": "2026-10-06T09:00:00", "customer_name": "A", '
                 '"ai_reply": "好的", "dispatch_level": "auto_send"}\n'
                 '{"broken": \n'
                 'not json at all\n'
                 '\n',
                 encoding="utf-8")
    rows = load_reply_history(p)
    assert len(rows) == 1 and rows[0].message == "好的"


def test_history_is_chronological(tmp_path):
    """按时间先后返回（显示时再倒过来，跟实时追加的顺序保持一致）。"""
    from gui.main_window import load_reply_history
    p = _write_log(tmp_path / "log.jsonl", [
        {"timestamp": "2026-10-06T09:00:00", "customer_name": "早",
         "ai_reply": "第一条", "dispatch_level": "auto_send"},
        {"timestamp": "2026-10-06T10:00:00", "customer_name": "晚",
         "ai_reply": "第二条", "dispatch_level": "auto_send"},
    ])
    rows = load_reply_history(p)
    assert [r.message for r in rows] == ["第一条", "第二条"]


# ── 2. 「软件发出去」的语义：占位语/欢迎语也算 ──────────────────────

def test_outbound_set_includes_hold_and_greeting():
    """占位语、欢迎语都是真发出去的 —— 客户收到了，就得算「已回复」。"""
    from gui.main_window import OUTBOUND_ACTIONS
    for a in ("replied", "auto_send", "hold", "greeting"):
        assert a in OUTBOUND_ACTIONS, "%s 应当算'发出去的消息'" % a
    assert "no_reply" not in OUTBOUND_ACTIONS
    assert "escalated" not in OUTBOUND_ACTIONS, "转人工不是发出去的消息"


def test_source_no_longer_filters_by_literal_replied():
    """不许再写 `action == "replied"` 这种会漏掉占位语的过滤。"""
    src = (ROOT / "gui/main_window.py").read_text(encoding="utf-8")
    assert 'r.action == "replied"' not in src


# ── 3. 界面：就两个格子 ────────────────────────────────────────────

@pytest.fixture(scope="module")
def window():
    """整个文件**只建一个** MainWindow。

    为什么不用 function 级：同一进程里反复建/毁 Tk 根会撞上 tkinter 的老问题
    `Can't find a usable tk.tcl in the following directories` —— 实测 6 遍里有 3 遍
    随机跳过某个用例（不是偶尔，是一半概率）。建一次就稳了。
    （代价：下面的用例**有顺序依赖** —— 先验刚建好的初始状态，
      后面才改 `_records` / 切标签，所以那个初始状态用例排在前面。）
    """
    pytest.importorskip("tkinter")
    from gui.main_window import MainWindow
    try:
        w = MainWindow()
    except Exception as e:
        pytest.skip("没有可用的显示环境: %s" % e)
    w.root.withdraw()                       # 测试期间别弹窗
    yield w
    try:
        w.root.destroy()
    except Exception:
        pass


def test_startup_shows_pending_page_not_the_list(window):
    """默认标签是待人工 → 启动就该显示待人工那一页（不能高亮 A 却显示 B）。

    ★ 必须排在最前：它验的是"刚建好的初始状态"。
    """
    assert window._pending_frame.winfo_manager() == "pack", "默认应当显示待人工页"
    assert window._list_frame.winfo_manager() == "", "列表页这时应当收起"


def test_only_two_tabs_and_pending_is_default(window):
    """就两个格子：待人工（第一个、默认）+ 已回复。原来的「全部」没了。"""
    assert list(window._tab_buttons) == ["待人工", "已回复"]
    assert window._current_tab == "待人工"
    assert window._current_tab not in ("全部",)


def test_hold_message_appears_in_replied_tab(window):
    """★ 核心：占位语（客户真收到的那句）必须在「已回复」里看得到。"""
    from gui.main_window import MessageRecord
    window._records = [
        MessageRecord("10-06 01:00", "客户A", "帮您问下押金的事，稍等～",
                      "hold", "帮您问下押金的事，稍等～"),
    ]
    window._switch_tab("已回复")
    rows = window._tree.get_children()
    assert len(rows) == 1, "占位语也是软件发出去的消息，必须显示"
    vals = window._tree.item(rows[0], "values")
    assert "稍等" in vals[2], "「消息」列要显示发出去的原文"
    assert vals[0] == "10-06 01:00"


def test_newest_first(window):
    """流水账最新在上。"""
    from gui.main_window import MessageRecord
    window._records = [
        MessageRecord("10-06 01:00", "A", "先发的", "replied", "先发的"),
        MessageRecord("10-06 02:00", "B", "后发的", "replied", "后发的"),
    ]
    window._switch_tab("已回复")
    rows = window._tree.get_children()
    assert window._tree.item(rows[0], "values")[2] == "后发的"
