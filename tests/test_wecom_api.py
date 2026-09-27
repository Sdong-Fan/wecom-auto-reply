"""企业微信「微信客服」API 客户端测试（全部离线，mock 掉 HTTP）。

真机连通性靠 `python scripts/probe_wecom_api.py`（已验证 5/5 路径可达）与
`python scripts/wecom_api_selftest.py`（需要真实凭据）。
这里只测协议解析、token 缓存、游标推进、去重与错误处理。
"""
import json
from pathlib import Path

import pytest

from gateway.wecom_kf import (ORIGIN_CUSTOMER, ORIGIN_STAFF, IncomingMessage,
                              WeComKFClient, WeComKFError)


@pytest.fixture
def cli(tmp_path):
    c = WeComKFClient(corp_id="wwtest", secret="sectest", open_kfid="wkTEST",
                      state_path=str(tmp_path / "kf.json"))
    return c


def _responder(cli, monkeypatch, table):
    """把 cli._call 换成查表实现；table[path] = dict 或 Exception。"""
    calls = []

    def fake_call(path, body=None, params=None, raise_on_error=True):
        calls.append({"path": path, "body": body, "params": params})
        val = table.get(path)
        if val is None:
            raise AssertionError(f"未预期的请求: {path}")
        if isinstance(val, Exception):
            raise val
        return val

    monkeypatch.setattr(cli, "_call", fake_call)
    return calls


# ── token ─────────────────────────────────────────────────────────────

def test_token_is_cached(cli, monkeypatch):
    calls = _responder(cli, monkeypatch, {
        "/cgi-bin/gettoken": {"errcode": 0, "access_token": "T1", "expires_in": 7200},
    })
    assert cli.token() == "T1"
    assert cli.token() == "T1"          # 第二次不该再请求
    assert len(calls) == 1


def test_token_force_refreshes(cli, monkeypatch):
    calls = _responder(cli, monkeypatch, {
        "/cgi-bin/gettoken": {"errcode": 0, "access_token": "T2", "expires_in": 7200},
    })
    cli._access_token = "OLD"
    cli._token_expire_at = 9e18
    assert cli.token(force=True) == "T2"
    assert len(calls) == 1


def test_token_persisted_and_reloaded(cli, monkeypatch):
    _responder(cli, monkeypatch, {
        "/cgi-bin/gettoken": {"errcode": 0, "access_token": "T3", "expires_in": 7200},
    })
    cli.token()
    assert Path(cli.state_path).exists()

    c2 = WeComKFClient(corp_id="wwtest", secret="sectest",
                       state_path=cli.state_path)
    c2._load_state()
    assert c2._access_token == "T3"
    assert c2._token_expire_at > 0


def test_missing_credentials_gives_plain_message():
    c = WeComKFClient(corp_id="", secret="")
    with pytest.raises(WeComKFError) as e:
        c.token()
    assert "WECOM_CORP_ID" in str(e.value) or "凭据" in str(e.value) or "缺少" in str(e.value)


# ── 业务接口 ──────────────────────────────────────────────────────────

def test_sync_msg_keeps_customer_messages_drops_staff_and_blank(cli, monkeypatch):
    """客户的消息**全部**收（含图片），只丢我们自己发的和空文本。

    原来非文本整个丢掉 —— 客户发张器材照片问"这个有吗"，一个字都收不到，
    等于让人干等（这是 API 模式最大的洞）。
    """
    _responder(cli, monkeypatch, {
        "/cgi-bin/gettoken": {"errcode": 0, "access_token": "T", "expires_in": 7200},
        "/cgi-bin/kf/sync_msg": {
            "errcode": 0, "next_cursor": "CUR2", "has_more": 0,
            "msg_list": [
                {"msgid": "m1", "origin": ORIGIN_CUSTOMER, "msgtype": "text",
                 "external_userid": "wmAAA", "text": {"content": "有富士吗"}},
                {"msgid": "m2", "origin": ORIGIN_STAFF, "msgtype": "text",
                 "external_userid": "wmAAA", "text": {"content": "我们自己发的"}},
                {"msgid": "m3", "origin": ORIGIN_CUSTOMER, "msgtype": "image",
                 "external_userid": "wmAAA", "image": {"media_id": "x"}},
                {"msgid": "m4", "origin": ORIGIN_CUSTOMER, "msgtype": "text",
                 "external_userid": "wmBBB", "text": {"content": "   "}},
            ],
        },
    })
    msgs = cli.sync_msg()
    assert [m.msgid for m in msgs] == ["m1", "m3"]
    assert msgs[0].customer_key == "wmAAA"
    assert msgs[0].is_text is True
    # 图片：不是文本，但要能说出是什么类型，待人工列表才有话可说
    assert msgs[1].is_text is False
    assert msgs[1].kind_label == "图片"
    assert msgs[1].display_text == "（客户发来图片）"
    assert cli._cursor == "CUR2"


def test_sync_msg_requests_voice_to_text(cli, monkeypatch):
    """语音请服务端转文字 —— 转出来就能当文本正常回答。"""
    seen = {}
    _responder(cli, monkeypatch, {
        "/cgi-bin/gettoken": {"errcode": 0, "access_token": "T", "expires_in": 7200},
        "/cgi-bin/kf/sync_msg": {
            "errcode": 0, "next_cursor": "C1", "has_more": 0,
            "msg_list": [
                {"msgid": "v1", "origin": ORIGIN_CUSTOMER, "msgtype": "voice",
                 "external_userid": "wmAAA", "voice": {"text": "你们几点下班"}},
            ],
        },
    })
    msgs = cli.sync_msg()
    assert msgs[0].text == "你们几点下班"
    assert msgs[0].from_voice is True
    assert msgs[0].is_text is False          # 类型还是 voice，但文本能用


def test_sync_msg_voice_without_text_kept(cli, monkeypatch):
    """转写不出来也要收下来（走转人工），不能又变成"客户没消息"。"""
    _responder(cli, monkeypatch, {
        "/cgi-bin/gettoken": {"errcode": 0, "access_token": "T", "expires_in": 7200},
        "/cgi-bin/kf/sync_msg": {
            "errcode": 0, "next_cursor": "C1", "has_more": 0,
            "msg_list": [
                {"msgid": "v1", "origin": ORIGIN_CUSTOMER, "msgtype": "voice",
                 "external_userid": "wmAAA", "voice": {"media_id": "amr1"}},
            ],
        },
    })
    msgs = cli.sync_msg()
    assert len(msgs) == 1
    assert msgs[0].text == ""
    assert msgs[0].display_text == "（客户发来语音）"


def test_sync_msg_picks_up_enter_session_event(cli, monkeypatch):
    """客户进入会话是个事件 —— 发欢迎语全靠它。"""
    _responder(cli, monkeypatch, {
        "/cgi-bin/gettoken": {"errcode": 0, "access_token": "T", "expires_in": 7200},
        "/cgi-bin/kf/sync_msg": {
            "errcode": 0, "next_cursor": "C1", "has_more": 0,
            "msg_list": [
                {"msgid": "e1", "origin": 4, "msgtype": "event",
                 "external_userid": "wmAAA",
                 "event": {"event_type": "enter_session", "code": "CODE123"}},
                {"msgid": "e2", "origin": 4, "msgtype": "event",
                 "external_userid": "wmAAA",
                 "event": {"event_type": "session_status_change"}},
            ],
        },
    })
    msgs = cli.sync_msg()
    assert [m.msgid for m in msgs] == ["e1"]      # 只认"进入会话"
    assert msgs[0].is_event is True
    assert msgs[0].event_code == "CODE123"


def test_sync_msg_persists_cursor_across_instances(cli, monkeypatch):
    _responder(cli, monkeypatch, {
        "/cgi-bin/gettoken": {"errcode": 0, "access_token": "T", "expires_in": 7200},
        "/cgi-bin/kf/sync_msg": {"errcode": 0, "next_cursor": "CUR9", "msg_list": []},
    })
    cli.sync_msg()

    c2 = WeComKFClient(corp_id="wwtest", secret="sectest", state_path=cli.state_path)
    c2._load_state()
    assert c2._cursor == "CUR9", "游标必须落盘，否则重启会重复拉全量消息"


def test_send_text_body_shape(cli, monkeypatch):
    calls = _responder(cli, monkeypatch, {
        "/cgi-bin/gettoken": {"errcode": 0, "access_token": "T", "expires_in": 7200},
        "/cgi-bin/kf/send_msg": {"errcode": 0, "msgid": "MID1"},
    })
    r = cli.send_text("wmAAA", "日租 95 元")
    assert r["msgid"] == "MID1"
    body = [c for c in calls if c["path"] == "/cgi-bin/kf/send_msg"][0]["body"]
    assert body["touser"] == "wmAAA"
    assert body["open_kfid"] == "wkTEST"
    assert body["msgtype"] == "text"
    assert body["text"]["content"] == "日租 95 元"


def test_account_list(cli, monkeypatch):
    _responder(cli, monkeypatch, {
        "/cgi-bin/gettoken": {"errcode": 0, "access_token": "T", "expires_in": 7200},
        "/cgi-bin/kf/account/list": {"errcode": 0,
                                     "account_list": [{"open_kfid": "wkA"}]},
    })
    assert cli.account_list() == [{"open_kfid": "wkA"}]


def test_display_name_falls_back_to_userid(cli, monkeypatch):
    _responder(cli, monkeypatch, {
        "/cgi-bin/gettoken": {"errcode": 0, "access_token": "T", "expires_in": 7200},
        "/cgi-bin/kf/customer/batchget": WeComKFError(60011, "no privilege"),
    })
    assert cli.display_name("wmAAA") == "wmAAA"
    assert cli.display_name("wmAAA") == "wmAAA"   # 缓存住，不再请求


def test_display_name_uses_nickname(cli, monkeypatch):
    _responder(cli, monkeypatch, {
        "/cgi-bin/gettoken": {"errcode": 0, "access_token": "T", "expires_in": 7200},
        "/cgi-bin/kf/customer/batchget": {"errcode": 0,
                                          "customer_list": [{"nickname": "张三"}]},
    })
    assert cli.display_name("wmAAA") == "张三"


# ── token 失效重试 ────────────────────────────────────────────────────

def test_invalid_token_triggers_one_refresh_retry(cli, monkeypatch):
    calls = []
    state = {"token_n": 0}

    def fake_call(path, body=None, params=None, raise_on_error=True):
        calls.append(path)
        if path == "/cgi-bin/gettoken":
            state["token_n"] += 1
            return {"errcode": 0, "access_token": f"T{state['token_n']}",
                    "expires_in": 7200}
        if path == "/cgi-bin/kf/account/list":
            # 第一次用旧 token 报失效，第二次成功
            if params["access_token"] == "T1":
                raise WeComKFError(40014, "invalid access_token", path)
            return {"errcode": 0, "account_list": []}
        raise AssertionError(path)

    monkeypatch.setattr(cli, "_call", fake_call)
    assert cli.account_list() == []
    assert state["token_n"] == 2, "应该只刷新重试一次"


def test_other_errcode_is_not_retried(cli, monkeypatch):
    calls = []

    def fake_call(path, body=None, params=None, raise_on_error=True):
        calls.append(path)
        if path == "/cgi-bin/gettoken":
            return {"errcode": 0, "access_token": "T", "expires_in": 7200}
        raise WeComKFError(60020, "not allow to access from your ip", path)

    monkeypatch.setattr(cli, "_call", fake_call)
    with pytest.raises(WeComKFError) as e:
        cli.account_list()
    assert e.value.errcode == 60020
    assert calls.count("/cgi-bin/kf/account/list") == 1


# ── selftest ──────────────────────────────────────────────────────────

def test_selftest_reports_missing_credentials():
    ok, detail = WeComKFClient(corp_id="", secret="").selftest()
    assert ok is False and "WECOM_CORP_ID" in detail


def test_selftest_flags_wrong_open_kfid(cli, monkeypatch):
    _responder(cli, monkeypatch, {
        "/cgi-bin/gettoken": {"errcode": 0, "access_token": "T", "expires_in": 7200},
        "/cgi-bin/kf/account/list": {"errcode": 0,
                                     "account_list": [{"open_kfid": "wkOTHER"}]},
    })
    ok, detail = cli.selftest()
    assert ok is False and "wkOTHER" in detail


def test_selftest_ok_path(cli, monkeypatch):
    _responder(cli, monkeypatch, {
        "/cgi-bin/gettoken": {"errcode": 0, "access_token": "T", "expires_in": 7200},
        "/cgi-bin/kf/account/list": {"errcode": 0,
                                     "account_list": [{"open_kfid": "wkTEST"}]},
        "/cgi-bin/kf/sync_msg": {"errcode": 0, "next_cursor": "C", "msg_list": []},
    })
    ok, detail = cli.selftest()
    assert ok is True and "OK" in detail


def test_selftest_explains_wrong_secret(cli, monkeypatch):
    _responder(cli, monkeypatch, {
        "/cgi-bin/gettoken": WeComKFError(40001, "invalid credential"),
    })
    ok, detail = cli.selftest()
    assert ok is False
    assert "微信客服" in detail, "要提示用户拿的是微信客服那栏的 Secret"


# ── main.py 接线 ──────────────────────────────────────────────────────

def test_main_has_channel_switch_and_api_paths():
    src = Path("main.py").read_text(encoding="utf-8")
    assert 'cfg.get("channel", "screenshot")' in src
    assert "def _run_api_poll" in src
    assert "def _handle_api_message" in src
    assert "api_channel.send_text" in src, "_do_send 必须通道感知"
    assert "_seen_msgids" in src, "API 模式要按 msgid 去重"
    # 截图模式的路径不能被破坏
    assert "def _run_scan" in src
    assert "_switch_to_wecom_and_back" in src
