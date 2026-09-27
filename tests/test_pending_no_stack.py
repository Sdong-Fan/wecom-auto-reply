# tests/test_pending_no_stack.py
"""待人工队列：**一条消息一行**，同一个人不合并，同一条消息不重复。

两个方向都要守住：

* **同一个人问了两件不同的事 → 两条**（原来第二条会把第一条顶掉，
  客户问「有打算在鼓浪屿开店吗」+「鼓浪屿房租不便宜」，店主只看得到后面那条）
* **同一条消息重复入队 → 一条**（发送失败重试、OCR 抖动不该刷屏）

还有一个人名归一的坑：OCR 读出来的会话名会变（`@微信` 有时读不出来、
名字后面粘着消息预览），同一个人的几种写法不能变成好几个"人"。
"""

import pytest

from rag.human_fallback import PendingQueue, better_name, name_candidates, same_person


@pytest.fixture
def q(tmp_path):
    return PendingQueue(persist_path=str(tmp_path / "pq.json"))


# ── 名字归一（同一个人的几种 OCR 写法） ───────────────────────────────

def test_candidates_of_clean_name():
    assert name_candidates("客户A@微信") == {"客户A@微信", "客户A"}


def test_candidates_of_name_with_preview():
    got = name_candidates("客户A@微信 帮您问下算好没，稍等。")
    assert "客户A" in got                      # @ 之前
    assert "客户A@微信" in got                  # 第一个空格之前


def test_candidates_when_suffix_missing():
    got = name_candidates("客户A 这一串我算不明白了")
    assert "客户A" in got                      # 第一个空格之前，这是唯一能救回来的线索


def test_same_person_across_ocr_variants():
    a = "客户A@微信"
    b = "客户A@微信 帮您问下算好没，稍等。"
    c = "客户A 这一串我算不明白了"
    assert same_person(a, b) is True
    assert same_person(a, c) is True
    assert same_person(b, c) is True


def test_different_people_are_not_merged():
    """合并的后果是把 A 的回复发到 B 那里 —— 宁可漏归一，也不能合错。"""
    assert same_person("张三", "张三丰") is False
    assert same_person("客户A@微信", "客户D@微信") is False
    assert same_person("小李 你好", "小王 你好") is False


def test_same_person_handles_empty():
    assert same_person("", "张三") is False
    assert same_person("张三", "") is False


def test_better_name_prefers_shorter():
    assert better_name("客户A@微信 帮您问下算好没", "客户A@微信") == "客户A@微信"
    assert better_name("", "客户A") == "客户A"
    assert better_name("客户A", "") == "客户A"


# ── 一条消息一行（本次要修的核心） ────────────────────────────────────

def test_two_different_messages_two_rows(q):
    """客户连问两句 = 两件事，店主必须都看得到。"""
    q.push("客户A@微信", "有打算在鼓浪屿开店吗", "帮您问下鼓浪屿开店的事，稍等。")
    q.push("客户A@微信", "鼓浪屿房租不便宜", "帮您问下鼓浪屿房租，稍等。")
    items = q.get_all()
    assert len(items) == 2
    assert {i.customer_message for i in items} == {"有打算在鼓浪屿开店吗", "鼓浪屿房租不便宜"}
    # 两条都算在这个人名下，发送时都能找到会话
    assert {i.customer_name for i in items} == {"客户A@微信"}


def test_same_message_does_not_duplicate(q):
    """同一条消息重复入队（发送失败重试）不该刷屏。"""
    q.push("客户A@微信", "有货吗", "有货的")
    q.push("客户A@微信", "有货吗", "有货的")
    assert len(q.get_all()) == 1


def test_same_message_with_punctuation_diff_is_one(q):
    q.push("客户A@微信", "老板你多大了", "帮您问下")
    q.push("客户A@微信", "老板你多大了！", "帮您问下")
    assert len(q.get_all()) == 1


def test_same_message_from_name_variant_is_one(q):
    """同一个人的名字读了三个版本、问的还是同一句 → 一条。"""
    q.push("客户A@微信", "老板你多大了", "d")
    q.push("客户A@微信 帮您问下算好没，稍等。", "老板你多大了", "d")
    q.push("客户A 这一串我算不明白了", "老板你多大了", "d")
    assert len(q.get_all()) == 1


def test_name_variant_shares_one_person(q):
    """名字写法变了，但人还是同一个人：两条消息都挂在他名下。"""
    q.push("客户A@微信 帮您问下算好没，稍等。", "第一条", "d")
    q.push("客户A", "第二条", "d")
    items = q.get_all()
    assert len(items) == 2
    # person 是内部标识（第一次见到的写法），只要**稳定且唯一**就能把人分组
    assert len({i.person for i in items}) == 1
    # 显示名留最干净的那个 —— 发送时要拿它对会话
    assert {i.customer_name for i in items} == {"客户A"}


def test_two_customers_two_rows_each(q):
    q.push("客户A@微信", "问题A1", "d")
    q.push("客户A@微信", "问题A2", "d")
    q.push("客户D@微信", "问题B1", "d")
    assert len(q.get_all()) == 3
    assert len(q.get_all_of("客户A@微信")) == 2
    assert len(q.get_all_of("客户D@微信")) == 1


def test_per_customer_cap_drops_oldest(q):
    """保险丝：一个人攒太多条（正常聊天到不了），丢最旧的。

    阈值特意留足余量（默认 10）—— 被挤掉一条问题正是这次要修的毛病。
    """
    assert PendingQueue.DEFAULT_MAX_PER_CUSTOMER >= 10
    q._max_per_customer = 3
    for i in range(5):
        q.push("客户A@微信", f"第{i}个问题", "d")
    items = q.get_all()
    assert len(items) == 3
    texts = {i.customer_message for i in items}
    assert "第0个问题" not in texts and "第4个问题" in texts


def test_cleanup_count_counts_real_replacements(q):
    """被顶掉的次数只统计"同一条消息重复入队"，不是"这个人又说话了"。"""
    q.push("客户A@微信", "有货吗", "d")
    q.push("客户A@微信", "有货吗", "d")          # 同一条 → 顶掉一次
    q.push("客户A@微信", "多少钱", "d")          # 换了一件事 → 不算顶掉
    assert q.get_cleanup_count("客户A@微信") == 1
    assert len(q.get_all()) == 2


# ── 按 key 定位（界面上那一行） ──────────────────────────────────────

def test_remove_key_only_removes_that_row(q):
    q.push("客户A@微信", "问题1", "d")
    q.push("客户A@微信", "问题2", "d")
    items = q.get_all()
    assert q.remove_key(items[0].key) is not None
    left = q.get_all()
    assert len(left) == 1
    assert left[0].customer_message == items[1].customer_message


def test_get_key_unknown(q):
    assert q.get_key("没有这个key") is None


def test_get_returns_newest_of_person(q):
    q.push("客户A@微信", "问题1", "d")
    q.push("客户A@微信", "问题2", "d")
    assert q.get("客户A@微信").customer_message == "问题2"


def test_remove_by_name_removes_newest_only(q):
    q.push("客户A@微信", "问题1", "d")
    q.push("客户A@微信", "问题2", "d")
    assert q.remove("客户A@微信").customer_message == "问题2"
    assert len(q.get_all()) == 1


def test_mark_expired_marks_all_of_person(q):
    q.push("客户A@微信", "问题1", "d")
    q.push("客户A@微信", "问题2", "d")
    q.mark_expired("客户A")
    assert {i.status for i in q.get_all()} == {"expired"}


def test_persisted_two_rows_survive_restart(tmp_path):
    path = str(tmp_path / "pq.json")
    a = PendingQueue(persist_path=path)
    a.push("客户A@微信", "有打算在鼓浪屿开店吗", "d1")
    a.push("客户A@微信 带预览", "鼓浪屿房租不便宜", "d2")
    b = PendingQueue(persist_path=path)          # 重启后读回来
    items = b.get_all()
    assert len(items) == 2
    assert all(i.key for i in items)             # key 也要存下来，界面才对得上
    assert {i.customer_message for i in items} == {"有打算在鼓浪屿开店吗", "鼓浪屿房租不便宜"}


def test_loads_legacy_one_per_person_file(tmp_path):
    """老文件是"一人一条、key 就是人名"，读回来要能补出 key。"""
    import json
    import time
    p = tmp_path / "old.json"
    p.write_text(json.dumps({"items": {"客户A@微信": {
        "timestamp": time.time(), "customer_name": "客户A@微信",
        "customer_message": "旧的问题", "ai_reply": "d",
        "confidence": 0.5, "guard_decision": "low_risk", "status": "pending"}}},
        ensure_ascii=False), encoding="utf-8")
    q = PendingQueue(persist_path=str(p))
    items = q.get_all()
    assert len(items) == 1
    assert items[0].key                            # 补出来的 key 不为空
    assert q.get_key(items[0].key) is not None


# ── 界面：两行 + 点哪行发哪条 ────────────────────────────────────────

def test_gui_shows_two_rows_and_sends_the_selected_one(tmp_path):
    """真建一个主窗口：同一个人的两条待人工要显示成两行，
    而且点第二行发出的是**第二条**（键不能都指向同一个人）。"""
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except Exception as e:
        pytest.skip(f"没有可用的显示环境: {e}")
    root.withdraw()
    try:
        from gui.main_window import MainWindow
        w = MainWindow()
        w.refresh_pending_queue([])                      # 建出待人工树

        q = PendingQueue(persist_path=str(tmp_path / "pq.json"))
        q.push("客户A@微信", "有打算在鼓浪屿开店吗", "帮您问下鼓浪屿开店的事，稍等。")
        q.push("客户A@微信", "鼓浪屿房租不便宜", "")
        items = q.get_all()                              # 新→旧
        w.refresh_pending_queue(items)

        rows = w._pending_tree.get_children()
        assert len(rows) == 2, "同一个人的两条消息必须是两行"

        keys = [w._pending_tree.item(r, "values")[5] for r in rows]
        assert len(set(keys)) == 2, "两行的定位键必须不同，否则点哪行都一样"
        assert set(keys) == {i.key for i in items}
        # 客户名后面标了条数，免得以为重复
        assert "[2条]" in str(w._pending_tree.item(rows[0], "values")[1])

        # 选中第二行 → 回调拿到的应该是第二行那个 key
        sent = []
        w.set_pending_callbacks(on_send=sent.append)
        w._pending_tree.selection_set(rows[1])
        w._handle_pending_send()
        assert sent == [items[1].key]
    finally:
        root.destroy()

