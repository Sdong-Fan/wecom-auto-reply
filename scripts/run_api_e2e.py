# -*- coding: utf-8 -*-
"""API 模式端到端跑通验证 —— 用本地假企业微信服务器，不需要任何真凭据。

它证明的是：**API 模式下"读消息 → 该不该答 → RAG → 分流 → 回复"整条链路是通的**，
而不是只有单元测试里 mock 出来的形状对。

跑法（需要先停掉正在运行的客服程序，Qdrant 本地文件模式同一时刻只能一个进程打开）：
    python scripts/run_api_e2e.py
"""
from __future__ import annotations

import asyncio
import io
import os
import queue
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "scripts"))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(HERE, ".env"))     # 主程序也是这么读 DEEPSEEK_API_KEY 的
except Exception:
    pass

from mock_wecom_server import MockWeCom          # noqa: E402
from gateway.wecom_kf import WeComKFClient       # noqa: E402
from gateway.api_policy import (dispatch_api_message,   # noqa: E402
                               dispatch_api_nontext, pick_nontext_ack,
                               should_welcome)
from rag import prompt_store as ps               # noqa: E402

# 覆盖全部分支：文本（走 RAG）、该不该答（不回）、知识库没有（转人工）、
# 图片/语音（原来整个丢掉 → 现在应答 + 转人工）、进入会话（欢迎语）
SCRIPT = [
    {"external_userid": "wmCUST01", "text": "你们有富士 X-T5 吗"},   # 应该走 RAG
    {"external_userid": "wmCUST01", "text": "谢谢"},                 # 该不该答 → 不回
    {"external_userid": "wmCUST02", "text": "缺保安吗"},             # 知识库没有 → 转人工
    {"external_userid": "wmCUST03", "msgtype": "image"},            # 图片
    {"external_userid": "wmCUST03", "msgtype": "image"},            # 同一批第二张
    {"external_userid": "wmCUST04", "msgtype": "voice",
     "voice_text": "你们几点下班"},                                  # 语音（能转文字）
    {"external_userid": "wmCUST05", "msgtype": "voice"},            # 语音（转不出来）
    {"external_userid": "wmCUST06", "msgtype": "event", "code": "CODE1"},   # 进会话
]


class FakePending:
    def __init__(self):
        self.items = []

    def push(self, *a, **kw):
        self.items.append(a + ((kw.get("key_hint", ""),) if kw else ()))


async def main():
    srv = MockWeCom(script=SCRIPT).start()
    print(f"假企业微信: {srv.base_url}\n")
    # 每次都用新的状态文件，免得上一轮的游标把这一轮的消息吃掉
    state_path = os.path.join(HERE, "logs", f"_e2e_kf_state_{os.getpid()}.json")
    try:
        cli = WeComKFClient(corp_id="wwmock", secret="mocksecret",
                            open_kfid="wkMOCK", base=srv.base_url,
                            state_path=state_path)

        ok, detail = cli.selftest()
        print(f"1) 自检: {'✓' if ok else '✗'} {detail}")
        if not ok:
            return 1

        print("\n2) 拉消息（第一轮）")
        msgs = cli.sync_msg()
        for m in msgs:
            print(f"   {m.msgid}  from={m.external_userid}  昵称={cli.display_name(m.external_userid)}"
                  f"  {m.text!r}")

        print("\n3) 拉消息（第二轮，游标应已推进 → 不该重复拿到）")
        again = cli.sync_msg()
        print(f"   第二轮拿到 {len(again)} 条（期望 0）")

        print("\n4) 走真实 RAG + 分流（要加载 BGE 模型与知识库，稍等）")
        try:
            from rag.responder import Responder
            from config.manager import ConfigManager
            cfg = ConfigManager().config
            from wxbot.scanner import Scanner
            responder = Responder(Scanner(cfg), config=cfg)
        except Exception as e:
            print(f"   ✗ 初始化 Responder 失败（是不是客服程序还在跑、占着 data/qdrant？）: {e}")
            return 1

        send_q: queue.Queue = queue.Queue()
        pending = FakePending()
        nontext: dict = {}
        acks = ps.parse_nontext_acks()
        welcomed: dict = {}
        for m in msgs:
            nick = cli.display_name(m.external_userid)
            if m.is_event:
                # ── 进入会话 → 欢迎语（走 send_msg_on_event，要 code）──
                if should_welcome(m.external_userid, welcomed, 21600,
                                  code=m.event_code):
                    cli.send_welcome(m.event_code, ps.get("welcome").strip())
                    welcomed[m.external_userid] = __import__("time").time()
                    print(f"   [进入会话 {m.external_userid}] → 已发欢迎语")
                else:
                    print(f"   [进入会话 {m.external_userid}] → 跳过（刚招呼过/没 code）")
                continue
            if not m.has_text:
                nontext.setdefault(m.external_userid, []).append(m)
                continue
            # 有可用文字就走正常问答（语音转写出来的也算）
            out = await dispatch_api_message(
                responder, m.external_userid, m.text, send_q, pending,
                display_name=nick)
            tag = "语音转写" if m.from_voice else "文本"
            print(f"   [{tag} {m.text[:20]!r}] → {out.action}  理由={out.reason}")
            if out.hold_text:
                print(f"        占位语: {out.hold_text}")

        # ── 非文本：同一客户一批只回一次、只进一条待人工 ──
        for uid, items in nontext.items():
            kinds = "、".join(dict.fromkeys(i.kind_label for i in items))
            out = await dispatch_api_nontext(
                uid, items, send_q, pending, acks=acks,
                display_name=cli.display_name(uid))
            print(f"   [{kinds} ×{len(items)}] → {out.action}  应答={out.hold_text!r}")

        print("\n5) 把队列里的回复真的通过 API 发出去")
        while not send_q.empty():
            _, uid, text = send_q.get_nowait()
            cli.send_text(uid, text)
        for s in srv.sent_texts():
            print(f"   → {s['touser']}: {s['text']}")
        for w in srv.welcome_texts():
            print(f"   → 欢迎语(code={w['code']}): {w['text']}")

        print(f"\n6) 待人工队列: {len(pending.items)} 条")
        for it in pending.items:
            print(f"   {it[0]}: 客户说 {it[1]!r} / 草稿 {it[2][:40]!r} / 分 {it[3]}")

        print("\n结论：API 模式整条链路（自检 → 拉消息 → 去重游标 → 分流 → 回复）已跑通。")
        return 0
    finally:
        srv.stop()
        if os.path.exists(state_path):
            os.remove(state_path)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
