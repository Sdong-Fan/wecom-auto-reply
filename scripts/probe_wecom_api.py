# -*- coding: utf-8 -*-
"""企业微信 API 连通性 + 协议形状探针（**不需要真实凭据**）。

目的：在让你去后台申请权限之前，先确认两件事
  1. 这台机器能不能连上 qyapi.weixin.qq.com（TLS/DNS/代理）
  2. 我们要用的那几个接口路径对不对 —— 用**故意错误**的参数去打，
     如果返回企业微信标准错误 JSON（errcode/errmsg），就说明路径与协议是对的，
     错只错在参数上。这是零风险、零凭据的验证方式。

只读探测：不发消息、不改任何配置。

用法:
    python scripts/probe_wecom_api.py
    python scripts/probe_wecom_api.py --proxy http://127.0.0.1:11111
"""
from __future__ import annotations

import argparse
import io
import json
import ssl
import sys
import urllib.error
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = "https://qyapi.weixin.qq.com"

# 我们打算用的接口（企业微信官方文档）：
#   gettoken      获取 access_token
#   kf/sync_msg   拉取微信客服的客户消息
#   kf/send_msg   以客服身份回复客户
#   kf/service_state_get / kf/account/list  辅助接口
PROBES = [
    ("gettoken", "GET", "/cgi-bin/gettoken?corpid=__probe_bad__&corpsecret=__probe_bad__", None),
    ("kf/sync_msg", "POST", "/cgi-bin/kf/sync_msg?access_token=__probe_bad__",
     {"cursor": "", "token": "", "limit": 1}),
    ("kf/send_msg", "POST", "/cgi-bin/kf/send_msg?access_token=__probe_bad__",
     {"touser": "probe", "open_kfid": "probe", "msgtype": "text", "text": {"content": "probe"}}),
    ("kf/account/list", "POST", "/cgi-bin/kf/account/list?access_token=__probe_bad__", {}),
    ("kf/service_state/get", "POST", "/cgi-bin/kf/service_state/get?access_token=__probe_bad__",
     {"open_kfid": "probe", "external_userid": "probe"}),
]


def build_opener(proxy: str | None):
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        handlers.append(urllib.request.ProxyHandler({}))   # 忽略环境变量里的代理
    ctx = ssl.create_default_context()
    handlers.append(urllib.request.HTTPSHandler(context=ctx))
    return urllib.request.build_opener(*handlers)


def call(opener, name, method, path, body):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with opener.open(req, timeout=20) as r:
            raw = r.read().decode("utf-8", "replace")
            return r.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--proxy", default="", help="走代理，例如 http://127.0.0.1:11111")
    args = ap.parse_args()

    opener = build_opener(args.proxy or None)
    print(f"目标: {BASE}   代理: {args.proxy or '（不使用）'}\n")

    ok_paths = []
    for name, method, path, body in PROBES:
        status, raw = call(opener, name, method, path, body)
        errcode = errmsg = None
        try:
            j = json.loads(raw)
            errcode, errmsg = j.get("errcode"), j.get("errmsg")
        except Exception:
            pass
        verdict = "?"
        if errcode is not None:
            # 参数错/凭据错 ⇒ 路径和协议是对的
            verdict = "✓ 路径可达（返回标准错误 JSON）"
            ok_paths.append(name)
        elif status is None:
            verdict = "✗ 连不上"
        else:
            verdict = f"✗ 非预期响应（HTTP {status}）"
        print(f"[{name}] {method} {path.split('?')[0]}")
        print(f"    HTTP={status}  errcode={errcode}  errmsg={errmsg!r}")
        if errcode is None:
            print(f"    原始: {raw[:200]!r}")
        print(f"    → {verdict}\n")

    print("=" * 60)
    print(f"可达接口 {len(ok_paths)}/{len(PROBES)}: {ok_paths}")
    if len(ok_paths) == len(PROBES):
        print("结论：网络通、路径对。给我 corp_id / secret / open_kfid 就能真跑。")
        print("      注意 errcode 40013=corpid 错，40001/40014/42001=secret 或 token 错 ——")
        print("      拿真实凭据再跑一次，这几类错误消失就说明凭据有效。")
    elif not ok_paths:
        print("结论：连不上 qyapi.weixin.qq.com。检查网络/代理/TLS。")
    else:
        print("结论：部分接口不通，看上面 ✗ 的那几条（可能是路径写错或权限不足）。")


if __name__ == "__main__":
    main()
