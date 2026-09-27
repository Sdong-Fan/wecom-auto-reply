# tests/test_context_merge.py
"""会话历史不能因为 OCR 名字读法不同就被切成好几份。

现场：同一个人的会话名，OCR 一会儿读成 ``客户A@微信``、一会儿读成
``客户A``（``@微信`` 没读出来，后面还粘着消息预览）。按字面存文件，
一个人的历史就散成好几份，机器人回忆"跟这个人聊过什么"时只看得到其中一份，
回答就断片了（该记住的押金、楼层、时间全忘了）。

这里测两件事：
1. 同一个人的几种写法**落在同一个文件**里；
2. 已经被切开的历史文件，读到的时候**并回一份**（并且返回活下来的那份）。
"""

import json

import pytest

from rag.human_fallback import ContextStore, same_person


def _turn(role, text):
    return json.dumps({"ts": 1.0, "role": role, "text": text},
                      ensure_ascii=False)


def _files(base):
    return sorted(p.name for p in base.glob("*.jsonl"))


# ── 同一个人的几种写法 ────────────────────────────────────────────────

def test_name_variants_are_same_person():
    assert same_person("客户A@微信", "客户A")
    assert not same_person("客户A", "客户E")
    # ``_safe_filename`` 把 @ 写成 _，所以磁盘上的 _微信 写法要靠
    # ContextStore._stem_variants 还原回来比（见下面 store 级的测试）。
    assert not same_person("客户A_微信", "客户A@微信")


def test_safe_filename_variant_matches(tmp_path):
    cs = ContextStore(base_dir=str(tmp_path))
    assert cs._matches("客户A_微信", "客户A@微信")
    assert not cs._matches("客户A_微信", "李四@微信")


def test_same_person_writes_one_file(tmp_path):
    """先按 ``@微信`` 写过，再来一次不带后缀的写法 → 不该另起一个文件。"""
    cs = ContextStore(base_dir=str(tmp_path))
    cs.append("客户A@微信", "customer", "押金多少")
    cs.append("客户A", "assistant", "押金 6000")

    assert len(_files(tmp_path)) == 1, f"一个人的历史被切开了: {_files(tmp_path)}"
    recent = cs.get_recent("客户A")
    assert [t["text"] for t in recent] == ["押金多少", "押金 6000"]


def test_read_back_with_either_spelling(tmp_path):
    """两个写法读回来都要看得到完整历史。"""
    cs = ContextStore(base_dir=str(tmp_path))
    cs.append("客户A@微信", "customer", "第一句")
    cs.append("客户A", "customer", "第二句")

    assert len(cs.get_recent("客户A@微信")) == 2
    assert len(cs.get_recent("客户A")) == 2


# ── 已经被切开的文件：读到就并 ────────────────────────────────────────

def test_split_files_merged_on_read(tmp_path):
    """两份历史（行数不同）→ 并成一份，行多的那份活下来。"""
    (tmp_path / "客户A.jsonl").write_text(
        _turn("customer", "只有这一句") + "\n", encoding="utf-8")
    (tmp_path / "客户A_微信.jsonl").write_text(
        "\n".join([_turn("customer", "第一句"),
                   _turn("assistant", "第二句"),
                   _turn("customer", "第三句")]) + "\n", encoding="utf-8")

    cs = ContextStore(base_dir=str(tmp_path))
    recent = cs.get_recent("客户A@微信", turns=10)

    assert len(_files(tmp_path)) == 1, f"没合并干净: {_files(tmp_path)}"
    # ★ 返回的必须是**活下来的那份**：原来这里返回 hits[0]（按文件名排序），
    # 而被删掉的恰恰可能是它 —— 于是历史读出来是空的。
    assert len(recent) == 4, f"合并不全或指向了已删除的文件: {recent}"


def test_merge_keeps_main_file_even_if_it_sorts_last(tmp_path):
    """主文件（行数多）在文件名排序里排**后面**时也要返回它。"""
    (tmp_path / "客户A.jsonl").write_text(
        "\n".join([_turn("customer", f"第{i}句") for i in range(5)]) + "\n",
        encoding="utf-8")
    (tmp_path / "客户A_微信.jsonl").write_text(
        _turn("customer", "后来补的一句") + "\n", encoding="utf-8")

    cs = ContextStore(base_dir=str(tmp_path))
    recent = cs.get_recent("客户A", turns=10)
    assert len(recent) == 6
    assert (tmp_path / "客户A.jsonl").is_file(), \
        "行多的那份才是主文件"


def test_append_after_merge_goes_to_main_file(tmp_path):
    """合并之后再写，要写进活下来的那份，不能再裂开。"""
    (tmp_path / "客户A.jsonl").write_text(
        _turn("customer", "老问题") + "\n", encoding="utf-8")
    (tmp_path / "客户A_微信.jsonl").write_text(
        "\n".join([_turn("customer", "甲"), _turn("customer", "乙")]) + "\n",
        encoding="utf-8")

    cs = ContextStore(base_dir=str(tmp_path))
    cs.append("客户A@微信", "customer", "新问题")

    assert len(_files(tmp_path)) == 1
    assert len(cs.get_recent("客户A", turns=10)) == 4


def test_other_customers_not_touched(tmp_path):
    """归一不能把别人也卷进来：合并的后果是回复发错人。"""
    cs = ContextStore(base_dir=str(tmp_path))
    cs.append("客户A@微信", "customer", "范的问题")
    cs.append("李四@微信", "customer", "李四的问题")
    cs.append("王五", "customer", "王五的问题")

    assert len(_files(tmp_path)) == 3
    assert [t["text"] for t in cs.get_recent("李四@微信")] == ["李四的问题"]
    assert [t["text"] for t in cs.get_recent("客户A")] == ["范的问题"]


def test_no_file_yet_returns_empty(tmp_path):
    cs = ContextStore(base_dir=str(tmp_path))
    assert cs.get_recent("从没聊过的人") == []


def test_turns_limit_still_works(tmp_path):
    cs = ContextStore(base_dir=str(tmp_path))
    for i in range(6):
        cs.append("张三@微信", "customer", f"第{i}句")
    assert [t["text"] for t in cs.get_recent("张三", turns=2)] == ["第4句", "第5句"]
