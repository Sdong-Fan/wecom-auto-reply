# -*- coding: utf-8 -*-
"""企业微信「微信客服」API 连通自检 —— 拿真实凭据跑一次，确认能不能用。

凭据放 .env（已被 .gitignore 忽略），不要在命令行里明文传：
    WECOM_CORP_ID=ww........
    WECOM_KF_SECRET=........     # 微信客服专用 Secret，不是自建应用的
    WECOM_OPEN_KFID=wk........    # 可留空，只有一个客服账号时自动选

用法:
    python scripts/wecom_api_selftest.py                # 自检（只读，不发消息）
    python scripts/wecom_api_selftest.py --ip           # 只查本机出口公网 IP（配「可信 IP」用）
    python scripts/wecom_api_selftest.py --peek         # 顺带打印最近拉到的客户消息
    python scripts/wecom_api_selftest.py --send wkxxx 你好   # 真的发一条（会打扰客户，慎用）
"""
from __future__ import annotations

import argparse
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(HERE, ".env"))
except Exception:
    pass

from gateway.wecom_kf import (WeComKFClient, WeComKFError,  # noqa: E402
                              public_ip)


def mask(v: str) -> str:
    if not v:
        return "(空)"
    return v[:4] + "****" + v[-4:] if len(v) > 10 else "****"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", action="store_true",
                    help="只查本机出口公网 IP（配微信客服的「可信 IP」时填它）")
    ap.add_argument("--corp-id", default="", help="临时用这个企业 ID（不写进 .env）")
    ap.add_argument("--secret", default="", help="临时用这个 Secret（不写进 .env）")
    ap.add_argument("--open-kfid", default="", help="临时用这个客服账号 ID")
    ap.add_argument("--peek", action="store_true", help="打印最近拉到的客户消息")
    ap.add_argument("--send", nargs=2, metavar=("EXTERNAL_USERID", "TEXT"),
                    help="真的发一条消息（会打扰客户）")
    ap.add_argument("--limit", type=int, default=50)
    args = ap.parse_args()

    if args.ip:
        ip = public_ip()
        if ip:
            print(f"本机出口公网 IP：{ip}")
            print("→ 把它填进微信客服后台的「API」→「可信 IP」，"
                  "否则调用接口会报 60020。")
            print("  注意：家宽/公司网重启后这个 IP 可能变，连不上先回来查一次。")
        else:
            print("没查到（网络被挡？）。可以浏览器搜「我的 IP」手动看。")
        return 0 if ip else 1

    cli = WeComKFClient.from_env()
    # 命令行传的临时覆盖 .env 里的值：方便一次试几个候选 Secret，
    # 试通了再去界面里正式填（命令行里的值会留在 shell 历史里，注意别外传）
    if args.corp_id:
        cli.corp_id = args.corp_id.strip()
    if args.secret:
        cli.secret = args.secret.strip()
    if args.open_kfid:
        cli.open_kfid = args.open_kfid.strip()
    print("凭据（脱敏）：")
    print(f"    WECOM_CORP_ID   = {mask(cli.corp_id)}")
    print(f"    WECOM_KF_SECRET = {mask(cli.secret)}")
    print(f"    WECOM_OPEN_KFID = {mask(cli.open_kfid)}")
    print(f"    游标文件        = {cli.state_path}\n")

    ok, detail = cli.selftest()
    print(("✓ " if ok else "✗ ") + detail)
    if not ok:
        print("\n自检没过，先按上面的提示在企业微信后台把权限/凭据配好。")
        return 1 if not ok else 0

    if args.peek:
        print(f"\n拉取最近消息（最多 {args.limit} 条客户文本）：")
        msgs = cli.sync_msg(limit=args.limit)
        if not msgs:
            print("   （没有新消息；注意 sync_msg 只给「上次游标之后」的消息）")
        for m in msgs:
            print(f"   from={m.external_userid}  昵称={cli.display_name(m.external_userid)}")
            print(f"        [{m.send_time}] {m.text[:80]!r}")
    else:
        print("\n提示：加 --peek 可以看看能不能真的拉到客户消息。")

    if args.send:
        uid, text = args.send
        try:
            r = cli.send_text(uid, text)
            print(f"\n✓ 已发送 msgid={r.get('msgid')}")
        except WeComKFError as e:
            print(f"\n✗ 发送失败: {e}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
