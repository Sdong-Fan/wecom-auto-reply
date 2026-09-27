# -*- coding: utf-8 -*-
"""企业微信「微信客服」API 的本地假服务器 —— 没有真凭据也能把 API 模式整条链路跑通。

它假装自己是 qyapi.weixin.qq.com，实现我们用到的那 5 个接口：
    GET  /cgi-bin/gettoken
    POST /cgi-bin/kf/account/list
    POST /cgi-bin/kf/sync_msg         （按脚本吐客户消息，并推进游标）
    POST /cgi-bin/kf/send_msg         （把"发出去的"记下来，供断言/查看）
    POST /cgi-bin/kf/customer/batchget

两种用法：
  1. 当库用（pytest 里起在后台线程）：
         srv = MockWeCom(script=[{"external_userid": "wmAAA", "text": "有富士吗"}])
         srv.start(); client = WeComKFClient(..., base=srv.base_url)
  2. 当命令行用（手动跑一遍看效果）：
         python scripts/mock_wecom_server.py --script-file msgs.json

只能在本机跑，端口默认随机；不含任何真实凭据。
"""
from __future__ import annotations

import argparse
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class MockWeCom:
    """假企业微信服务器。"""

    def __init__(self, script=None, corp_id="wwmock", secret="mocksecret",
                 open_kfid="wkMOCK", host="127.0.0.1", port=0):
        self.corp_id = corp_id
        self.secret = secret
        self.open_kfid = open_kfid
        self.script = list(script or [])     # [{external_userid, text, origin?, msgtype?}]
        self.sent: list = []                 # 记录 send_msg 的内容
        self.events_sent: list = []          # 记录 send_msg_on_event（欢迎语）
        self.calls: list = []                # 记录所有请求（含 body），便于断言
        self.cursor = ""                     # 客户端上次拿到的游标
        self.delivered = 0                   # 已经吐出去多少条
        self.require_token = True
        # 故障注入：前 N 次 send_msg 返回"系统繁忙"（errcode -1），用来测重试
        self.fail_send_times = 0
        self.fail_send_errcode = -1
        self._send_attempts = 0
        self._httpd = None
        self._thread = None
        self._host, self._port = host, port
        self.base_url = ""

    # ── 生命周期 ────────────────────────────────────────────────────────

    def start(self):
        srv = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):        # 别往 stdout 吐访问日志
                pass

            def _json(self, obj, code=200):
                body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _read_body(self):
                n = int(self.headers.get("Content-Length") or 0)
                if not n:
                    return {}
                try:
                    return json.loads(self.rfile.read(n).decode("utf-8"))
                except Exception:
                    return {}

            def do_GET(self):
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                srv.calls.append({"path": u.path, "query": q, "body": None})
                if u.path == "/cgi-bin/gettoken":
                    if q.get("corpid") != srv.corp_id or q.get("corpsecret") != srv.secret:
                        return self._json({"errcode": 40013, "errmsg": "invalid corpid"})
                    return self._json({"errcode": 0, "errmsg": "ok",
                                       "access_token": "MOCK_TOKEN", "expires_in": 7200})
                return self._json({"errcode": 404, "errmsg": "unknown path"})

            def do_POST(self):
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                body = self._read_body()
                srv.calls.append({"path": u.path, "query": q, "body": body})
                if srv.require_token and q.get("access_token") != "MOCK_TOKEN":
                    return self._json({"errcode": 40014, "errmsg": "invalid access_token"})

                if u.path == "/cgi-bin/kf/account/list":
                    return self._json({"errcode": 0, "errmsg": "ok",
                                       "account_list": [{"open_kfid": srv.open_kfid,
                                                         "name": "摄影器材租赁客服"}]})

                if u.path == "/cgi-bin/kf/sync_msg":
                    # 按客户端给的游标决定从第几条开始给 —— 必须真的认游标，
                    # 否则"游标推进/未推进"这类 bug 在假服务器上根本测不出来。
                    m = re.match(r"CUR(\d+)$", str(body.get("cursor") or ""))
                    start = int(m.group(1)) if m else 0
                    start = max(0, min(start, len(srv.script)))
                    limit = int(body.get("limit", 1000) or 1000)
                    batch = []
                    idx = start
                    while idx < len(srv.script) and len(batch) < limit:
                        item = dict(srv.script[idx])
                        idx += 1
                        item.setdefault("origin", 3)
                        item.setdefault("msgtype", "text")
                        msg = {
                            "msgid": item.get("msgid", f"MSG{idx}"),
                            "open_kfid": srv.open_kfid,
                            "external_userid": item.get("external_userid", "wmUNKNOWN"),
                            "send_time": item.get("send_time", 1758700000 + idx),
                            "origin": item["origin"],
                            "msgtype": item["msgtype"],
                        }
                        if item["msgtype"] == "text":
                            msg["text"] = {"content": item.get("text", "")}
                        elif item["msgtype"] == "voice":
                            # voice_format=1 时服务端会给转写文本；给了就用
                            v = {"media_id": item.get("media_id", "amr1")}
                            if item.get("voice_text") is not None:
                                v["text"] = item["voice_text"]
                            msg["voice"] = v
                        elif item["msgtype"] == "image":
                            msg["image"] = {"media_id": item.get("media_id", "img1")}
                        elif item["msgtype"] == "event":
                            msg["event"] = item.get("event") or {
                                "event_type": "enter_session",
                                "code": item.get("code", "CODE1")}
                        batch.append(msg)
                    nxt = f"CUR{idx}"
                    srv.cursor = nxt
                    return self._json({"errcode": 0, "errmsg": "ok",
                                       "next_cursor": nxt,
                                       "has_more": 1 if idx < len(srv.script) else 0,
                                       "msg_list": batch})

                if u.path == "/cgi-bin/kf/send_msg":
                    srv._send_attempts += 1
                    if srv._send_attempts <= srv.fail_send_times:
                        return self._json({"errcode": srv.fail_send_errcode,
                                           "errmsg": "system busy"})
                    srv.sent.append(body)
                    return self._json({"errcode": 0, "errmsg": "ok",
                                       "msgid": f"SENT{len(srv.sent)}"})

                if u.path == "/cgi-bin/kf/send_msg_on_event":
                    srv.events_sent.append(body)
                    return self._json({"errcode": 0, "errmsg": "ok",
                                       "msgid": f"EV{len(srv.events_sent)}"})

                if u.path == "/cgi-bin/kf/customer/batchget":
                    uids = body.get("external_userid") or []
                    return self._json({"errcode": 0, "errmsg": "ok",
                                       "customer_list": [{"external_userid": u,
                                                          "nickname": f"客户{str(u)[-3:]}"}
                                                         for u in uids]})
                return self._json({"errcode": 404, "errmsg": "unknown path"})

        self._httpd = ThreadingHTTPServer((self._host, self._port), Handler)
        self._port = self._httpd.server_address[1]
        self.base_url = f"http://{self._host}:{self._port}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread:
            self._thread.join(timeout=5)

    def __enter__(self):
        return self.start()

    def __exit__(self, *a):
        self.stop()

    # ── 断言辅助 ────────────────────────────────────────────────────────

    def sent_texts(self):
        out = []
        for b in self.sent:
            out.append({
                "touser": b.get("touser"),
                "text": ((b.get("text") or {}).get("content") or ""),
            })
        return out

    def welcome_texts(self):
        """发出去的欢迎语（走 send_msg_on_event）。"""
        return [{"code": b.get("code"),
                 "text": ((b.get("text") or {}).get("content") or "")}
                for b in self.events_sent]


def main():
    import io
    import sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser()
    ap.add_argument("--script-file", help="JSON 文件：[{external_userid,text}, …]")
    ap.add_argument("--port", type=int, default=18080)
    ap.add_argument("--hold", type=float, default=120.0, help="跑多少秒后自动退出")
    args = ap.parse_args()

    script = [{"external_userid": "wmAAAA", "text": "你们有富士 X-T5 吗"},
              {"external_userid": "wmAAAA", "text": "多少钱一天"}]
    if args.script_file:
        with open(args.script_file, encoding="utf-8") as f:
            script = json.load(f)

    srv = MockWeCom(script=script, port=args.port).start()
    print(f"假企业微信已启动: {srv.base_url}")
    print(f"用这个跑主程序：先设 WECOM_API_BASE={srv.base_url}，"
          f"corp_id={srv.corp_id} secret={srv.secret} open_kfid={srv.open_kfid}")
    print(f"脚本消息 {len(script)} 条，{args.hold:.0f} 秒后自动退出。Ctrl+C 提前结束。\n")
    try:
        import time
        t0 = time.time()
        while time.time() - t0 < args.hold:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        srv.stop()
        print("\n收件箱（假服务器收到的回复）：")
        for s in srv.sent_texts():
            print(f"   → {s['touser']}: {s['text']}")
        print(f"共处理请求 {len(srv.calls)} 次")


if __name__ == "__main__":
    main()
