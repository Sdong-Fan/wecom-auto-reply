# tests/test_wecom_credentials.py
"""凭据接入这块的"防呆"。

非技术用户接入时最容易卡的两处：
1. 把 `.env.example` 复制成 `.env` 就以为配好了 —— 而 `your_corp_id` 看起来"有个值"，
   自检会一路往下走、报出更难懂的错
2. `60020 not allow to access from your ip` —— 不知道该填哪个 IP，
   而且**查回来的可能是 IPv6 或代理的出口 IP**，填进去后台根本不认
"""

import pytest

from gateway import wecom_kf
from gateway.wecom_kf import WeComKFClient, WeComKFError, is_placeholder


# ── 占位符识别 ────────────────────────────────────────────────────────

def test_placeholder_detected():
    for v in ("your_corp_id", "your-kf-secret", "xxx", "XXXX", "TODO",
              "changeme", "___", "", "   "):
        assert is_placeholder(v) is True, v


def test_real_values_not_placeholder():
    for v in ("ww10123456789abc", "AbCdEf123456", "wkMOCK01", "sk-abc123"):
        assert is_placeholder(v) is False, v


def test_credentials_present_rejects_placeholders():
    c = WeComKFClient(corp_id="your_corp_id", secret="your_kf_secret")
    assert c.credentials_present() is False


def test_credentials_present_accepts_real():
    c = WeComKFClient(corp_id="ww10123456789abc", secret="AbCdEf123456")
    assert c.credentials_present() is True


def test_credentials_present_needs_both():
    assert WeComKFClient(corp_id="ww123", secret="").credentials_present() is False
    assert WeComKFClient(corp_id="", secret="abc").credentials_present() is False


def test_selftest_says_placeholder_explicitly():
    c = WeComKFClient(corp_id="your_corp_id", secret="your_kf_secret")
    ok, detail = c.selftest()
    assert ok is False
    assert "占位符" in detail, "要说清是没填，而不是含糊的'缺少凭据'"
    assert "WECOM_CORP_ID" in detail and "WECOM_KF_SECRET" in detail
    assert "不是自建应用的" in detail, "要提醒不是自建应用的 Secret"


def test_selftest_plain_missing_message():
    """完全空的时候给的是"缺少凭据"，不要误说成占位符。"""
    c = WeComKFClient(corp_id="", secret="")
    ok, detail = c.selftest()
    assert ok is False
    assert "缺少凭据" in detail
    assert "占位符" not in detail


# ── 公网 IP ───────────────────────────────────────────────────────────

def test_looks_like_ip_accepts_ipv4():
    assert wecom_kf._looks_like_ip("203.0.113.10") is True
    assert wecom_kf._looks_like_ip("203.0.113.10") is True


def test_looks_like_ip_rejects_bad_ipv4():
    for v in ("999.1.1.1", "1.2.3", "1.2.3.4.5", "abc", "", "  ", "203.0.113.10 5"):
        assert wecom_kf._looks_like_ip(v) is False, v


def test_looks_like_ip_accepts_ipv6_as_last_resort():
    """IPv4 拿不到时才接受 IPv6（后台的可信 IP 要 IPv4，所以只当兜底）。"""
    assert wecom_kf._looks_like_ip("2409:8a34:1ed5::1") is True


def test_public_ip_prefers_ipv4(monkeypatch):
    """机器同时有 IPv6 时，Python 默认可能连到 v6 上去 —— 查回来填后台不认。"""
    seen = []

    class _Resp:
        def __init__(self, text):
            self._t = text

        def read(self):
            return self._t.encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_open(req, timeout=None):
        seen.append(req.full_url)
        if "api4.ipify.org" in req.full_url:
            return _Resp("203.0.113.10")
        return _Resp("2409:8a34:1ed5:d274:6504:fae:4a78:78b5")

    class _Opener:
        def open(self, req, timeout=None):
            return fake_open(req, timeout)

    monkeypatch.setattr(wecom_kf.urllib.request, "build_opener",
                        lambda *a, **k: _Opener())
    assert wecom_kf.public_ip() == "203.0.113.10"


def test_public_ip_falls_back_when_v4_endpoint_down(monkeypatch):
    class _Resp:
        def __init__(self, text):
            self._t = text

        def read(self):
            return self._t.encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Opener:
        def open(self, req, timeout=None):
            if "api4" in req.full_url:
                raise OSError("连不上")
            return _Resp("203.0.113.10")

    monkeypatch.setattr(wecom_kf.urllib.request, "build_opener",
                        lambda *a, **k: _Opener())
    assert wecom_kf.public_ip() == "203.0.113.10"


def test_public_ip_returns_empty_when_all_fail(monkeypatch):
    class _Opener:
        def open(self, req, timeout=None):
            raise OSError("全挂了")

    monkeypatch.setattr(wecom_kf.urllib.request, "build_opener",
                        lambda *a, **k: _Opener())
    assert wecom_kf.public_ip() == ""


def test_60020_hint_includes_current_ip(monkeypatch):
    """60020 是最常见的坑：提示里要直接给出**此刻该填的那个 IP**。"""
    monkeypatch.setattr(wecom_kf, "public_ip", lambda *a, **k: "203.0.113.10")

    def boom(self, force=False):
        raise WeComKFError(60020, "not allow to access from your ip")

    monkeypatch.setattr(WeComKFClient, "token", boom)
    c = WeComKFClient(corp_id="ww10123456789abc", secret="AbCdEf123456")
    ok, detail = c.selftest()
    assert ok is False
    assert "203.0.113.10" in detail
    assert "可信 IP" in detail
    assert "IP 可能变" in detail or "可能变" in detail


def test_60020_hint_without_ip(monkeypatch):
    monkeypatch.setattr(wecom_kf, "public_ip", lambda *a, **k: "")

    def boom(self, force=False):
        raise WeComKFError(60020, "not allow to access from your ip")

    monkeypatch.setattr(WeComKFClient, "token", boom)
    c = WeComKFClient(corp_id="ww10123456789abc", secret="AbCdEf123456")
    ok, detail = c.selftest()
    assert ok is False
    assert "可信 IP" in detail
    assert "没能自动查到" in detail


# ── 脚本 ──────────────────────────────────────────────────────────────

def test_selftest_script_has_ip_flag():
    from pathlib import Path
    s = (Path(__file__).resolve().parent.parent
         / "scripts" / "wecom_api_selftest.py").read_text(encoding="utf-8")
    assert '"--ip"' in s
    assert "public_ip" in s


def test_selftest_script_can_take_temp_credentials():
    """能临时传参试几个候选 Secret，不用先写进 .env。"""
    from pathlib import Path
    s = (Path(__file__).resolve().parent.parent
         / "scripts" / "wecom_api_selftest.py").read_text(encoding="utf-8")
    for flag in ('"--corp-id"', '"--secret"', '"--open-kfid"'):
        assert flag in s
    assert "cli.secret = args.secret.strip()" in s


# ── 客服账号列表：把真正该填的 open_kfid 打出来 ──────────────────────

def test_account_list_mismatch_shows_real_ids(monkeypatch):
    """后台界面上显示的"账号ID"不一定就是 open_kfid（有人拿到 kf 开头的），
    所以要把接口返回的真实值打出来。"""
    monkeypatch.setattr(WeComKFClient, "token", lambda self, force=False: "T")
    monkeypatch.setattr(WeComKFClient, "account_list", lambda self: [
        {"open_kfid": "wkAJ2GCAAASSm4_FhToWMFea0xAFfd3Q", "name": "摄影器材租赁客服"}])
    c = WeComKFClient(corp_id="ww10123456789abc", secret="AbCdEf123456",
                      open_kfid="kfcb5fcfa7e2aa2ad58")
    ok, detail = c.selftest()
    assert ok is False
    assert "wkAJ2GCAAASSm4_FhToWMFea0xAFfd3Q" in detail
    assert "该填的是" in detail
    assert "摄影器材租赁客服" in detail, "带上客服账号名字，方便对上是哪个"


def test_account_list_no_permission_hint(monkeypatch):
    """自建应用的 Secret 没被设成「可调用接口的应用」时会没权限 —— 要说清怎么补。"""
    monkeypatch.setattr(WeComKFClient, "token", lambda self, force=False: "T")

    def boom(self):
        raise WeComKFError(48002, "api forbidden")

    monkeypatch.setattr(WeComKFClient, "account_list", boom)
    c = WeComKFClient(corp_id="ww10123456789abc", secret="AbCdEf123456")
    ok, detail = c.selftest()
    assert ok is False
    assert "可调用接口的应用" in detail


def test_account_list_ip_hint(monkeypatch):
    monkeypatch.setattr(WeComKFClient, "token", lambda self, force=False: "T")
    monkeypatch.setattr(wecom_kf, "public_ip", lambda *a, **k: "203.0.113.10")

    def boom(self):
        raise WeComKFError(60020, "not allow to access from your ip")

    monkeypatch.setattr(WeComKFClient, "account_list", boom)
    c = WeComKFClient(corp_id="ww10123456789abc", secret="AbCdEf123456")
    ok, detail = c.selftest()
    assert ok is False
    assert "203.0.113.10" in detail and "可信 IP" in detail


def test_bad_corp_id_gives_40013_not_40001(monkeypatch):
    """这两种错的区分很重要：40013=企业ID错，40001=Secret错。

    实测靠这个判断出"企业 ID 是对的、只差 Secret"。
    """
    def boom(self, force=False):
        raise WeComKFError(40013, "invalid corpid")

    monkeypatch.setattr(WeComKFClient, "token", boom)
    c = WeComKFClient(corp_id="ww不对", secret="AbCdEf123456")
    ok, detail = c.selftest()
    assert ok is False
    assert "40013" in detail and "WECOM_CORP_ID 不对" in detail
