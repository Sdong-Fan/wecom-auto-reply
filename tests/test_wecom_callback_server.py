# tests/test_wecom_callback_server.py
"""微信客服「回调 URL 验证」接收器。

这一步只为拿到 Secret：后台要填 回调URL/Token/EncodingAESKey，点「完成」之前
微信会来访问那个 URL 做验证。验证要**原样返回解密后的 echostr**，
Signature/AES 任何一处错都过不了 —— 而这个脚本是用户唯一能看到的现场。
"""

import json
import threading
import urllib.error
import urllib.request

import pytest

from gateway import crypto
from scripts import wecom_callback_server as srv


TOKEN = "testtoken123"
# 43 位 EncodingAESKey（企业微信要求），base64 解码后 32 字节
AES_KEY = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG"
CORP_ID = "wwe0123456789abcd"


def _encrypted(plain: str) -> str:
    return crypto.encrypt_message(plain, AES_KEY, CORP_ID)


def _signature(ts: str, nonce: str, echo: str) -> str:
    import hashlib
    joined = "".join(sorted([TOKEN, ts, nonce, echo]))
    return hashlib.sha1(joined.encode()).hexdigest()


# ── GET：URL 验证 ─────────────────────────────────────────────────────

def test_verify_returns_decrypted_echostr():
    echo = _encrypted("hello-wecom-12345")
    ts, nonce = "1700000000", "abc"
    code, text = srv.handle_get(
        {"msg_signature": _signature(ts, nonce, echo), "timestamp": ts,
         "nonce": nonce, "echostr": echo},
        TOKEN, AES_KEY, CORP_ID)
    assert code == 200
    assert text == "hello-wecom-12345", "必须原样返回明文，不能加引号/JSON 包装"


def test_verify_accepts_signature_param_name():
    """有的回调用 `signature` 而不是 `msg_signature`，两个都认。"""
    echo = _encrypted("ping")
    ts, nonce = "1700000001", "n2"
    code, text = srv.handle_get(
        {"signature": _signature(ts, nonce, echo), "timestamp": ts,
         "nonce": nonce, "echostr": echo},
        TOKEN, AES_KEY, CORP_ID)
    assert code == 200 and text == "ping"


def test_verify_rejects_bad_signature():
    echo = _encrypted("ping")
    code, text = srv.handle_get(
        {"msg_signature": "deadbeef", "timestamp": "1", "nonce": "2",
         "echostr": echo}, TOKEN, AES_KEY, CORP_ID)
    assert code == 403
    assert "signature" in text


def test_verify_rejects_wrong_token():
    echo = _encrypted("ping")
    ts, nonce = "1700000002", "n3"
    sig = _signature(ts, nonce, echo)          # 用正确 token 算的
    code, _ = srv.handle_get(
        {"msg_signature": sig, "timestamp": ts, "nonce": nonce, "echostr": echo},
        "错的Token", AES_KEY, CORP_ID)          # 但服务器配的是别的
    assert code == 403


def test_verify_rejects_wrong_aes_key():
    echo = _encrypted("ping")
    ts, nonce = "1700000003", "n4"
    code, text = srv.handle_get(
        {"msg_signature": _signature(ts, nonce, echo), "timestamp": ts,
         "nonce": nonce, "echostr": echo},
        TOKEN, "ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ", CORP_ID)
    assert code == 403
    assert "decrypt" in text or "解密" in text


def test_verify_rejects_missing_params():
    code, text = srv.handle_get({"timestamp": "1"}, TOKEN, AES_KEY, CORP_ID)
    assert code == 400


def test_verify_counts():
    srv.STATE["verified"] = 0
    echo = _encrypted("x")
    ts, nonce = "1700000004", "n5"
    srv.handle_get({"msg_signature": _signature(ts, nonce, echo),
                    "timestamp": ts, "nonce": nonce, "echostr": echo},
                   TOKEN, AES_KEY, CORP_ID)
    assert srv.STATE["verified"] == 1


# ── POST：推送（我们不消费，只登记） ─────────────────────────────────

def test_post_records_pushed_message():
    srv.STATE["pushed"] = 0
    inner = ('<xml><ToUserName><![CDATA[ww]]></ToUserName>'
             '<MsgType><![CDATA[text]]></MsgType>'
             '<Content><![CDATA[有货吗]]></Content></xml>')
    enc = _encrypted(inner)
    body = (f"<xml><Encrypt><![CDATA[{enc}]]></Encrypt>"
            f"<MsgSignature><![CDATA[x]]></MsgSignature></xml>").encode()
    ts, nonce = "1700000005", "n6"
    code, text = srv.handle_post(
        body, {"msg_signature": _signature(ts, nonce, enc),
               "timestamp": ts, "nonce": nonce}, TOKEN, AES_KEY)
    assert code == 200
    assert text == "success", "要让微信别重试"
    assert srv.STATE["pushed"] == 1


def test_post_garbage_body_does_not_crash():
    """推送体不是加密 XML 时也不能 500 —— 只登记。"""
    code, text = srv.handle_post(b"not xml at all", {},
                                 TOKEN, AES_KEY)
    assert code == 200 and text == "success"


def test_extract_encrypt_strips_cdata():
    """微信推送的密文外面包着 CDATA —— 不剥掉签名就永远校验不过。"""
    assert srv._extract_encrypt("<xml><Encrypt><![CDATA[abc123]]></Encrypt></xml>") == "abc123"
    assert srv._extract_encrypt("<xml><Encrypt>abc123</Encrypt></xml>") == "abc123"
    assert srv._extract_encrypt("<xml>没有密文</xml>") == ""
    assert srv._extract_encrypt("") == ""


# ── 真起服务器跑一遍 HTTP ────────────────────────────────────────────

@pytest.fixture
def server():
    from http.server import ThreadingHTTPServer
    from urllib.parse import parse_qs, urlparse

    class Handler(srv.BaseHTTPRequestHandler):  # type: ignore[attr-defined]
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _reply(self, code, text):
            b = text.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            u = urlparse(self.path)
            p = {k: v[0] for k, v in parse_qs(u.query).items()}
            if u.path != "/wecom-kf":
                return self._reply(404, "not found")
            code, text = srv.handle_get(p, TOKEN, AES_KEY, CORP_ID)
            self._reply(code, text)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def test_http_roundtrip(server):
    from urllib.parse import quote
    echo = _encrypted("round-trip-ok")
    ts, nonce = "1700000006", "n7"
    # base64 里有 + / =，必须 URL 编码（微信自己会编码）——
    # 不编码的话 + 会被解成空格，签名就对不上了
    url = (f"{server}/wecom-kf?msg_signature={_signature(ts, nonce, echo)}"
           f"&timestamp={ts}&nonce={nonce}&echostr={quote(echo)}")
    with urllib.request.urlopen(url, timeout=10) as r:
        assert r.status == 200
        assert r.read().decode() == "round-trip-ok"


def test_http_wrong_path_404(server):
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(f"{server}/other", timeout=10)
    assert e.value.code == 404


# ── 说明与依赖 ────────────────────────────────────────────────────────

def test_script_documents_the_mode_question():
    """脚本头要说清这一步的用途和"回调 vs 拉取"的取舍，别让后人误以为必须用它。"""
    from pathlib import Path
    s = (Path(__file__).resolve().parent.parent
         / "scripts" / "wecom_callback_server.py").read_text(encoding="utf-8")
    assert "只为过这一步" in s or "只为过验证" in s
    assert "sync_msg" in s
