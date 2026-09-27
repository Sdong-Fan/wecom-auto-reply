# tests/test_wecom_api_features.py
"""API 模式的新功能：图片/语音不乱丢、进入会话发欢迎语、发送失败重试。

全部跑在**假企业微信服务端**上（`scripts/mock_wecom_server.py`）：
真发 HTTP、真读游标、真发消息，只是服务器是本机的。
所以这些测试验的是"整条链路"，不是打桩。真凭据的连通性要等
`scripts/wecom_api_selftest.py`（那一步必须有 Secret）。
"""

import time

import pytest

from gateway.wecom_kf import (NON_RETRYABLE_ERRCODES, ORIGIN_CUSTOMER,
                              ORIGIN_SYSTEM, WeComKFClient, WeComKFError)
from scripts.mock_wecom_server import MockWeCom


@pytest.fixture
def srv():
    s = MockWeCom(script=[]).start()
    yield s
    s.stop()


def _client(srv, tmp_path):
    c = WeComKFClient(corp_id=srv.corp_id, secret=srv.secret,
                      open_kfid=srv.open_kfid, base=srv.base_url,
                      state_path=str(tmp_path / "state.json"))
    return c


# ── ① 图片 / 语音不再丢 ───────────────────────────────────────────────

def test_image_message_reaches_client(srv, tmp_path):
    srv.script = [{"external_userid": "wmAAA", "msgtype": "image",
                   "media_id": "IMG1"}]
    c = _client(srv, tmp_path)
    msgs = c.sync_msg()
    assert len(msgs) == 1
    assert msgs[0].msgtype == "image"
    assert msgs[0].kind_label == "图片"
    assert msgs[0].display_text == "（客户发来图片）"
    assert msgs[0].is_text is False


def test_voice_with_speech_text_usable(srv, tmp_path):
    """语音能转文字时，直接当文本用（客户体验最好）。"""
    srv.script = [{"external_userid": "wmAAA", "msgtype": "voice",
                   "voice_text": "你们有索尼A7M4吗"}]
    c = _client(srv, tmp_path)
    msgs = c.sync_msg()
    assert msgs[0].text == "你们有索尼A7M4吗"
    assert msgs[0].from_voice is True


def test_voice_without_speech_text_still_arrives(srv, tmp_path):
    srv.script = [{"external_userid": "wmAAA", "msgtype": "voice"}]
    c = _client(srv, tmp_path)
    msgs = c.sync_msg()
    assert len(msgs) == 1 and msgs[0].text == ""
    assert msgs[0].display_text == "（客户发来语音）"


def test_mixed_batch_order_preserved(srv, tmp_path):
    srv.script = [
        {"external_userid": "wmAAA", "msgtype": "text", "text": "在吗"},
        {"external_userid": "wmAAA", "msgtype": "image"},
        {"external_userid": "wmAAA", "msgtype": "voice", "voice_text": "多少钱"},
    ]
    c = _client(srv, tmp_path)
    assert [m.msgtype for m in c.sync_msg()] == ["text", "image", "voice"]


# ── ③ 进入会话事件 ────────────────────────────────────────────────────

def test_enter_session_event_carries_code(srv, tmp_path):
    srv.script = [{"external_userid": "wmAAA", "msgtype": "event",
                   "code": "ABC123"}]
    c = _client(srv, tmp_path)
    msgs = c.sync_msg()
    assert msgs[0].is_event is True
    assert msgs[0].event_type == "enter_session"
    assert msgs[0].event_code == "ABC123"
    assert msgs[0].display_text == "（客户进入会话）"


def test_send_welcome_hits_the_right_endpoint(srv, tmp_path):
    c = _client(srv, tmp_path)
    c.send_welcome("ABC123", "在的～想租什么机器？")
    got = srv.welcome_texts()
    assert got == [{"code": "ABC123", "text": "在的～想租什么机器？"}]


def test_welcome_not_in_send_msg(srv, tmp_path):
    """欢迎语必须走 send_msg_on_event（要 code），不能走普通发消息。"""
    c = _client(srv, tmp_path)
    c.send_welcome("CODE", "欢迎")
    assert srv.sent == []
    assert len(srv.events_sent) == 1


# ── ⑤ 发送失败重试 ────────────────────────────────────────────────────

def test_send_retries_then_succeeds(srv, tmp_path):
    """接口偶发"系统繁忙"时重试，客户才不会白等。"""
    srv.fail_send_times = 2
    c = _client(srv, tmp_path)
    c.send_text("wmAAA", "有货的", retries=3)
    assert [s["text"] for s in srv.sent_texts()] == ["有货的"]
    assert srv._send_attempts == 3


def test_send_gives_up_after_retries(srv, tmp_path):
    srv.fail_send_times = 99
    c = _client(srv, tmp_path)
    with pytest.raises(WeComKFError) as e:
        c.send_text("wmAAA", "有货的", retries=2)
    assert srv._send_attempts == 2
    assert e.value.errcode == -1


def test_non_retryable_error_fails_fast(srv, tmp_path):
    """参数/权限错了就立刻放弃 —— 重试只是白等（还可能触发频率限制）。"""
    srv.fail_send_times = 99
    srv.fail_send_errcode = 40003          # invalid external_userid
    assert 40003 in NON_RETRYABLE_ERRCODES
    c = _client(srv, tmp_path)
    with pytest.raises(WeComKFError) as e:
        c.send_text("wmBAD", "有货的", retries=3)
    assert srv._send_attempts == 1, "不可重试的错误只打一次"
    assert e.value.errcode == 40003


def test_send_default_retries_is_three(srv, tmp_path):
    srv.fail_send_times = 2
    c = _client(srv, tmp_path)
    c.send_text("wmAAA", "你好")            # 不传 retries 也要能成功
    assert len(srv.sent_texts()) == 1


def test_cursor_advances_only_after_fetch(srv, tmp_path):
    """游标语义：拿到就推进（已知取舍，见 docs）。至少别在失败时推进。"""
    srv.script = [{"external_userid": "wmAAA", "text": "第一条"},
                  {"external_userid": "wmAAA", "text": "第二条"}]
    c = _client(srv, tmp_path)
    assert [m.text for m in c.sync_msg()] == ["第一条", "第二条"]
    assert c.sync_msg() == []              # 再拉没有了


def test_voice_with_text_routes_as_question(srv, tmp_path):
    """转写出文字的语音要按**普通问题**处理，不能打成"听不清，麻烦打字"。

    分流看的是 has_text，不是 msgtype —— 否则 voice_format=1 白设。
    """
    srv.script = [{"external_userid": "wmAAA", "msgtype": "voice",
                   "voice_text": "你们几点下班"}]
    c = _client(srv, tmp_path)
    m = c.sync_msg()[0]
    assert m.is_text is False        # 类型还是语音
    assert m.has_text is True        # 但文字能用 → 走问答
    assert m.from_voice is True


def test_image_has_no_text(srv, tmp_path):
    srv.script = [{"external_userid": "wmAAA", "msgtype": "image"}]
    m = _client(srv, tmp_path).sync_msg()[0]
    assert m.has_text is False


def test_blank_text_has_no_text(srv, tmp_path):
    srv.script = [{"external_userid": "wmAAA", "msgtype": "text", "text": "   "}]
    assert _client(srv, tmp_path).sync_msg() == []


# ── 非文本消息的处置策略（纯逻辑，可测）───────────────────────────────

class _Msg:
    def __init__(self, msgid, kind):
        self.msgid = msgid
        self.kind_label = kind


class _FakeQueue:
    def __init__(self):
        self.sent = []
        self.pending = []

    def put(self, item):
        self.sent.append(item)

    def push(self, customer, msg, ai_reply, conf, guard, key_hint=""):
        self.pending.append({"customer": customer, "msg": msg,
                             "key_hint": key_hint})


def test_pick_nontext_ack_by_kind():
    from gateway.api_policy import pick_nontext_ack
    acks = {"图片": "看图的话我看看哈", "其它": "麻烦打字说下"}
    assert pick_nontext_ack(["图片"], acks) == "看图的话我看看哈"
    assert pick_nontext_ack(["位置"], acks) == "麻烦打字说下"
    assert pick_nontext_ack([], acks) == "麻烦打字说下"
    assert pick_nontext_ack(["图片"], {}) == ""


@pytest.mark.asyncio
async def test_nontext_dispatch_acks_and_escalates_once():
    """连发 3 张图：只回一句、只进一条待人工（不能刷屏）。"""
    from gateway.api_policy import ACTION_ESCALATE, dispatch_api_nontext
    q = _FakeQueue()
    items = [_Msg("m1", "图片"), _Msg("m2", "图片"), _Msg("m3", "图片")]
    out = await dispatch_api_nontext("wmAAA", items, q, q,
                                     acks={"图片": "图片收到啦", "其它": "打字说下"})
    assert out.action == ACTION_ESCALATE
    assert len(q.sent) == 1 and q.sent[0] == ("send", "wmAAA", "图片收到啦")
    assert len(q.pending) == 1
    assert q.pending[0]["msg"] == "（客户发来图片，请到微信客服后台查看）"
    assert q.pending[0]["key_hint"] == "nontext:m1"


@pytest.mark.asyncio
async def test_nontext_dispatch_mixed_kinds():
    """图片 + 语音一起来：只说一次，但把两种都写清楚。"""
    from gateway.api_policy import dispatch_api_nontext
    q = _FakeQueue()
    items = [_Msg("a1", "图片"), _Msg("a2", "语音")]
    await dispatch_api_nontext("wmAAA", items, q, q,
                               acks={"图片": "看图", "语音": "听不清", "其它": "打字"})
    assert q.sent[0][2] == "看图"          # 用第一个类型的话
    assert "图片、语音" in q.pending[0]["msg"]


@pytest.mark.asyncio
async def test_nontext_dispatch_no_ack_configured():
    """应答文案被清空也不能不发转人工。"""
    from gateway.api_policy import dispatch_api_nontext
    q = _FakeQueue()
    await dispatch_api_nontext("wmAAA", [_Msg("x", "图片")], q, q, acks={})
    assert q.sent == []
    assert len(q.pending) == 1


def test_nontext_rows_do_not_collapse():
    """两张图文本一样，但 key_hint 不同 → 待人工里是两行。"""
    from rag.human_fallback import PendingQueue
    import tempfile
    from pathlib import Path
    q = PendingQueue(persist_path=str(Path(tempfile.mkdtemp()) / "pq.json"))
    q.push("wmAAA", "（客户发来图片）", "", 0.0, "x", key_hint="nontext:m1")
    q.push("wmAAA", "（客户发来图片）", "", 0.0, "x", key_hint="nontext:m2")
    assert len(q.get_all()) == 2
    # 同一条重复入队（重试）仍然只一行
    q.push("wmAAA", "（客户发来图片）", "", 0.0, "x", key_hint="nontext:m2")
    assert len(q.get_all()) == 2


# ── 欢迎语该不该发 ────────────────────────────────────────────────────

def test_should_welcome_first_time():
    from gateway.api_policy import should_welcome
    assert should_welcome("wmA", {}, 3600, code="C1", now=1000.0) is True


def test_should_welcome_respects_cooldown():
    """客户来回切会话会反复触发 enter_session —— 不能每次都招呼。"""
    from gateway.api_policy import should_welcome
    seen = {"wmA": 1000.0}
    assert should_welcome("wmA", seen, 3600, code="C2", now=1500.0) is False
    assert should_welcome("wmA", seen, 3600, code="C2", now=5000.0) is True


def test_should_welcome_per_customer():
    from gateway.api_policy import should_welcome
    seen = {"wmA": 1000.0}
    assert should_welcome("wmB", seen, 3600, code="C", now=1100.0) is True


def test_should_welcome_needs_code_and_switch():
    from gateway.api_policy import should_welcome
    assert should_welcome("wmA", {}, 3600, code="", now=1.0) is False
    assert should_welcome("wmA", {}, 3600, enabled=False, code="C", now=1.0) is False


# ── 接线 ──────────────────────────────────────────────────────────────

def _main_src():
    from pathlib import Path
    return (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")


def test_main_wires_nontext_and_welcome():
    s = _main_src()
    assert "dispatch_api_nontext(" in s
    assert "should_welcome(" in s
    assert "api_channel.send_welcome" in s
    # 非文本要攒成一批再处理：连发多张只回一次
    assert "nontext.setdefault(m.external_userid, []).append(m)" in s
    assert "for uid, items in nontext.items():" in s


def test_main_sends_welcome_on_event():
    s = _main_src()
    assert "if m.is_event:" in s
    assert "_handle_enter_session(m)" in s
