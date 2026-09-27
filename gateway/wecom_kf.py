# gateway/wecom_kf.py
"""企业微信「微信客服」API 客户端 —— 官方接口版的消息收发通道。

为什么是「微信客服」：它是企业微信官方唯一一套能让程序**读到微信用户发来的消息、
并以客服身份回复**的接口（客户联系/会话存档都做不到"回复"）。
对应关系：截图模式里的"客户"（会话名带 @微信 的那些），在 API 模式里就是
``external_userid``。

协议形状已实测确认（不需要凭据，故意用错参数打，返回企业微信标准错误 JSON）：
    GET  /cgi-bin/gettoken                      → errcode 40013 (invalid corpid)
    POST /cgi-bin/kf/sync_msg                   → errcode 40014 (invalid access_token)
    POST /cgi-bin/kf/send_msg                   → errcode 40014
    POST /cgi-bin/kf/account/list               → errcode 40014
    POST /cgi-bin/kf/service_state/get          → errcode 40014
复现：``python scripts/probe_wecom_api.py``

字段名按官方文档实现；**真实响应要你拿凭据跑一次 selftest 才能算数**
（``python scripts/wecom_api_selftest.py``）—— 那之前不要当成已验证。

凭据从环境变量/.env 读，不进 config.json、不进日志：
    WECOM_CORP_ID      企业 ID
    WECOM_KF_SECRET    微信客服专用 Secret（不是自建应用的 Secret）
    WECOM_OPEN_KFID    客服账号 ID，形如 wkxxxxxxxx
"""

from __future__ import annotations

import json
import logging
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

BASE = os.getenv("WECOM_API_BASE", "https://qyapi.weixin.qq.com")
PATH_GETTOKEN = "/cgi-bin/gettoken"
PATH_SYNC_MSG = "/cgi-bin/kf/sync_msg"
PATH_SEND_MSG = "/cgi-bin/kf/send_msg"
PATH_SEND_MSG_ON_EVENT = "/cgi-bin/kf/send_msg_on_event"
PATH_ACCOUNT_LIST = "/cgi-bin/kf/account/list"
PATH_SERVICE_STATE_GET = "/cgi-bin/kf/service_state/get"
PATH_CUSTOMER_BATCHGET = "/cgi-bin/kf/customer/batchget"

# sync_msg 的 origin 字段：谁发的
ORIGIN_CUSTOMER = 3     # 客户
ORIGIN_SYSTEM = 4       # 系统（进入会话等事件）
ORIGIN_STAFF = 5        # 接待人员（我们自己）

TOKEN_SAFETY_MARGIN = 300   # 提前 5 分钟续期

# 客户发来的不是文字时，给它一个好读的名字（转人工时告诉店主"客户发来的是图片"）
MSG_KIND_LABEL = {
    "image": "图片",
    "voice": "语音",
    "video": "视频",
    "file": "文件",
    "location": "位置",
    "link": "链接",
    "miniprogram": "小程序",
    "business_card": "名片",
    "channels": "视频号",
    "merged_msg": "聊天记录",
}

# 这些错误重试也没用（参数/权限问题，重试只是白等）
NON_RETRYABLE_ERRCODES = {
    40001, 40013, 40003, 40008, 40036, 60020, 60111, 95000,
    40056, 40097, 41001,
}


def public_ip(timeout: float = 6.0) -> str:
    """查本机出口公网 **IPv4**（配「可信 IP」要填它）。查不到返回空串。

    为什么要有这个：`60020 not allow to access from your ip` 是接入时最常见的坑，
    而且**这个 IP 会变**（家宽/公司网重启光猫、换网络就变），
    光说"去配可信 IP"没用，得告诉用户此刻该填哪个。

    **优先 IPv4**：微信客服的可信 IP 要的是 IPv4，而机器同时有 IPv6 时，
    Python 默认可能连到 IPv6 地址上去（实测踩到：查回来一串 `2409:...`，
    填进后台根本不认）。
    """
    for url in ("https://api4.ipify.org", "https://ipv4.icanhazip.com",
                "https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}),
                urllib.request.HTTPSHandler(context=ssl.create_default_context()))
            with opener.open(req, timeout=timeout) as r:
                ip = r.read().decode("utf-8", "replace").strip()
            if _looks_like_ip(ip):
                return ip
        except Exception:
            continue
    return ""


def _looks_like_ip(s: str) -> bool:
    s = (s or "").strip()
    if not s or len(s) > 45 or " " in s:
        return False
    parts = s.split(".")
    if len(parts) == 4:                      # IPv4 优先
        return all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)
    return ":" in s                          # 退而求其次才接受 IPv6


def is_placeholder(v: str) -> bool:
    """这个值是不是 .env.example 里那种占位符（没真填）。

    非技术用户最容易踩的坑：把 .env.example 复制成 .env 就以为配好了，
    而 `your_corp_id` 看起来"有个值"，自检就会一路往下走、报出更难懂的错。
    """
    s = (v or "").strip().lower()
    if not s:
        return True
    return (s.startswith("your") or s.startswith("your_")
            or s in {"xxx", "xxxx", "xx", "填这里", "todo", "changeme"}
            or set(s) <= {"x", "_", "-"})


class WeComKFError(Exception):
    """企业微信返回了非 0 的 errcode。"""

    def __init__(self, errcode: int, errmsg: str, path: str = ""):
        super().__init__(f"[{errcode}] {errmsg} ({path})")
        self.errcode = errcode
        self.errmsg = errmsg
        self.path = path


@dataclass
class IncomingMessage:
    """一条客户消息（只保留我们关心的字段）。

    非文本消息（图片/语音/文件/位置…）**不再丢掉**：客户发张器材照片问"这个有吗"，
    丢掉就等于让客户干等（原来就是这样，客户一个字都收不到）。
    这类消息会被转人工，并给客户回一句得体的话。
    """
    msgid: str
    open_kfid: str
    external_userid: str
    text: str
    send_time: int = 0
    msgtype: str = "text"
    event_type: str = ""       # 进入会话等事件（msgtype=event）
    event_code: str = ""       # 发欢迎语要用的一次性 code
    from_voice: bool = False   # 这条文本是语音转写来的

    @property
    def customer_key(self) -> str:
        """回复时用的收件人：微信客服就是 external_userid。"""
        return self.external_userid

    @property
    def is_text(self) -> bool:
        return self.msgtype == "text"

    @property
    def has_text(self) -> bool:
        """有没有**能拿来回答**的文字。

        语音转写成功时 ``msgtype`` 还是 voice，但文本能用 —— 分流该看这个，
        不是看类型。否则语音转出来的问题会被当成"听不清，麻烦打字"，
        ``voice_format=1`` 就白设了。
        """
        return bool((self.text or "").strip())

    @property
    def is_event(self) -> bool:
        return self.msgtype == "event"

    @property
    def kind_label(self) -> str:
        """给店主看的名字：图片 / 语音 / …"""
        return MSG_KIND_LABEL.get(self.msgtype, self.msgtype or "未知类型")

    @property
    def display_text(self) -> str:
        """待人工列表里显示的"客户消息"。非文本给个能看懂的说明。"""
        if self.is_text:
            return self.text
        if self.is_event:
            return "（客户进入会话）"
        return f"（客户发来{self.kind_label}）"


@dataclass
class WeComKFClient:
    corp_id: str = ""
    secret: str = ""
    open_kfid: str = ""
    state_path: str = "data/state/wecom_kf.json"
    timeout: float = 20.0
    base: str = ""
    _access_token: str = field(default="", init=False, repr=False)
    _token_expire_at: float = field(default=0.0, init=False, repr=False)
    _cursor: str = field(default="", init=False, repr=False)
    _name_cache: dict = field(default_factory=dict, init=False, repr=False)

    # ── 构造 ────────────────────────────────────────────────────────────

    @classmethod
    def from_env(cls, open_kfid: str = "", state_path: str = "data/state/wecom_kf.json",
                 timeout: float = 20.0) -> "WeComKFClient":
        """从环境变量/.env 读凭据。放这里而不是 config.json，避免密钥进版本库。"""
        return cls(
            corp_id=os.getenv("WECOM_CORP_ID", "").strip(),
            secret=os.getenv("WECOM_KF_SECRET", "").strip(),
            open_kfid=(open_kfid or os.getenv("WECOM_OPEN_KFID", "").strip()),
            state_path=state_path,
            timeout=timeout,
        )

    @classmethod
    def from_config(cls, cfg: dict) -> "WeComKFClient":
        api = (cfg or {}).get("api", {}).get("wecom", {})
        cli = cls.from_env(
            open_kfid=api.get("open_kfid", ""),
            state_path=api.get("state_path", "data/state/wecom_kf.json"),
            timeout=float(api.get("timeout_seconds", 20)),
        )
        cli._load_state()
        return cli

    def credentials_present(self) -> bool:
        """凭据齐了吗 —— **占位符不算数**（`your_corp_id` 这种等于没填）。"""
        return (bool(self.corp_id and self.secret)
                and not is_placeholder(self.corp_id)
                and not is_placeholder(self.secret))

    # ── 状态持久化（游标 + token）────────────────────────────────────────

    def _load_state(self):
        p = Path(self.state_path)
        if not p.exists():
            return
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            self._cursor = d.get("cursor", "")
            self._access_token = d.get("access_token", "")
            self._token_expire_at = float(d.get("expire_at", 0))
        except Exception as e:
            logger.warning(f"读取 {p} 失败（忽略，从零开始）: {e}")

    def _save_state(self):
        p = Path(self.state_path)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "cursor": self._cursor,
                "access_token": self._access_token,
                "expire_at": self._token_expire_at,
            }, ensure_ascii=False), encoding="utf-8")
            tmp.replace(p)
        except Exception as e:
            logger.warning(f"写入 {p} 失败: {e}")

    # ── HTTP ───────────────────────────────────────────────────────────

    def _call(self, path: str, body: Optional[dict] = None,
              params: Optional[dict] = None, raise_on_error: bool = True) -> dict:
        url = (self.base or BASE) + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data,
                                     method="POST" if data is not None else "GET")
        req.add_header("Content-Type", "application/json")
        # 不走环境里的 HTTP(S)_PROXY：实测这台机器直连 qyapi 就通，
        # 而系统里配的代理（gost）对 TLS 有干扰。
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()))
        try:
            with opener.open(req, timeout=self.timeout) as r:
                raw = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
        except Exception as e:
            raise WeComKFError(-1, f"网络失败: {type(e).__name__}: {e}", path) from e
        try:
            j = json.loads(raw)
        except Exception:
            raise WeComKFError(-1, f"响应不是 JSON: {raw[:200]!r}", path)
        errcode = int(j.get("errcode", 0))
        if errcode != 0 and raise_on_error:
            raise WeComKFError(errcode, str(j.get("errmsg", "")), path)
        return j

    # ── 鉴权 ───────────────────────────────────────────────────────────

    def token(self, force: bool = False) -> str:
        """取 access_token（带缓存 + 提前 5 分钟续期）。"""
        if not self.credentials_present():
            raise WeComKFError(-2, "缺少 WECOM_CORP_ID / WECOM_KF_SECRET", PATH_GETTOKEN)
        if (not force and self._access_token
                and time.time() < self._token_expire_at - TOKEN_SAFETY_MARGIN):
            return self._access_token
        j = self._call(PATH_GETTOKEN, params={"corpid": self.corp_id,
                                              "corpsecret": self.secret})
        self._access_token = j["access_token"]
        self._token_expire_at = time.time() + float(j.get("expires_in", 7200))
        self._save_state()
        logger.info(f"access_token 已刷新，{j.get('expires_in')}s 后过期")
        return self._access_token

    def _tokened(self, path: str, body: Optional[dict] = None) -> dict:
        """带 token 调用；token 失效（40014/42001）自动刷新重试一次。"""
        try:
            return self._call(path, body=body, params={"access_token": self.token()})
        except WeComKFError as e:
            if e.errcode in (40014, 42001, 40001):
                logger.info(f"token 失效({e.errcode})，刷新后重试一次")
                return self._call(path, body=body,
                                  params={"access_token": self.token(force=True)})
            raise

    # ── 业务接口 ────────────────────────────────────────────────────────

    def account_list(self) -> list:
        """客服账号列表（用来确认 open_kfid 填对没有）。"""
        j = self._tokened(PATH_ACCOUNT_LIST, body={})
        return j.get("account_list", []) or []

    def sync_msg(self, limit: int = 1000) -> list:
        """拉取新消息并推进游标。

        返回**客户发的一切**（文本 / 图片 / 语音 / 文件…）和**系统事件**
        （进入会话）。接待人员自己发的、以及我们认不出的类型丢弃。

        ``voice_format=1`` 是请服务端把语音转成文字 —— 转出来就能当文本正常回答
        （转不出来就退回"发来语音"的转人工路径，不会让客户干等）。
        """
        body = {"cursor": self._cursor, "limit": limit, "voice_format": 1}
        if self.open_kfid:
            body["open_kfid"] = self.open_kfid
        j = self._tokened(PATH_SYNC_MSG, body=body)
        out = []
        for m in j.get("msg_list", []) or []:
            origin = int(m.get("origin", 0))
            msgtype = str(m.get("msgtype") or "text")
            uid = str(m.get("external_userid", ""))
            msgid = str(m.get("msgid", ""))

            # ── 系统事件：客户进入会话（发欢迎语的机会）──
            if msgtype == "event" or origin == ORIGIN_SYSTEM:
                ev = m.get("event") or {}
                etype = str(ev.get("event_type") or "")
                if etype == "enter_session":
                    out.append(IncomingMessage(
                        msgid=msgid, open_kfid=str(m.get("open_kfid", "")),
                        external_userid=uid, text="",
                        send_time=int(m.get("send_time", 0) or 0),
                        msgtype="event", event_type=etype,
                        event_code=str(ev.get("code") or ""),
                    ))
                continue
            if origin != ORIGIN_CUSTOMER:
                continue           # 接待人员自己发的 / 其它

            # ── 客户消息 ──
            text, from_voice = "", False
            if msgtype == "text":
                text = ((m.get("text") or {}).get("content") or "").strip()
            elif msgtype == "voice":
                # voice_format=1 时服务端会给转写文本；没有就空着（走转人工）
                text = ((m.get("voice") or {}).get("text")
                        or (m.get("voice") or {}).get("content") or "").strip()
                from_voice = bool(text)
            if msgtype == "text" and not text:
                continue
            out.append(IncomingMessage(
                msgid=msgid, open_kfid=str(m.get("open_kfid", "")),
                external_userid=uid, text=text,
                send_time=int(m.get("send_time", 0) or 0),
                msgtype=msgtype, from_voice=from_voice,
            ))
        if j.get("next_cursor") is not None:
            self._cursor = str(j["next_cursor"])
            self._save_state()
        if j.get("has_more"):
            logger.info("sync_msg 还有更多消息，下一轮继续拉")
        return out

    def _send(self, path: str, body: dict, retries: int = 3) -> dict:
        """发送类接口的**重试**：接口偶发失败（-1 系统繁忙 / 45009 频率超限 /
        网络抖动）时重试，客户才不会白等。参数或权限错了就不重试，重试也没用。"""
        last: Optional[WeComKFError] = None
        for attempt in range(1, max(1, retries) + 1):
            try:
                return self._tokened(path, body=body)
            except WeComKFError as e:
                last = e
                if e.errcode in NON_RETRYABLE_ERRCODES:
                    logger.error(f"发送失败且不该重试（{e.errcode}: {e.errmsg}）")
                    raise
                if attempt >= retries:
                    break
                wait = 0.8 * attempt
                logger.warning(f"发送失败（{e.errcode}: {e.errmsg}），"
                               f"{wait:.1f}s 后重试（第 {attempt}/{retries - 1} 次）")
                time.sleep(wait)
        raise last if last else WeComKFError(-1, "发送失败", path)

    def send_text(self, external_userid: str, content: str,
                  retries: int = 3) -> dict:
        """以客服身份回复客户（带重试）。"""
        body = {
            "touser": external_userid,
            "msgtype": "text",
            "text": {"content": content},
        }
        if self.open_kfid:
            body["open_kfid"] = self.open_kfid
        return self._send(PATH_SEND_MSG, body, retries=retries)

    def send_welcome(self, code: str, content: str, retries: int = 2) -> dict:
        """给刚进入会话的客户发欢迎语。

        ``code`` 来自 enter_session 事件，**只能用一次、且很快过期**，
        所以拉到事件就要马上发（轮询间隔 3 秒，够用）。
        """
        body = {
            "code": code,
            "msgtype": "text",
            "text": {"content": content},
        }
        return self._send(PATH_SEND_MSG_ON_EVENT, body, retries=retries)

    def service_state(self, external_userid: str) -> dict:
        body = {"open_kfid": self.open_kfid, "external_userid": external_userid}
        return self._tokened(PATH_SERVICE_STATE_GET, body=body)

    def display_name(self, external_userid: str) -> str:
        """客户昵称（拿不到就退回 id）。结果缓存在内存里。"""
        if not external_userid:
            return ""
        if external_userid in self._name_cache:
            return self._name_cache[external_userid]
        name = external_userid
        try:
            j = self._tokened(PATH_CUSTOMER_BATCHGET,
                              body={"external_userid": [external_userid]})
            for c in j.get("customer_list", []) or []:
                if c.get("nickname"):
                    name = str(c["nickname"])
                    break
        except WeComKFError as e:
            logger.debug(f"取客户昵称失败（用 id 代替）: {e}")
        self._name_cache[external_userid] = name
        return name

    # ── 自检 ───────────────────────────────────────────────────────────

    def selftest(self) -> tuple:
        """连通性自检 → (ok, 人话说明)。不需要企业微信桌面端。"""
        if not self.credentials_present():
            bad = [n for n, v in (("WECOM_CORP_ID", self.corp_id),
                                  ("WECOM_KF_SECRET", self.secret))
                   if not v or is_placeholder(v)]
            if bad and any(v and is_placeholder(v)
                           for v in (self.corp_id, self.secret)):
                return False, (f"{'、'.join(bad)} 现在还是 .env.example 里的**占位符**，"
                               f"不是真凭据。请在「设置 → 软件选企业微信·API 模式」里填："
                               f"企业 ID / 客服 Secret（微信客服那栏的，不是自建应用的）/ "
                               f"客服账号 ID")
            return False, ("缺少凭据：请在 .env 里填 WECOM_CORP_ID 与 WECOM_KF_SECRET"
                           "（微信客服 Secret，不是自建应用 Secret）")
        try:
            self.token(force=True)
        except WeComKFError as e:
            hint = {
                40013: "WECOM_CORP_ID 不对",
                40001: "WECOM_KF_SECRET 不对（注意要用【微信客服】那栏的 Secret，不是自建应用的）",
                60020: "",
            }.get(e.errcode, "")
            if e.errcode == 60020:
                # 最常见的坑，而且这个 IP 会变 —— 直接把"该填哪个"查出来告诉他
                ip = public_ip()
                hint = ("这台电脑的出口公网 IP " + (f"是 {ip}，" if ip else "没能自动查到，")
                        + "要去微信客服后台的「API」页把它加进「可信 IP」"
                          "（家宽/公司网重启后 IP 可能变，变了要重新加）")
            return False, f"取 access_token 失败：{e.errmsg}（errcode={e.errcode}）{hint}"
        try:
            accounts = self.account_list()
        except WeComKFError as e:
            hint = {
                60020: (lambda: ("这台电脑的出口公网 IP "
                                 + (public_ip() or "没能自动查到")
                                 + "，要去微信客服后台「API」→「可信 IP」加进去"))(),
                48002: "这个 Secret 对应的应用没有微信客服权限："
                       "去微信客服后台「API」把它设成「可调用接口的应用」",
                60011: "没有权限调用微信客服接口："
                       "去微信客服后台「API」把对应应用设成「可调用接口的应用」",
                40001: "Secret 不对（注意：微信客服 API 可能要的是"
                       "被指定为「可调用接口的应用」那个自建应用的 Secret）",
            }.get(e.errcode, "")
            return False, (f"account/list 失败：{e.errmsg}（errcode={e.errcode}）{hint}")
        if not accounts:
            return False, ("没有可用的客服账号：请在企业微信后台 → 应用管理 → 微信客服"
                           "里创建/启用一个客服账号，并把它的 open_kfid 填进 WECOM_OPEN_KFID")
        kids = [a.get("open_kfid") for a in accounts]
        # 把真实可用的 open_kfid 打出来 —— 后台界面上显示的"账号ID"不一定就是它
        # （实测有人拿到的是 kf 开头的一串，而接口要的是 wk 开头的）
        names = "、".join(f"{a.get('open_kfid')}（{a.get('name', '')}）"
                         for a in accounts)
        if self.open_kfid and self.open_kfid not in kids:
            return False, (f"WECOM_OPEN_KFID={self.open_kfid} 不在你的客服账号列表里。"
                           f"**该填的是**：{names}")
        if not self.open_kfid and len(kids) == 1:
            self.open_kfid = kids[0]
            logger.info(f"自动选用唯一客服账号 open_kfid={self.open_kfid}")
        try:
            # 自检绝不能吃掉客户消息：sync_msg 会推进并落盘游标，
            # 直接调会把第一条真实消息永久跳过（端到端验证时真踩到了）。
            # 这里先存下游标，探一下就还原。
            saved_cursor = self._cursor
            try:
                msgs = self.sync_msg(limit=1)
            finally:
                self._cursor = saved_cursor
                self._save_state()
        except WeComKFError as e:
            return False, f"sync_msg 失败：{e.errmsg}（errcode={e.errcode}）"
        return True, (f"OK：token 可用、客服账号 {names}、"
                      f"sync_msg 通（本轮探到 {len(msgs)} 条客户消息，游标未推进）")
