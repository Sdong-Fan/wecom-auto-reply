"""回复冷却必须够短 —— 客户连发几条时不能干等。

2026-09-25 现场（日志）：
    00:45:22 转人工: msg1(招保安吗) → 已回复: 帮您问下招不招保安，稍等。
    00:45:24 兜底行0 客户A@微信 回复冷却中，跳过
    00:45:27 兜底行0 客户A@微信 回复冷却中，跳过
    00:45:42 兜底行0 客户A@微信 回复冷却中，跳过
    00:46:12 兜底行0 [客户A@微信] 客户=True        ← 冷却过了才去看
    00:46:14 兜底y=129 OCR: 在吗 给保安拍照有什么推荐吗  ← msg2+msg3 一起读到
    00:46:16 回复: 帮您问下给保安拍照的推荐，稍等。

`reply_cooldown_seconds` 是**按客户**记的，30 秒里 msg2 来了也完全不看；
等冷却过了，msg2 和 msg3 已经被合并成一条一起答 —— 客户体验是
"先回第一条、半天不理、然后一次性回第二三条"。

冷却的初衷只是"别在我们自己的回复还没渲染完时就重扫同一批消息"，
而**消息级去重**（is_message_seen + mark_text_sent）已经负责防重复回复，
那条还有长度比例保护（ratio >= 0.6），不会把"msg1"和"msg1 msg2"误判成同一条。
所以冷却可以很短。
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _cfg():
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def test_reply_cooldown_is_short():
    v = _cfg().get("reply_cooldown_seconds")
    assert v is not None
    assert v <= 10, (
        f"回复冷却是按客户记的，{v} 秒意味着客户连发几条时后面几条会一直干等；"
        f"防重复回复由消息级去重负责，这里保持 <= 10 秒即可")


def test_click_cooldown_bigger_than_reply_cooldown():
    """行点击冷却（防重复 OCR 同一行）应当明显大于回复冷却，两者职责不同。"""
    c = _cfg()
    assert c["dedup"]["fallback_click_cooldown_seconds"] > c["reply_cooldown_seconds"]


def test_message_level_dedup_still_guards_duplicates():
    """冷却砍短的前提：消息级去重还在，且带长度比例保护。"""
    from wxbot.detector import MessageDetector
    d = MessageDetector({"reply_cooldown_seconds": 5})
    d.mark_message_seen("张三", "招保安吗")
    assert d.is_message_seen("张三", "招保安吗") is True, "同一条消息仍然要去重"
    # 客户又在后面补了一句 → 合并文本长得多，不该被当成重复
    assert d.is_message_seen("张三", "招保安吗 给保安拍照有什么推荐吗") is False, \
        "后续补的消息不能被旧消息的子串匹配吞掉"


def test_cooldown_is_per_customer():
    """给 A 回话不该挡住 B（这条之前修过，别再退化）。"""
    from wxbot.detector import MessageDetector
    d = MessageDetector({"reply_cooldown_seconds": 5})
    d.mark_replied("A")
    assert d.is_in_cooldown("A") is True
    assert d.is_in_cooldown("B") is False
