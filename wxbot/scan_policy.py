# wxbot/scan_policy.py
"""扫描时的**逐行取舍**策略 —— 抽成纯函数，好测，也防止再出"乱点会话"的事故。

背景（2026-09-25 真实事故）：切到微信 PC 模式后，机器人把个人微信里
朋友/群/营销号全判成客户，一轮点开了 6 个会话（点开会话＝清掉对方未读，不可逆），
并自动发了一条占位语到一个群聊里。

那次的根因是两层叠加：
  1. 客户判定对个人微信太宽（"不是系统号就算客户"）
  2. 行几何用的是企业微信的值（行高 94 vs 微信 98）→ 读到的名字和实际行不对应

这里把"这一行要不要点开"独立出来，规则显式化：
  * 不是客户 → 不点
  * 该客户刚回过话（冷却中）→ 不点
  * 启用了白名单而该客户不在名单里 → **不点**（自定义软件的默认：空名单＝谁都不点）
只有全部通过才允许点开。点开是有代价的动作，值得单独把关。
"""

from __future__ import annotations

from typing import Optional, Set, Tuple

SKIP_NOT_CUSTOMER = "not_customer"
SKIP_COOLDOWN = "customer_cooldown"
SKIP_NOT_IN_WHITELIST = "not_in_whitelist"
OK = "ok"


def row_eligibility(is_customer: bool, customer_key: str,
                    whitelist: Optional[Set[str]] = None,
                    in_cooldown: bool = False) -> Tuple[bool, str]:
    """这一行值不值得**点开**看聊天区。

    Args:
        is_customer: 会话名判定结果。
        customer_key: 清理后的会话名（白名单与冷却都按它比对）。
        whitelist: ``None`` = 不启用白名单；空集合 = 谁都不允许。
        in_cooldown: 这个客户是否刚被回复过。

    Returns:
        ``(是否点开, 原因)``。原因用于日志与测试断言。
    """
    if not is_customer:
        return False, SKIP_NOT_CUSTOMER
    if customer_key and in_cooldown:
        return False, SKIP_COOLDOWN
    if whitelist is not None and customer_key not in whitelist:
        # 自定义软件（个人微信）里"哪些会话算客户"必须用户指定 ——
        # 宁可什么都不做，也不要去猜：点开会话会清掉对方未读，不可逆。
        return False, SKIP_NOT_IN_WHITELIST
    return True, OK


def parse_whitelist(customers_cfg: dict) -> Optional[Set[str]]:
    """从 config 的 ``customers`` 段解析白名单。

    ``require_whitelist`` 为假 → 返回 ``None``（不启用）。
    为真 → 返回集合（可能是空集合，含义是"谁都不点"）。
    """
    cfg = customers_cfg or {}
    if not cfg.get("require_whitelist"):
        return None
    out = set()
    for item in (cfg.get("whitelist") or []):
        if item is None:            # 别把 None 变成字符串 'None' 混进名单
            continue
        name = str(item).strip()
        if name:
            out.add(name)
    return out
