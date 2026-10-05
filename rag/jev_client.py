# rag/jev_client.py
"""Jev（TypeSafe 决策模型）的极简客户端 —— **只用于「该不该答」这一个判断点**。

## 它和生成模型有什么不一样

Jev 是**判断模型**：只回答**选择题 / 打分 / 是非**，返回概率，**不生成文字**。
答案只有三种形状（照着协议抄的，不是我编的）：

    noul   → {"type": "noul",   "noul": 0.9}                      命题为真的程度（当概率用）
    choice → {"type": "choice", "choice": "request_price",
              "confidence": 0.7, "probabilities": {...}}
    score  → {"type": "score",  "score": 4.0, "confidence": 0.6,
              "probabilities": {"4": 0.6, "5": 0.4}}

## 请求形状（官方 SKILL.md 的最小调用）

    POST https://jev.bocha.cn/v1/systemone
    Authorization: Bearer <key>
    {"model": "bocha-jev-v1", "state": <字符串/对象/数组>, "questions": {...}}

几处**官方明确写死的约束**（踩了就 422，不会自动截断）：

* `state` 是**自由格式**（字符串 / 对象 / 数组都行）——放对话上下文。
* `questions` 由调用方自定义 id 键；每题 `{type, instructions, criteria}`。
* 一请求 **1~32 题**，候选总数 ≤ 1024（每个 noul 算两个候选）。
* **单题**输入 ≤ 32768 token（共享的 state + instructions + 全部候选**加起来**算这一题）。
* instructions ≤ 8192 字符。
* **不允许 `temperature` / `stream` 等未识别字段**（会被拒）。
* `noul` 是**为真的概率**（0~1），不是布尔；`score` 是**零基下标的概率加权**，不是档位数字。
* 响应顶层是 `model / answers / usage / metadata`，**没有 code/msg/data 外壳**。
* 401 查 key；413/422 先改大小/类型再重试；429/503/529 按 `Retry-After` 退避。

## 凭据（官方文档的变量名）

    首选 BOCHA_JEV_API_KEY
    其次 BOCHA_SEARCH_API_KEY（已有博查 key 且有权访问该服务时可复用）
    兼容 JEV_API_KEY（Jev 聊天助手那个客户端用的名字）

注意 `JEV_UPSTREAM_API_KEY` 是**服务端内部凭据**，不是给客户端用的。

## 设计取舍（都是刻意的）

1. **只用标准库 urllib**，不引入 `typesafe_sdk` / `requests`：分发包里已经 1.6GB，
   为一个可选功能再加依赖不值得；而且 urllib 不用管 TLS 证书路径。
2. **绝不把 key 写进日志/异常**：任何要落盘的字符串都过 `redact_secrets()`。
3. **这是"可选增强"，不是关键路径**：所有失败都抛 `JevError` 出去，
   由 `rag/jev_judge.py` 兜住并**退回规则层**——判断模型挂了不能影响机器人接待。
"""

from __future__ import annotations

import json
import logging
import os
import socket
import time
import urllib.error
import urllib.request
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 6.0          # 客户在等，判断不能慢；超过就退回规则层
MAX_RETRIES = 2                # 官方建议"至多两次"重试
RETRY_STATUS = (429, 503, 529)  # 限流 / 不可用 / 过载

# 凭据变量名，**按优先级排**（官方 SKILL.md 的 Configure access 一节）：
#   首选 BOCHA_JEV_API_KEY；已有博查 key 有权访问时可复用 BOCHA_SEARCH_API_KEY；
#   最后兼容 Jev 聊天助手客户端用的 JEV_API_KEY。
ENV_KEYS = ("BOCHA_JEV_API_KEY", "BOCHA_SEARCH_API_KEY", "JEV_API_KEY")
ENV_KEY = ENV_KEYS[-1]         # 兼容旧引用（日志/测试里用得到）

# 各家托管（协议相同，只有 base + 路径 + 默认模型不同）。
# 来源：Jev 聊天助手 README / CHANGELOG 的"接口与模型"一节；OpenRouter 那条的路径确实不一样
# （SDK 把路径写死成 /v1/systemone，打不到 OpenRouter 的 /api/alpha/decisions）。
_PROVIDER = Tuple[str, str, str, str]      # (显示名, base_url, path, 默认模型)
PROVIDERS: dict = {
    "bocha":      ("博查 Jev（限时免费）", "https://jev.bocha.cn",
                   "/v1/systemone", "bocha-jev-v1"),
    "typesafe":   ("TypeSafe 直连", "https://api.typesafe.ai",
                   "/v1/systemone", "jev-latest"),
    "vercel":     ("Vercel AI Gateway", "https://ai-gateway.vercel.sh/typesafe",
                   "/v1/systemone", "typesafe-ai/jev"),
    "opencode":   ("OpenCode Zen", "https://opencode.ai/zen",
                   "/v1/systemone", "jev-1.13"),
    "openrouter": ("OpenRouter", "https://openrouter.ai",
                   "/api/alpha/decisions", "typesafe/jev-1.13"),
}
DEFAULT_PROVIDER = "bocha"                 # 限时免费，拿来做灰度实验成本最低


class JevError(Exception):
    """判断调用失败。**调用方必须捕获它并退回规则层**，不要让它冒到主链路。"""

    def __init__(self, message: str, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


def key() -> str:
    """按优先级取一把可用的凭据（都没有就空字符串）。"""
    for name in ENV_KEYS:
        v = (os.environ.get(name) or "").strip()
        if v:
            return v
    return ""


def redact_secrets(text) -> str:
    """把 key 从任何要落盘的字符串里抹掉（异常、响应体、日志都过这里）。"""
    if not isinstance(text, str):
        text = str(text)
    for name in ENV_KEYS:
        k = (os.environ.get(name) or "").strip()
        if k:
            text = text.replace(k, "[REDACTED]")
    return text


def provider_spec(name: str) -> _PROVIDER:
    return PROVIDERS.get((name or "").strip().lower()) or PROVIDERS[DEFAULT_PROVIDER]


def has_key() -> bool:
    """有没有配 key —— 没有就别浪费时间试（灰度/影子模式会据此自动跳过）。"""
    return bool(key())


def resolve_config(cfg: dict = None) -> Tuple[str, str, str, str]:
    """返回 (provider, base_url, path, model)。

    优先级：`.env` 的环境变量 > config.json 的 judge.jev 段 > 内置预设。
    这样"临时换一家试"不用改 config.json（改 .env 就行），而长期设置留在 config。
    """
    jcfg = ((cfg or {}).get("judge", {}) or {}).get("jev", {}) or {}
    name = (os.environ.get("JEV_PROVIDER") or jcfg.get("provider")
            or DEFAULT_PROVIDER).strip().lower()
    disp, base, path, model = provider_spec(name)
    base = (os.environ.get("JEV_BASE_URL") or jcfg.get("base_url") or base).rstrip("/")
    model = (os.environ.get("JEV_MODEL") or jcfg.get("model") or model).strip()
    return name, base, path, model


def timeout_of(cfg: dict = None) -> float:
    jcfg = ((cfg or {}).get("judge", {}) or {}).get("jev", {}) or {}
    try:
        return max(2.0, min(30.0, float(jcfg.get("timeout_seconds", DEFAULT_TIMEOUT))))
    except Exception:
        return DEFAULT_TIMEOUT


def _error_body(exc: urllib.error.HTTPError) -> str:
    try:
        raw = exc.read().decode("utf-8", errors="replace")
    except Exception:
        raw = ""
    return redact_secrets(raw)[:400]


def _status_of(exc: Exception) -> Optional[int]:
    for name in ("status_code", "code", "status"):
        v = getattr(exc, name, None)
        if isinstance(v, int):
            return v
    return None


def _one_shot(url: str, payload: bytes, key: str, timeout: float) -> dict:
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            # 与官方最小示例保持一致（不带 charset，免得严格校验的网关挑刺）
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:   # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _retry_after(exc: urllib.error.HTTPError, default: float) -> float:
    """官方要求 429/503/529 按 Retry-After 退避；没给就用默认。"""
    try:
        raw = (exc.headers.get("Retry-After") or "").strip()
        return max(0.0, min(10.0, float(raw))) if raw else default
    except Exception:
        return default


def ask(state, questions: dict, cfg: dict = None,
        timeout: float = None) -> dict:
    """问 Jev 一轮判断 → ``{"answers": {题目名: 答案}, "usage": {...}, ...}``。

    `state` 是**自由格式**（字符串 / 对象 / 数组都行），按官方 SKILL.md。

    **同步阻塞**（几秒）。在 asyncio 里请用 `ask_async()`，别卡住事件循环。
    失败一律抛 `JevError`，调用方负责降级。
    """
    name, base, path, model = resolve_config(cfg)
    api_key = key()
    if not api_key:
        raise JevError(f"没配凭据（{' / '.join(ENV_KEYS)} 都没值），判断调用跳过")
    timeout = timeout or timeout_of(cfg)
    url = f"{base}{path}"
    payload = json.dumps(
        {"model": model, "state": state, "questions": questions},
        ensure_ascii=False).encode("utf-8")

    # 官方限了请求体 256 KiB：超了先自己拦（避免 422 白白等一轮）
    if len(payload) > 262144:
        raise JevError(f"请求体 {len(payload)} 字节超官方上限 256 KiB —— 少带点上下文")

    last_err = None
    for attempt in range(MAX_RETRIES + 1):
        started = time.time()
        try:
            data = _one_shot(url, payload, api_key, timeout)
            logger.info("Jev 判断成功: %s/%s %.2fs",
                        name, model, time.time() - started)
            return data
        except urllib.error.HTTPError as e:
            status = e.code
            body = _error_body(e)
            hint = {401: "密钥被拒（检查 BOCHA_JEV_API_KEY）", 403: "没有权限",
                    404: "模型或地址不对",
                    413: "请求体太大", 422: "请求被拒（题目/state 形状或大小不对）",
                    429: "被限流", 503: "服务不可用", 529: "服务过载"}.get(status, "")
            last_err = JevError(f"Jev HTTP {status}: {hint or body[:200]}", status)
            # 官方：只对 429/503/529 按 Retry-After 退避重试；参数错重试没用
            if status in RETRY_STATUS and attempt < MAX_RETRIES:
                time.sleep(_retry_after(e, 0.5 * (2 ** attempt)))
                continue
            raise last_err from None
        except (TimeoutError, socket.timeout):
            last_err = JevError(f"Jev 判断超时（{timeout:.0f} 秒）—— 退回规则层")
            if attempt < MAX_RETRIES:
                continue
            raise last_err from None
        except urllib.error.URLError as e:
            reason = redact_secrets(getattr(e, "reason", e))
            last_err = JevError(f"Jev 连不上: {reason}")
            if attempt < MAX_RETRIES:
                continue
            raise last_err from None
        except json.JSONDecodeError as e:
            raise JevError(f"Jev 返回的不是 JSON: {e}") from None
    raise last_err or JevError("Jev 调用失败")


async def ask_async(state, questions: dict, cfg: dict = None) -> dict:
    """给 asyncio 用的包装：放到线程里跑，别阻塞主循环。"""
    import asyncio
    return await asyncio.to_thread(ask, state, questions, cfg)


def ping(cfg: dict = None) -> Tuple[bool, str]:
    """连通性自检 → (是否通, 人话说明)。给脚本和面试演示用。

    用一道最便宜的 noul 题探活：既验证 key，也验证题目形状能不能被接受。
    """
    if not has_key():
        return False, "没配凭据（BOCHA_JEV_API_KEY / BOCHA_SEARCH_API_KEY / JEV_API_KEY）"
    name, base, _path, model = resolve_config(cfg)
    probe = {"ping": {
        "type": "noul",
        "instructions": "Is this a connectivity probe? Answer true.",
        "criteria": {"true": "Always true: this is a probe.",
                     "false": "Never choose this."},
    }}
    state = {"chat": {"relationship": "connectivity probe",
                      "messages": [{"from": "her", "text": "ping"}],
                      "latest_from": "her", "is_group": False}}
    try:
        got = ask(state, probe, cfg)
    except JevError as e:
        return False, f"{name} 不通：{e}"
    except Exception as e:                      # 兜底：绝不把异常抛给界面
        return False, f"{name} 不通：{type(e).__name__}: {redact_secrets(e)}"
    ans = (got.get("answers") or {}).get("ping") or {}
    if not ans:
        return False, f"{name} 通了但没返回答案（题目名或响应结构不对）"
    return True, f"{name} · {model} 通了（noul={ans.get('noul')}）"
