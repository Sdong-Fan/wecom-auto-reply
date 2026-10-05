# eval/__init__.py
"""评测数据：三批各 200 条测试消息（messages_200{,b,c}.csv）。

**这里只有数据，没有跑分脚本**——跑分脚本在 `scripts/`：
    python scripts/eval_set.py              # 46 题关键路径「尺子」
    python scripts/eval_messages_200.py     # 200 条「体检」（另有 200b / 200c 两条）

（上游那套 20 题 QA + LLM 裁判的 harness 已于 2026-10-05 删除：
 它的题库是 SaaS 域，与相机租赁业务不匹配。）
"""