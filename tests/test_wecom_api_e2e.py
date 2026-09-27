# -*- coding: utf-8 -*-
"""API 模式集成测试：用本地假企业微信服务器跑真实 HTTP（无凭据、无外网）。

覆盖两类东西：
  * 客户端 ↔ 服务端的真实往返（token / account_list / sync_msg 游标 / send_msg）
  * 收到消息后的分流策略（gateway/api_policy.py）：不回 / 直发 / 转人工 / 失败
"""
import asyncio
import os
import queue
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "scripts"))

from mock_wecom_server import MockWeCom           # noqa: E402
from gateway.wecom_kf import WeComKFClient        # noqa: E402
from gateway.api_policy import (ACTION_AUTO_SEND, ACTION_ESCALATE,  # noqa: E402
                                ACTION_FAILED, ACTION_NO_REPLY,
                                dispatch_api_message)


SCRIPT = [
    {"external_userid": "wmA", "text": "你们有富士 X-T5 吗"},
    {"external_userid": "wmB", "text": "谢谢"},
]


@pytest.fixture
def srv():
    s = MockWeCom(script=SCRIPT).start()
    yield s
    s.stop()


@pytest.fixture
def cli(srv, tmp_path):
    return WeComKFClient(corp_id="wwmock", secret="mocksecret", open_kfid="wkMOCK",
                         base=srv.base_url, state_path=str(tmp_path / "kf.json"))


# ── 客户端 ↔ 假服务器 ─────────────────────────────────────────────────

def test_credentials_wrong_are_reported(srv):
    c = WeComKFClient(corp_id="WRONG", secret="WRONG", base=srv.base_url,
                      state_path="/tmp/never.json")
    import gateway.wecom_kf as kf
    try:
        c.token()
        assert False, "应当抛错"
    except kf.WeComKFError as e:
        assert e.errcode == 40013


def test_selftest_ok_and_does_not_advance_cursor(cli, srv):
    """**回归**：自检绝不能吃掉第一条客户消息。

    sync_msg 会推进并落盘游标 —— 自检里直接调，第一条真实消息就被永久跳过了。
    端到端验证时真踩到过（MSG1 消失）。
    """
    ok, detail = cli.selftest()
    assert ok and "OK" in detail
    assert cli._cursor == "", "自检后游标必须还原"

    msgs = cli.sync_msg()
    assert [m.text for m in msgs] == ["你们有富士 X-T5 吗", "谢谢"], \
        "自检之后的第一次正式拉取必须拿到全部消息（含第一条）"


def test_full_round_trip(cli, srv):
    ok, _ = cli.selftest()
    assert ok
    msgs = cli.sync_msg()
    assert len(msgs) == 2
    assert msgs[0].customer_key == "wmA"

    r = cli.send_text("wmA", "日租 95 元")
    assert r["msgid"].startswith("SENT")
    assert srv.sent_texts() == [{"touser": "wmA", "text": "日租 95 元"}]


def test_cursor_prevents_duplicates(cli, srv):
    cli.sync_msg()
    assert cli.sync_msg() == [], "游标已推进，第二轮不该再拿到同样的消息"


def test_cursor_persists_across_restart(cli, srv):
    cli.sync_msg()
    c2 = WeComKFClient(corp_id="wwmock", secret="mocksecret", open_kfid="wkMOCK",
                       base=srv.base_url, state_path=cli.state_path)
    c2._load_state()
    assert c2._cursor == cli._cursor
    assert c2.sync_msg() == [], "重启后不能把老消息重放一遍"


def test_token_is_reused_across_calls(cli, srv):
    cli.token()
    cli.account_list()
    cli.sync_msg()
    token_calls = [c for c in srv.calls if c["path"] == "/cgi-bin/gettoken"]
    assert len(token_calls) == 1, "token 应当缓存，不能每个请求都换一次"


def test_nickname_lookup(cli, srv):
    cli.sync_msg()
    assert cli.display_name("wmA").startswith("客户")


def test_bad_token_is_retried_once(cli, srv):
    cli._access_token = "STALE"
    cli._token_expire_at = 9e18          # 假装还没过期
    assert cli.account_list()[0]["open_kfid"] == "wkMOCK"
    assert len([c for c in srv.calls if c["path"] == "/cgi-bin/gettoken"]) == 1, \
        "token 失效应当强制刷新一次"


# ── 分流策略 ──────────────────────────────────────────────────────────

class _Result:
    def __init__(self, **kw):
        self.success = kw.get("success", False)
        self.dispatch_level = kw.get("dispatch_level", "human_handle")
        self.reply_text = kw.get("reply_text", "")
        self.hold_text = kw.get("hold_text", "")
        self.reason = kw.get("reason", "")
        self.escalated = kw.get("escalated", False)
        self.retrieval_score = kw.get("retrieval_score", 0.0)
        self.guard_decision = kw.get("guard_decision", "")
        self.guard_reason = kw.get("guard_reason", "")


class _StubResponder:
    def __init__(self, result):
        self._result = result
        self.seen = []
        self.raise_exc = None

    async def handle_customer_message(self, name, messages):
        self.seen.append((name, messages))
        if self.raise_exc:
            raise self.raise_exc
        return self._result


class _Pending:
    def __init__(self):
        self.items = []

    def push(self, *a):
        self.items.append(a)


def _run(coro):
    return asyncio.run(coro)


def test_policy_no_reply_does_not_send_or_escalate():
    r = _StubResponder(_Result(dispatch_level="no_reply", reason="收尾语"))
    q, p = queue.Queue(), _Pending()
    out = _run(dispatch_api_message(r, "wmA", "谢谢", q, p))
    assert out.action == ACTION_NO_REPLY
    assert q.empty() and not p.items


def test_policy_auto_send_queues_reply():
    r = _StubResponder(_Result(success=True, dispatch_level="auto_send",
                               reply_text="日租 95 元", guard_reason="all_checks_passed"))
    q, p = queue.Queue(), _Pending()
    out = _run(dispatch_api_message(r, "wmA", "多少钱", q, p, display_name="张三"))
    assert out.action == ACTION_AUTO_SEND
    assert q.get_nowait() == ("send", "wmA", "日租 95 元")
    assert not p.items


def test_policy_escalate_sends_hold_and_pushes_pending():
    r = _StubResponder(_Result(escalated=True, dispatch_level="human_handle",
                               reply_text="不确定的草稿", hold_text="帮您问下，稍等。",
                               reason="置信度不足(0.48)", retrieval_score=0.48,
                               guard_decision="low_risk"))
    q, p = queue.Queue(), _Pending()
    refreshed = []
    out = _run(dispatch_api_message(r, "wmB", "缺保安吗", q, p,
                                    refresh_pending=lambda: refreshed.append(1)))
    assert out.action == ACTION_ESCALATE
    assert q.get_nowait() == ("send", "wmB", "帮您问下，稍等。")
    assert p.items[0][0] == "wmB" and p.items[0][1] == "缺保安吗"
    assert refreshed, "待人工页要刷新"


def test_policy_escalate_without_hold_text_sends_nothing():
    r = _StubResponder(_Result(escalated=True, hold_text="", reason="检索低"))
    q, p = queue.Queue(), _Pending()
    _run(dispatch_api_message(r, "wmB", "??", q, p))
    assert q.empty() and len(p.items) == 1


def test_policy_responder_exception_becomes_failed():
    r = _StubResponder(_Result())
    r.raise_exc = RuntimeError("Qdrant 挂了")
    q, p = queue.Queue(), _Pending()
    out = _run(dispatch_api_message(r, "wmA", "在吗", q, p))
    assert out.action == ACTION_FAILED and "Qdrant" in out.reason
    assert q.empty() and not p.items


def test_policy_uses_userid_as_customer_key():
    """回复路由必须用 external_userid，昵称只用于显示。"""
    r = _StubResponder(_Result(dispatch_level="no_reply"))
    q, p = queue.Queue(), _Pending()
    _run(dispatch_api_message(r, "wmABC123", "谢谢", q, p, display_name="张三"))
    assert r.seen == [("wmABC123", ["谢谢"])]


# ── main.py 接线 ──────────────────────────────────────────────────────

def test_main_delegates_to_shared_policy():
    src = (HERE / "main.py").read_text(encoding="utf-8")
    assert "from gateway.api_policy import dispatch_api_message" in src
    assert "await dispatch_api_message(" in src
