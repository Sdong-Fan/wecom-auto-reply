# rag/local_answers.py
"""本地直答：有些问题**不该问知识库，也不该让模型猜** —— 读系统时钟就行。

用户提的："我问了现在几点，但是现在几点都回答不了吗，直接读取时间不就行了"

对。知识库里不可能有"现在几点"，模型也没有时钟；让模型编时间是错的。
这类"问助手自己的世界状态"的问题，直接本地算、不走 RAG、不过 LLM：

    现在几点 / 几点了 / 现在时间      → 现在 1 点 12 分
    今天几号 / 几月几号 / 今天日期    → 今天是 9 月 25 日
    今天星期几 / 周几                → 今天星期四

刻意做得**窄**：只认明确在问时间/日期的短句。像"你们几点下班"问的是**营业时间**，
那是知识库的事，不能拿当前时间糊弄过去 —— 所以这里要求句子里出现
"现在/今天/此刻"这类词，或者整句就是在问当前时刻。
"""

from __future__ import annotations

import datetime
import re
from typing import Optional

# 明确在问"当前时刻/日期"的句式。
# ★ 允许 OCR 把"几"读成"儿"：实测客户打的"现在几点"被读成"现在儿点了？"，
#   本地直答没认出来 → 落到闲聊通道 → 模型没有时钟，**编了一个时间发出去**
#   （"快十一点了"，当时是凌晨两点）。宁可多认几种写法。
_JI = "[几儿]"
_TIME_PATTERNS = [
    rf"现在(是)?{_JI}(点|时)",
    rf"{_JI}(点|时)了",
    r"现在(的)?时间",
    r"现在什么时间",
    r"报(一下)?(时间|时)",
]
_DATE_PATTERNS = [
    rf"今天(是)?{_JI}(号|日)",
    rf"{_JI}(号|日)了",
    rf"{_JI}月{_JI}号",
    r"今天(是)?(什么|几月几号|多少号)",
    r"今天(的)?日期",
    rf"现在(是)?({_JI}月{_JI}号|{_JI}号)",
]
_WEEKDAY_PATTERNS = [
    rf"今天(是)?(星期|周|礼拜){_JI}?",
    rf"(星期|周|礼拜){_JI}了",
]

# 这些是在问"营业时间/排班"，属于知识库，不能被当前时间顶掉
_BUSINESS_HINTS = ("营业", "上班", "下班", "开门", "关门", "打烊", "几点开始营业",
                   "几点上班", "几点下班", "营业时间", "工作时间")


def _weekday_cn(d: datetime.date) -> str:
    return "星期" + "一二三四五六日"[d.weekday()]


def _fmt_time(now: datetime.datetime) -> str:
    """口语化：整点说'几点'，否则说'几点几分'。"""
    if now.minute == 0:
        return f"现在 {now.hour} 点整"
    return f"现在 {now.hour} 点 {now.minute} 分"


_CLEAN = re.compile(r"[\s，,。.！!？?~～、]+")
_PARTICLE = r"[了吗呢啊呀吧哈]?"


def answer_locally(text: str, now: Optional[datetime.datetime] = None) -> Optional[str]:
    """能本地答就返回答案，否则 None（交回给 RAG 流程）。"""
    t = _CLEAN.sub("", text or "")
    # 只认短问句："现在几点" 是问时间；"现在几点了我还在等你们回复" 不是。
    if not t or len(t) > 15:
        return None
    # 问营业/上下班的，交给知识库
    if any(h in t for h in _BUSINESS_HINTS):
        return None

    now = now or datetime.datetime.now()

    def hit(pats):
        # 必须**整句**就是这个问题。"现在几点了你们店里还有人吗" 里虽然
        # 含"现在几点了"，但它还有别的问题，应当交给 LLM，不该被本地直答截走。
        return any(re.fullmatch(p + _PARTICLE, t) for p in pats)

    # 星期要优先于日期（"今天星期几"里也含"今天"）
    if hit(_WEEKDAY_PATTERNS):
        return f"今天{_weekday_cn(now.date())}"
    if hit(_DATE_PATTERNS):
        return f"今天是 {now.month} 月 {now.day} 日"
    if hit(_TIME_PATTERNS):
        return _fmt_time(now)
    return None


def now_line(now: Optional[datetime.datetime] = None) -> str:
    """一行"当前时间"，塞进提示词里当作可引用的上下文。

    注意：它也应当作为**知识片段**传给 guard —— 否则模型一旦在回复里写出
    时间数字，guard 的"数字必须有出处"检查会把它当成编造而拦掉。
    """
    n = now or datetime.datetime.now()
    return (f"[当前时间] {n.year}-{n.month:02d}-{n.day:02d} "
            f"{n.hour:02d}:{n.minute:02d} {_weekday_cn(n.date())}")
