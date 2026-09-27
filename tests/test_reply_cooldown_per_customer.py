"""按客户的回复冷却测试。

原来的问题是**全局** 30 秒回复冷却：给 A 回完话，同一个时间窗里 B 发来的消息
会被兜底扫描整整挡掉 30 秒（多客户场景漏人）。改成按客户记。
"""
import time
from pathlib import Path

import pytest

from wxbot.detector import MessageDetector


def _detector(**dedup):
    cfg = {"dedup": dedup} if dedup else {}
    return MessageDetector(cfg)


def test_reply_cooldown_is_per_customer():
    d = _detector()
    d.mark_replied("客户A")

    assert d.is_in_cooldown("客户A") is True
    assert d.is_in_cooldown("客户B") is False, "给 A 回话不该挡住 B"


def test_global_cooldown_still_works_without_name():
    d = _detector()
    d.mark_replied("客户A")
    assert d.is_in_cooldown() is True, "不传名字时保持旧的全局语义"


def test_cooldown_expires_per_customer():
    d = _detector(reply_cooldown_seconds=0.01) if False else _detector()
    d._cooldown = 0.05
    d.mark_replied("客户A")
    assert d.is_in_cooldown("客户A") is True
    time.sleep(0.08)
    assert d.is_in_cooldown("客户A") is False


def test_mark_replied_without_name_does_not_block_everyone():
    """不带名字的标记只更新全局时间，不该让所有客户都进冷却。"""
    d = _detector()
    d.mark_replied()
    assert d.is_in_cooldown("客户A") is False


def test_unknown_customer_is_never_in_cooldown():
    d = _detector()
    d.mark_replied("客户A")
    assert d.is_in_cooldown("从没回过的客户") is False


def test_main_fallback_is_not_gated_by_global_cooldown():
    """兜底扫描不能再用全局冷却挡住整轮；改为按客户跳过。"""
    src = Path("main.py").read_text(encoding="utf-8")
    assert "if not replied and not detector.is_in_cooldown()" not in src, \
        "兜底不该被全局冷却挡掉"
    assert "detector.is_in_cooldown(cust_key)" in src, \
        "兜底要按客户跳过冷却中的那一个"
