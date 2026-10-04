# UI 设计参考（前端美化依据）

> 来源：[`VoltAgent/awesome-design-md`](https://github.com/VoltAgent/awesome-design-md) —— 74 个主流产品开源的
> UI 设计规范（每个一份 `DESIGN.md`，含颜色/字体/间距/圆角/组件 token）。
> 本项目的界面按下面这两套规范做，**落地的 token 全部集中在 `gui/theme.py`**（单一事实来源）。

## 选了哪两套、为什么

| 规范 | 借的是什么 | 为什么选它 |
| :--- | :--- | :--- |
| **Stripe** | 颜色体系与语义（浅色画布、深海军蓝正文、靛蓝主色、发丝线） | 店主是白天在店里用的，浅色更友好；它的数据表/数字排版最成熟，适合我们这个"看数字做决定"的软件 |
| **Linear** | 结构与密度（surface 分层、发丝线分隔、紧凑行高、6–8px 圆角） | 桌面工具的信息密度参考；避免把 web 营销页那种大留白照搬进窗口 |

**没选**：Linear 的深色主题（店主白天用，深色不合适）、Stripe 的渐变 mesh 与 56px 细体
大标题（营销页的东西，桌面工具用不上）。

## 落地的 token（`gui/theme.py: COLORS`）

| 用途 | 值 | 出处 |
| :--- | :--- | :--- |
| 卡片/内容底 | `#ffffff` | Stripe `canvas` |
| 页面底 | `#f6f9fc` | Stripe `canvas-soft` |
| 分区带/表头 | `#eef2f7` | Linear 的 surface 分层思路 |
| 正文/标题 | `#0d253d`（深海军蓝，**不用纯黑**） | Stripe `ink` |
| 次要文字 / 辅助文字 | `#273951` / `#64748d` | Stripe `ink-secondary` / `ink-mute` |
| 发丝线 | `#e3e8ee` | Stripe `hairline` |
| 主色（只给主要动作） | `#533afd`，按下 `#2e2b8c`，浅底 `#eef0fe` | Stripe `primary` / `primary-press` |
| 语义色 | 成功 `#0f7b46` / 警示 `#8a5a17` / 危险 `#b3244a` | Stripe 的资料页语义色偏亮，压暗后用于白底文字 |

**间距**：严格 8px 栅格（2/4/8/12/16/24/32）；**圆角**：卡片 8px、控件 6px（自绘）。

## tkinter 的先天限制与我们的近似做法（关键）

照着 web 规范写 tkinter 会四不像，这几条是踩过的：

| web 有 | tkinter 没有 | 我们怎么做 |
| :--- | :--- | :--- |
| 圆角 | ❌ | `theme.rounded_rect()` 在 Canvas 上用平滑多边形近似 |
| 阴影 | ❌ | 用**发丝线边框 + 底色差**做层次（Linear 本来就是靠线分层） |
| 字重 300/500/600 | ❌ 只有 normal/bold | 层级靠**字号 + 颜色**做，不靠字重 |
| 主题色随配置生效 | ❌ 默认 `vista` 主题**忽略大部分颜色** | 统一切到 `clam`（`apply_theme` 第一件事） |
| 按钮颜色只设 background 就够 | ❌ clam 的边框由 light/dark/bordercolor 控制，会盖住底色 | 三个色一起设（否则"选中态看不出来"，第一版就吃了这个亏） |

字体固定用 **微软雅黑**：不要用规范的 Inter —— 中文会掉回宋体，很丑。

## 美化前后的对比（都能复现）

| 界面 | 美化前 | 美化后 |
| :--- | :--- | :--- |
| 主窗口 | 系统默认灰、按钮无主次、表格里直接显示内部代号（`escalate`） | 白底头部 + 靛蓝主按钮（开始）+ 发丝线分隔；表格显示人话（已自动回复/已转人工/无需回复） |
| 运营看板 | （新增） | 时间分段控件 + KPI 四卡 + 粒度自适应趋势图 + 两块 Top + 护栏统计 |

**视觉自查方式**：`_preview_main.py` / `_preview_dash.py` / `_preview_cal.py`（本地临时脚本，用 Win32
`PrintWindow` 抓窗口位图）—— 界面改动后跑一次看图，不靠脑补。

## 复现这套设计的方法

```bash
# 1) 取规范（raw.githubusercontent 在部分网络下会被重置，走 API 更稳）
python - <<'PY'
import base64, json, urllib.request
url = "https://api.github.com/repos/VoltAgent/awesome-design-md/contents/design-md/stripe/DESIGN.md"
d = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "x"})))
open("stripe-DESIGN.md", "w", encoding="utf-8").write(base64.b64decode(d["content"]).decode())
PY
# 2) 读它的 YAML frontmatter（colors / typography / rounded / components）与 Components 一节
# 3) 把 token 抄进 gui/theme.py，按上表的"近似做法"落到 ttk 样式
```
