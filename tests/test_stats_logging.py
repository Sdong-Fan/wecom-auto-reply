# tests/test_stats_logging.py
"""运营看板的前置日志改造（2026-10-03）。

为什么要有这些测试：看板的「客户问题分布」「护栏拦下了什么」全都依赖日志字段。
改造前 `log_send_result` 把 customer_message / guard_decision / guard_reason 写成空串，
自动发出去的消息**客户问了什么从来没落盘**——数字缺一半，而且没人会发现。
所以这里把字段钉死。
"""

import json
from pathlib import Path

import pytest

from rag.responder import ReplyResult, Responder, decision_path_of


class _FakeResponder:
    """只带 _log_path 的壳：log_send_result / _log_reply 只用到它。"""

    def __init__(self, path: Path):
        self._log_path = path


# ── decision_path：把内部字段翻译成看板能分组的短标签 ──────────────────

@pytest.mark.parametrize("guard,reason,level,extra,expect", [
    ("pass", "all_checks_passed", "auto_send", "", "正常直答"),
    ("must_escalate", "议价加码", "human_handle", "", "议价加码"),
    ("must_escalate", "投诉纠纷", "human_handle", "", "退款与投诉"),
    ("must_escalate", "库存与档期", "human_handle", "", "库存与档期"),
    ("must_escalate", "指代不明", "human_handle", "", "指代不明"),
    ("must_escalate", "政策未覆盖", "human_handle", "", "政策未覆盖"),
    ("unsafe_promise", "越权承诺（时效承诺）", "human_handle", "", "越权承诺"),
    ("deferral_phrase", "拖延话术（晚点回）", "human_handle", "", "拖延话术"),
    ("out_of_scope", "越界请求（要凭据）", "auto_send", "", "越界婉拒"),
    ("smalltalk", "非业务闲聊（店员视角）", "auto_send", "", "闲聊"),
    ("local_answer", "本地直答（系统时钟）", "auto_send", "", "本地直答"),
    ("", "", "no_reply", "", "无信息量/收尾"),
    ("low_risk", "llm_requests_human", "human_handle", "", "模型要人工"),
    ("pass", "all_checks_passed", "human_handle", "置信度不足(0.52)", "置信度不足"),
    ("block", "retrieval_low_confidence: low", "human_handle", "", "置信度不足"),
])
def test_decision_path_mapping(guard, reason, level, extra, expect):
    assert decision_path_of(guard, reason, level, extra) == expect


def test_decision_path_never_empty():
    """归一化必须总有输出 —— 看板要按它分组，空值会变成"未分类"黑洞。"""
    assert decision_path_of("", "", "", "") != ""


# ── log_send_result：自动发也要记客户原话与护栏信息 ────────────────────

def test_log_send_result_records_customer_message_and_guard(tmp_path):
    log = tmp_path / "auto_replies.jsonl"
    r = _FakeResponder(log)
    Responder.log_send_result(
        r, "客户A", "A7M4 日租 90", True,
        customer_message="A7M4多少钱一天",
        guard_decision="pass", guard_reason="all_checks_passed",
        decision_path="正常直答", source="auto")

    row = json.loads(log.read_text(encoding="utf-8").strip())
    assert row["customer_message"] == "A7M4多少钱一天"   # ★ 改造前这里是空串
    assert row["guard_decision"] == "pass"
    assert row["guard_reason"] == "all_checks_passed"
    assert row["decision_path"] == "正常直答"
    assert row["source"] == "auto"
    assert row["sent"] is True
    assert row["dispatch_level"] == "auto_send"


def test_log_send_result_source_distinguishes_human(tmp_path):
    """人工直发/改稿发的回复不能被算成"机器人自动解决"。"""
    log = tmp_path / "auto_replies.jsonl"
    r = _FakeResponder(log)
    Responder.log_send_result(r, "客户B", "人工写的回复", True,
                              customer_message="问一句", source="human_edit",
                              decision_path="人工改稿后发出")
    row = json.loads(log.read_text(encoding="utf-8").strip())
    assert row["source"] == "human_edit"
    assert row["decision_path"] == "人工改稿后发出"


def test_log_send_result_backward_compatible(tmp_path):
    """老调用方式（3 个参数）还能用，decision_path 自动兜底。"""
    log = tmp_path / "auto_replies.jsonl"
    r = _FakeResponder(log)
    Responder.log_send_result(r, "客户C", "嗯嗯", True)
    row = json.loads(log.read_text(encoding="utf-8").strip())
    assert row["customer_message"] == ""
    assert row["decision_path"]          # 不为空
    assert row["source"] == "auto"


def test_log_send_result_truncates_long_message(tmp_path):
    log = tmp_path / "auto_replies.jsonl"
    r = _FakeResponder(log)
    Responder.log_send_result(r, "客户D", "回复", True,
                              customer_message="问" * 900)
    row = json.loads(log.read_text(encoding="utf-8").strip())
    assert len(row["customer_message"]) == 500


# ── _log_reply：转人工/不回的行也要带 decision_path ────────────────────

def test_log_reply_has_decision_path(tmp_path):
    log = tmp_path / "auto_replies.jsonl"
    r = _FakeResponder(log)
    result = ReplyResult(success=False, reason="议价加码", escalated=True,
                         dispatch_level="human_handle",
                         guard_decision="must_escalate", guard_reason="议价加码")
    Responder._log_reply(r, "客户E", "能不能便宜点", result)
    row = json.loads(log.read_text(encoding="utf-8").strip())
    assert row["decision_path"] == "议价加码"
    assert row["customer_message"] == "能不能便宜点"
    assert row["dispatch_level"] == "human_handle"


# ── 发送队列把日志上下文透传到落盘（源码级，避免"改了消费端忘了生产端"）──

def test_send_queue_carries_log_context():
    src = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")
    assert 'log_ctx = row[4] if len(row) > 4 else None' in src, "消费端要读第 5 项"
    assert '**(log_ctx or {})' in src, "要透传给 log_send_result"
    assert '"source": "auto"' in src, "自动发要标 source=auto"
    assert '"source": "hold"' in src, "转人工占位语要标 source=hold"
    assert '"source": "human_edit"' in src, "人工改稿要标 source=human_edit"
    assert '"source": "human_confirm"' in src, "人工直发草稿要标 source=human_confirm"


def test_greeting_is_marked_as_greeting():
    """欢迎语不是"回答客户问题"，看板要能把它单独拎出来。"""
    src = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")
    assert 'source="greeting"' in src
