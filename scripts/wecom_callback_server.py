# -*- coding: utf-8 -*-
"""微信客服「回调 URL 验证」接收器 —— 只为过验证 / 看推送内容。

背景：微信客服后台那页要你填「回调URL / Token / EncodingAESKey」并点「完成」，
点之前**微信服务器会来访问你的 URL 做验证**；验证通过才显示 Secret。
这个脚本就是那个 URL 背后要跑的东西。

它做三件事：
1. ``GET  /wecom-kf``  → 校验签名 + AES 解密 echostr + **原文返回**（验证全靠这个）
2. ``POST /wecom-kf``  → 解密并打印推送内容（我们不消费它，只让人看见），返回 success
3. 每次请求都打日志，方便你亲眼看到"微信的验证真的来了"

用法（Token / EncodingAESKey 从后台点「随机获取」拿到）：
    python scripts/wecom_callback_server.py --token XXX --aes-key YYY --port 18090

然后把「回调URL」填成 ``http://<你的公网地址>/wecom-kf``（穿透工具给你的那个地址）。
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from gateway.crypto import decrypt_message, verify_signature  # noqa: E402

log = logging.getLogger("wecom-callback")

STATE = {"token": "", "aes_key": "", "corp_id": "", "verified": 0, "pushed": 0}


def handle_get(params: dict, token: str, aes_key: str, corp_id: str) -> tuple[int, str]:
    """处理微信的 URL 验证请求。

    微信发来 ``msg_signature`` / ``timestamp`` / ``nonce`` / ``echostr``，
    我们要**原样返回解密后的 echostr**（不带引号、不带 JSON 包装）。
    """
    sig = params.get("msg_signature") or params.get("signature") or ""
    ts = params.get("timestamp") or ""
    nonce = params.get("nonce") or ""
    echo = params.get("echostr") or ""
    if not (sig and ts and nonce and echo):
        log.warning(f"验证请求参数不全: {list(params)}")
        return 400, "missing params"
    if not verify_signature(sig, ts, nonce, echo, token):
        log.error("签名校验不通过 —— Token 填错了吧？（后台那个 Token 要和 --token 一致）")
        return 403, "bad signature"
    try:
        plain, got_corp = decrypt_message(echo, aes_key)
    except Exception as e:
        log.error(f"解密 echostr 失败 —— EncodingAESKey 填错了吧？({e})")
        return 403, "decrypt failed"
    if corp_id and got_corp and got_corp != corp_id:
        log.warning(f"解密出来的 corpid={got_corp} 和你给的不一样（{corp_id}）")
    STATE["verified"] += 1
    log.info(f"✓ 验证通过！返回明文（后台应该会显示'完成'）: {plain[:40]!r}")
    return 200, plain


def handle_post(body: bytes, params: dict, token: str, aes_key: str) -> tuple[int, str]:
    """微信推送消息/事件 —— 我们**不消费**（消息用 sync_msg 拉），只登记一下。

    返回 ``success`` 让微信别重试。
    """
    sig = params.get("msg_signature") or params.get("signature") or ""
    ts = params.get("timestamp") or ""
    nonce = params.get("nonce") or ""
    text = body.decode("utf-8", "replace")
    # 推送体是 XML，密文在 <Encrypt> 里，而且**外面还包着 CDATA**：
    #   <Encrypt><![CDATA[xxxxx]]></Encrypt>
    # 不剥掉 CDATA 就拿去校验签名，必然对不上（实测踩到）。
    enc = _extract_encrypt(text)
    if enc and verify_signature(sig, ts, nonce, enc, token):
        try:
            plain, _corp = decrypt_message(enc, aes_key)
            STATE["pushed"] += 1
            log.info(f"收到推送（我们不消费，只登记）: {plain[:300]}")
        except Exception as e:
            log.warning(f"推送解密失败: {e}")
    else:
        log.info(f"收到 POST（签名没校验过或不是加密体），长度 {len(text)}")
    return 200, "success"


def _extract_encrypt(text: str) -> str:
    """从推送 XML 里抠出加密载荷（去掉 CDATA 包装）。"""
    if "<Encrypt>" not in text:
        return ""
    enc = text.split("<Encrypt>", 1)[1].split("</Encrypt>", 1)[0].strip()
    if enc.startswith("<![CDATA["):
        enc = enc[len("<![CDATA["):]
    if enc.endswith("]]>"):
        enc = enc[:-len("]]>")]
    return enc.strip()


def main():
    # 这些副作用只在**当脚本跑**时做：放模块级别会在测试 import 时劫持 stdout，
    # 把 pytest 的输出捕获搞崩（实测踩到）。
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", required=True, help="后台「Token」（点随机获取拿到的那个）")
    ap.add_argument("--aes-key", required=True,
                    help="后台「EncodingAESKey」（点随机获取拿到的那个）")
    ap.add_argument("--corp-id", default="", help="可选：你的企业 ID，用来核对解密结果")
    ap.add_argument("--port", type=int, default=18090)
    ap.add_argument("--path", default="/wecom-kf", help="回调路径（要跟后台填的一致）")
    args = ap.parse_args()

    STATE.update({"token": args.token, "aes_key": args.aes_key,
                  "corp_id": args.corp_id})
    path = args.path if args.path.startswith("/") else "/" + args.path

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass                      # 用我们自己的日志

        def _reply(self, code: int, text: str):
            body = text.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            params = {k: v[0] for k, v in parse_qs(u.query).items()}
            log.info(f"GET {u.path} 参数={list(params)}")
            if u.path != path:
                return self._reply(404, "not found")
            code, text = handle_get(params, args.token, args.aes_key, args.corp_id)
            self._reply(code, text)

        def do_POST(self):
            u = urlparse(self.path)
            params = {k: v[0] for k, v in parse_qs(u.query).items()}
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b""
            log.info(f"POST {u.path} 长度={len(body)}")
            if u.path != path:
                return self._reply(404, "not found")
            code, text = handle_post(body, params, args.token, args.aes_key)
            self._reply(code, text)

    srv = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print("=" * 66)
    print(f"回调接收器已启动：http://127.0.0.1:{args.port}{path}")
    print(f"后台「回调URL」要填成：http://<穿透给你的公网地址>{path}")
    print(f"Token={args.token[:6]}…（{len(args.token)} 位）  "
          f"EncodingAESKey={args.aes_key[:6]}…（{len(args.aes_key)} 位）")
    print("等微信来验证……（Ctrl+C 退出）")
    print("=" * 66)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print(f"\n退出。验证通过 {STATE['verified']} 次，收到推送 {STATE['pushed']} 条。")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
