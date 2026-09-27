# 上传到 GitHub（照着做）

> 这个仓库已经准备好：**不含密钥、不含客户数据、不含开发机路径、客户姓名已匿名化**。
> 下面是推上去的步骤，以及"上传前必须确认的 5 件事"。

---

## 一、推上去（3 条命令）

先在 GitHub 网页上新建一个仓库（**不要**勾选 "Add a README / .gitignore / license"，
本地已经有了）。拿到地址后：

```bash
cd <你的项目目录>
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git branch -M main
git push -u origin main
```

首次推送会要求登录：用 **Personal Access Token** 当密码
（GitHub → Settings → Developer settings → Personal access tokens，勾 `repo` 权限）。

> 如果想让仓库先"私有"：新建仓库时选 **Private**，确认没问题再在
> Settings → General → Danger Zone 里改成 Public。

---

## 二、上传前必须确认的 5 件事

```bash
# 1) 密钥类文件不在版本控制里（应该一行都没有）
git ls-files | findstr /I ".env .privacy_needles .bak"

# 2) 客户数据 / 日志 / 打包产物没被提交
git ls-files | findstr /I "data/context data/state logs/ dist/ bge_model/"

# 3) 内容级隐私自检（凭据、客户姓名、开发机路径、手机号）
python scripts/privacy_check.py --path .

# 4) 测试还是绿的
set SKIP_EMBED_TESTS=1 && python -m pytest tests/ -q

# 5) 提交里没有意外的大文件（仓库应该只有几 MB）
git count-objects -vH
```

**当前状态**（2026-09-27 首次提交时）：

| 检查 | 结果 |
|---|---|
| `.env` / `.privacy_needles` / `.bak*` 是否被跟踪 | ❌ 没有（在 `.gitignore` 里） |
| `data/context` `data/state` `logs/` `dist/` `bge_model/` | ❌ 没有（在 `.gitignore` 里） |
| 待提交文件的内容级自检 | ✅ 0 处问题 |
| 测试 | ✅ 1178 passed / 4 skipped |
| 仓库体积 | 约 0.7 MB（`.git`），工作区源码约 4 MB |

---

## 三、哪些东西被有意排除（以及为什么）

| 排除项 | 原因 | 对方要跑怎么办 |
|---|---|---|
| `.env` | 真实 API Key / 企业微信凭据 | 复制 `.env.example` — `启动.bat` 也会自动生成 |
| `data/context/`、`data/state/`、`data/unanswered.json` | 真实客户聊天记录与状态 | 程序运行后自动重建（空库开始） |
| `logs/` | 含客户消息原文与截图 | 运行后自动生成 |
| `data/learned/`、`data/kb_history*.jsonl` | 从真实对话里学到的语气/问答 | 用界面重新学 |
| `data/qdrant/`（资料库索引） | 二进制产物；示例资料已经放在 `examples/kb_demo/` | 知识库 → 上传 `examples/kb_demo/*.txt` → 重建索引 |
| `bge_model/`（92MB 嵌入模型） | 太大 | 首次运行自动下载（或 `python scripts/build.py` 会准备） |
| `dist/`（1.6GB 免安装包） | 太大，GitHub 单文件上限 100MB | 需要发人时本地 `python scripts/build.py` 打 |
| `docs/面试冲刺/` | 个人面试准备材料 | 想公开就把 `.gitignore` 里那行删掉再 `git add` |
| `.privacy_needles` | 本地私密名单（真实客户姓名/自己的 IP） | 对方自己建，或用 `cp .privacy_needles.example` 之类 |

---

## 四、如果不小心把密钥推上去了

**先换密钥，再删历史** —— 只要推上去过，就要假设它已经泄露（GitHub 会被爬虫扫）。

1. 立刻在服务商后台**作废并重新生成**：LLM API Key、企业微信 Secret / Token / EncodingAESKey
2. 再清理历史（任选）：
   ```bash
   # 方式一：把文件从所有历史里抹掉（需要 git-filter-repo）
   pip install git-filter-repo
   git filter-repo --path .env --invert-paths
   git push --force

   # 方式二：如果只有最后一次提交里带上了
   git rm --cached .env
   git commit --amend --no-edit
   git push --force
   ```
3. 检查 GitHub 的 **Settings → Security → Secret scanning** 有没有告警

> 这个项目已经内置了防线：`scripts/privacy_check.py` 在**打包流程里是硬门禁**，
> 不通过就中止；发布仓库时手动跑一次 `--path .` 即可。

---

## 五、想让仓库更好看的可选动作

* **仓库描述 / 话题**：GitHub 仓库页 → About → 填一句描述
  （例：`企业微信截图 RPA 自动客服：RAG 资料库 + 三态护栏 + 人工学习闭环`），
  Topics 建议：`wecom` `rpa` `rag` `llm` `customer-service` `python` `tkinter` `qdrant`
* **置顶截图**：界面截图（注意别把真实客户名截进去 —— 用 `examples/kb_demo` 的资料演示）
* **Releases**：把 `dist/WeComBot` 压缩后传成 Release 附件（超过 100MB 不能进仓库，
  但 Release 附件可以到 2GB）
* **GitHub Actions**（可选）：加一个跑 `pytest` 的 workflow，
  但注意本项目依赖 Windows + torch，建议只跑轻量子集（`-k "not embed"`）。
