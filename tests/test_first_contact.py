# tests/test_first_contact.py
"""截图模式的"新客户先招呼一句"（③ 的近似版）。

API 模式能收到 enter_session 事件（客户刚进会话、还没说话）；截图模式看不到这个动作
—— 客户不发言，屏幕上没有任何变化。所以退一步：**第一次要跟这个客户说话之前，
先招呼一句**。两边共用一份"招呼过"记录，API 模式欢迎过的人不会再被招呼第二次。
"""

import pytest
from pathlib import Path

from wxbot import first_contact


@pytest.fixture
def store(tmp_path, monkeypatch):
    p = tmp_path / "greeted.json"
    monkeypatch.setattr(first_contact, "GREETED_PATH", p)
    return p


# ── 记录本身 ──────────────────────────────────────────────────────────

def test_not_greeted_initially(store):
    assert first_contact.is_greeted("客户A@微信") is False


def test_mark_then_greeted(store):
    first_contact.mark_greeted("客户A@微信")
    assert first_contact.is_greeted("客户A@微信") is True


def test_survives_restart(store):
    first_contact.mark_greeted("客户A@微信")
    assert store.is_file()
    # 新进程再读（这里就是重新读文件）
    assert first_contact.is_greeted("客户A@微信") is True


def test_name_variants_are_same_person(store):
    """截图模式的会话名会变（@微信 读不出、后面粘预览）—— 不能因此再招呼一次。"""
    first_contact.mark_greeted("客户A@微信")
    assert first_contact.is_greeted("客户A@微信 老板你多大了") is True
    assert first_contact.is_greeted("客户A") is True


def test_different_people_separate(store):
    first_contact.mark_greeted("客户A@微信")
    assert first_contact.is_greeted("客户D@微信") is False


def test_mark_is_idempotent(store):
    first_contact.mark_greeted("客户A@微信")
    first_contact.mark_greeted("客户A@微信")
    first_contact.mark_greeted("客户A")
    assert first_contact.count() == 1


def test_empty_name_ignored(store):
    first_contact.mark_greeted("")
    assert first_contact.is_greeted("") is False
    assert first_contact.count() == 0


def test_corrupt_file_ignored(store):
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text("{坏掉的", encoding="utf-8")
    assert first_contact.is_greeted("谁") is False
    first_contact.mark_greeted("谁")       # 还得能写回去
    assert first_contact.is_greeted("谁") is True


def test_reset(store):
    first_contact.mark_greeted("A")
    first_contact.reset()
    assert first_contact.count() == 0


# ── 真行为：第一次回复前先招呼 ────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clean_tried():
    """_GREET_TRIED 是进程级的（同一进程内不重复试），测试之间要清干净。"""
    import main as main_mod
    main_mod._GREET_TRIED.clear()
    yield
    main_mod._GREET_TRIED.clear()


class _Sender:
    def __init__(self, ok=True):
        self.calls = []
        self.ok = ok

    def __call__(self, name, text):
        self.calls.append((name, text))
        return self.ok


def test_greet_sends_welcome_first(store):
    import main as main_mod
    s = _Sender()
    assert main_mod.greet_first_contact("客户A@微信", {}, s) is True
    assert len(s.calls) == 1
    assert s.calls[0][0] == "客户A@微信"
    assert "在的" in s.calls[0][1]          # 出厂欢迎语
    assert first_contact.is_greeted("客户A@微信") is True


def test_greet_only_once(store):
    """第二次不再招呼（同一份记录，重启也在）。"""
    import main as main_mod
    s = _Sender()
    main_mod.greet_first_contact("客户A@微信", {}, s)
    assert main_mod.greet_first_contact("客户A@微信", {}, s) is False
    assert len(s.calls) == 1


def test_greet_once_across_process(store):
    """换个进程（清掉 _GREET_TRIED）也还记得招呼过 —— 记录是落盘的。"""
    import main as main_mod
    s = _Sender()
    main_mod.greet_first_contact("客户A@微信", {}, s)
    main_mod._GREET_TRIED.clear()
    assert main_mod.greet_first_contact("客户A@微信", {}, s) is False
    assert len(s.calls) == 1


def test_greet_different_people(store):
    import main as main_mod
    s = _Sender()
    main_mod.greet_first_contact("客户A@微信", {}, s)
    main_mod.greet_first_contact("客户D@微信", {}, s)
    assert len(s.calls) == 2


def test_greet_respects_switch(store):
    import main as main_mod
    s = _Sender()
    assert main_mod.greet_first_contact(
        "客户A@微信", {"welcome": {"on_first_contact": False}}, s) is False
    assert s.calls == []


def test_greet_failure_still_returns_true_but_not_marked(store):
    """招呼没发出去：正文照发（返回 True），但不算招呼过，也**不再重试**。"""
    import main as main_mod
    s = _Sender(ok=False)
    assert main_mod.greet_first_contact("客户A@微信", {}, s) is True
    assert first_contact.is_greeted("客户A@微信") is False
    assert main_mod.greet_first_contact("客户A@微信", {}, s) is False
    assert len(s.calls) == 1, "本进程内不再重试，免得刷屏"


def test_greet_send_exception_does_not_raise(store):
    """发送函数抛异常也不能把正文卡住。"""
    import main as main_mod

    def boom(name, text):
        raise RuntimeError("发送炸了")

    assert main_mod.greet_first_contact("客户A@微信", {}, boom) is True


def test_greet_logs_send_result(store):
    import main as main_mod
    logged = []
    main_mod.greet_first_contact(
        "客户A@微信", {}, _Sender(),
        log_send=lambda n, t, ok: logged.append((n, ok)))
    assert logged == [("客户A@微信", True)]


def test_greet_skipped_when_welcome_text_empty(store, monkeypatch):
    """欢迎语被清空 → 不招呼，也不记（以后填回来了还能招呼）。"""
    import main as main_mod
    import rag.prompt_store as ps
    monkeypatch.setattr(ps, "get", lambda k: "" if k == "welcome" else "x")
    s = _Sender()
    assert main_mod.greet_first_contact("客户A@微信", {}, s) is False
    assert s.calls == []
    assert first_contact.is_greeted("客户A@微信") is False


def test_empty_customer_name(store):
    import main as main_mod
    assert main_mod.greet_first_contact("", {}, _Sender()) is False


# ── 接线 ──────────────────────────────────────────────────────────────

def _main_src():
    return (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")


def test_body_requeued_for_next_tick():
    """正文必须放回队列下一轮发 —— 一次 tick 只发一条，别卡界面两倍时间。"""
    s = _main_src()
    block = s[s.index("if greet_first_contact(customer_name, cfg, _do_send"):
              s.index("send_ok = _do_send(customer_name, reply_text)")]
    assert "send_queue.put(row)" in block
    assert "root.after(500, _process_send_queue_tick)" in block
    assert "return" in block


def test_greet_before_send_in_tick():
    s = _main_src()
    i_greet = s.index("if greet_first_contact(customer_name, cfg, _do_send")
    i_send = s.index("send_ok = _do_send(customer_name, reply_text)")
    assert i_greet < i_send


def test_api_welcome_marks_greeted():
    """API 模式欢迎过的人，第一次回复时不能再招呼一遍。"""
    s = _main_src()
    i = s.index("api_channel.send_welcome")
    seg = s[i:i + 800]
    assert "first_contact.mark_greeted(uid)" in seg


def test_config_has_welcome_block():
    import json
    cfg = json.loads((Path(__file__).resolve().parent.parent
                      / "config.json").read_text(encoding="utf-8"))
    w = cfg.get("welcome", {})
    assert w.get("on_first_contact") is True
    assert "on_enter_session" in w
    assert "cooldown_seconds" in w
