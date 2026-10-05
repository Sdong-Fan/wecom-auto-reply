# tests/test_learn_switch.py
"""「人工改稿 → 学到的 → 采纳进资料库」这条闭环的开关行为。

店主的真实经历：在待人工页把草稿改成「亲爱的，我们没有针对不同户籍地人的优惠哦」
发出去，然后去知识库找 —— **没有**。原因是学习总开关出厂是 `false`，
`record_direct_send` 直接返回、什么都不记；而且 `_offer_learn` 把
"学习已关闭"**故意排除在提示之外**，所以界面上**一声不吭** ——
只能怀疑是 bug。

设计取舍保留：学到的东西**不自动进资料库**，先进「学到的」等人工确认
（点「采纳进资料库」才写），因为有口误入库的风险。但：
  · 开关默认值改为 true（店主的期待就是"它该学会"）；
  · 关着的时候**必须说出来**（这条就是防它再变成静默）。
"""
import json
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _cfg():
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def test_learning_is_on_in_shipped_config():
    """出厂配置必须开着学习 —— 否则店主改完稿发出去，知识库悄悄不更新。"""
    assert _cfg()["kb"]["learn"]["enabled"] is True


def test_record_direct_send_records_when_enabled():
    """开关打开：直发确认会进「学到的」待确认列表。"""
    from rag import learn, learn_store as ls
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        got = learn.record_direct_send("厦门人有优惠吗",
                                       "亲爱的，我们没有针对不同户籍地人的特殊优惠哦",
                                       cfg=_cfg(), base=base)
        assert got, "开着学习时应当返回记录"
        pending = ls.facts("pending", base=base)
        assert any("户籍地" in f["claim"] for f in pending), pending


def test_record_direct_send_is_gated_when_disabled():
    """开关关掉：什么都不记（这是有意设计，但调用方要能看出来）。"""
    from rag import learn
    with tempfile.TemporaryDirectory() as tmp:
        cfg = _cfg()
        cfg["kb"]["learn"]["enabled"] = False
        assert learn.record_direct_send("问", "答", cfg=cfg,
                                        base=Path(tmp)) is None


def test_offer_learn_tells_the_user_when_learning_is_off():
    """★ 回归：学习关着时**必须提示**，不能一声不吭。

    原来 `_offer_learn` 里写的是
        if reason and reason not in ("没改动，不学", "学习已关闭"):
    也就是"学习已关闭"被**故意跳过提示** —— 店主改完稿发出去，
    界面没任何反馈、知识库也没变化，只能怀疑坏了。
    """
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    # 那段"跳过提示"的写法不许再出现
    assert 'not in ("没改动，不学", "学习已关闭")' not in src
    # 关着时要弹横幅，并且说清去哪儿打开
    assert "学习功能关着" in src
    assert "知识库 → 学到的" in src


def test_set_learn_enabled_writes_config(tmp_path, monkeypatch):
    """编辑窗里勾/取消开关要真的写进 config.json（重启也记得）。

    用临时 config 打桩 —— **绝不能碰真实的 config.json**（那会把店主的学习
    开关改掉，而他正是被这个开关静默关掉坑过一次的人）。
    """
    from config import settings_store as ss
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"kb": {"learn": {"enabled": True}}}), encoding="utf-8")
    monkeypatch.setattr(ss, "load_config",
                        lambda path=None: json.loads(p.read_text(encoding="utf-8")))
    monkeypatch.setattr(ss, "save_config",
                        lambda cfg, path=None: p.write_text(json.dumps(cfg),
                                                            encoding="utf-8"))

    from gui.pending_edit import set_learn_enabled
    set_learn_enabled(False)
    assert json.loads(p.read_text(encoding="utf-8"))["kb"]["learn"]["enabled"] is False
    set_learn_enabled(True)
    assert json.loads(p.read_text(encoding="utf-8"))["kb"]["learn"]["enabled"] is True


def test_set_learn_enabled_keeps_other_config(tmp_path, monkeypatch):
    """只改这一个键，别把配置里其它东西冲掉（save_config 是整份覆盖写）。"""
    from config import settings_store as ss
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"kb": {"learn": {"enabled": True, "max_items": 33},
                                    "history_versions": 5},
                             "rag": {"high_confidence_threshold": 0.5}}),
                 encoding="utf-8")
    monkeypatch.setattr(ss, "load_config",
                        lambda path=None: json.loads(p.read_text(encoding="utf-8")))
    monkeypatch.setattr(ss, "save_config",
                        lambda cfg, path=None: p.write_text(json.dumps(cfg),
                                                            encoding="utf-8"))

    from gui.pending_edit import set_learn_enabled
    set_learn_enabled(False)
    got = json.loads(p.read_text(encoding="utf-8"))
    assert got["kb"]["learn"]["max_items"] == 33, "别把 max_items 冲掉"
    assert got["kb"]["history_versions"] == 5
    assert got["rag"]["high_confidence_threshold"] == 0.5
