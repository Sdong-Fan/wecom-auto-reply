# 企业微信 API 模式 + 截图 profile 化（交付说明）

**日期**：2026-09-24　**状态**：两者都已实现并跑通能验证的部分；两处卡在**你**手上的东西

---

## 截图版 / API 版 功能对照（2026-09-27 补）

两种模式共用同一套内核（RAG / guard / 该不该答 / 待人工队列 / 发送队列），
但**读消息的方式不同**，能做的事也就不同。下面这张表是"哪些功能两边对齐了"：

| 能力 | 截图模式 | API 模式 |
|---|---|---|
| 文本问答、三档分流 | ✅ | ✅ |
| **图片 / 语音 / 文件** | ✅ 气泡读不出字 → 回一句 + 转人工（**按人合并成一条**，因为分不清是图还是语音） | ✅ 按类型分开说（"图片收到啦" / "语音听不清"），语音**能转文字就直接当问题回答** |
| **发送失败重试** | ✅ 发完看一眼聊天区：还有读得出字的未回复灰泡 → 重发一次（判据是**气泡颜色**，不是 OCR 文字，避免误判成重复发） | ✅ 接口失败重试 3 次，参数/权限类错误不重试 |
| **欢迎语** | ✅ **近似版**：第一次要跟某个客户说话之前先招呼一句（`welcome.on_first_contact`）。做不到"刚进会话就欢迎"——那个动作屏幕上不可观测 | ✅ `enter_session` 事件 + `send_msg_on_event`，**客户还没说话就能欢迎** |
| 欢迎语冷却 | —— 一人只招呼一次（永久记着） | 同一人 6 小时内只招呼一次（`welcome.cooldown_seconds`） |
| "人工回过也算回过" | ✅ 蓝泡之上的客户气泡记为已见 | ✅ 人工接待时 `service_state` 能查到（**接口已备，尚未接**） |
| 防重复回复（逐条气泡 + 24h 落盘） | ✅ | ✅（去重按 `msgid`，更准） |
| 列表重排 / OCR 认错字的风险 | 有（靠 `confirm_opened` + 名字归一兜） | 没有（`external_userid` 是精确 ID） |
| 48 小时回复窗口限制 | 无 | **有**（只能在客户发消息后 48h 内回复） |

**两种模式共用一份"招呼过"记录**（`wxbot/first_contact.py` → `data/state/greeted.json`）：
API 模式发过 enter_session 欢迎语的人，第一次回复时不会再被招呼一遍 ——
名字归一用 `same_person`，所以截图模式的名字变体也算同一个人。

截图版那几条能力在 `main.py`：`_screenshot_nontext()`（读不出字的气泡）、
`_verify_screenshot_sent()`（发完确认）、`_greet_before_first_reply()`（第一次先招呼）；
回归测试 `tests/test_screenshot_parity.py`、`tests/test_first_contact.py`。

---

## 一、企业微信 API 模式

### 1.1 先验证协议（已完成，不需要凭据）

`python scripts/probe_wecom_api.py` —— 用**故意错误**的参数去打，看是否返回企业微信
标准错误 JSON。返回 40013/40014 就说明路径与协议是对的，错只错在参数上：

```
[gettoken]            HTTP=200  errcode=40013  'invalid corpid'
[kf/sync_msg]         HTTP=200  errcode=40014  'invalid access_token'
[kf/send_msg]         HTTP=200  errcode=40014  'invalid access_token'
[kf/account/list]     HTTP=200  errcode=40014  'invalid access_token'
[kf/service_state/get]HTTP=200  errcode=40014  'invalid access_token'
可达接口 5/5
```

**结论**：这台机器直连 `qyapi.weixin.qq.com` 通（不需要代理），五个接口路径全部正确。
顺带拿到本机出口 IP：`203.0.113.10`（配"可信 IP"时要用它）。

### 1.2 实现了什么

| 文件 | 内容 |
|---|---|
| `gateway/wecom_kf.py` | 微信客服 API 客户端：access_token 缓存+提前 5 分钟续期、落盘；`sync_msg` 拉消息 + **游标持久化**；`send_text` 回复；`account_list` / `service_state` / 客户昵称查询；`selftest()` 给人话错误 |
| `scripts/wecom_api_selftest.py` | 真凭据自检：`--peek` 看能拉到什么、`--send uid 文本` 真发一条 |
| `main.py` | `channel` 开关：`screenshot`（默认）/ `wecom_api`；新增 `_run_api_poll` + `_handle_api_message`；`_do_send` 通道感知 |
| `config.json` | 新增 `channel` 与 `api.wecom`（`open_kfid` / `state_path` / `timeout_seconds` / `poll_interval_seconds`） |
| `.env` / `.env.example` | 新增 `WECOM_KF_SECRET`、`WECOM_OPEN_KFID`（`.env` 已被 gitignore） |
| `tests/test_wecom_api.py` | 17 个用例：token 缓存/续期/落盘、游标推进、消息过滤、发送体形状、token 失效重试一次、selftest 各类错误分支 |

**两种模式共用同一套内核**：RAG / guard / 该不该答 / 待人工队列 / 发送队列全都不变，
只有"读消息"和"发消息"两头不同。API 模式还额外做了两件事：

* **按 msgid 去重**（服务端唯一 ID），比截图模式的文本指纹准 —— 客户连发两句一样的话不会被吞掉。
* **customer key 用 `external_userid`**，不是昵称 —— 回复路由必须用 id。日志里会同时打昵称。

### 1.3 你需要做的（两步，缺一不可）

**第一步：企业微信后台开微信客服**
后台 → 应用管理 → 微信客服 → 创建/启用一个客服账号。然后在同一页拿三样东西：

| 填到 `.env` | 从哪拿 |
|---|---|
| `WECOM_CORP_ID` | 后台 → 我的企业 → 企业信息 → 企业 ID |
| `WECOM_KF_SECRET` | 后台 → 应用管理 → **微信客服** → Secret（**不是**自建应用的 Secret） |
| `WECOM_OPEN_KFID` | 微信客服账号列表里的 `open_kfid`，形如 `wkxxxx`（只有一个账号时可留空，会自动选） |

大概率还要把本机出口 IP `203.0.113.10` 加进「可信 IP」，否则报 60020。

**第二步：自检**
```powershell
cd D:\\your-project
.\.venv\Scripts\python.exe scripts\wecom_api_selftest.py --peek
```
看到 `✓ OK：token 可用、客服账号 [...]、sync_msg 通` 就成了。然后把 `config.json` 的
`"channel": "screenshot"` 改成 `"wecom_api"`，重启程序 —— 之后不再需要企业微信桌面端、不再截图。

### 1.4 已知缺口

- **字段名按官方文档实现，但没跑过真实响应**：`msg_list[].origin`（3=客户/4=系统/5=接待人员）、
  `text.content`、`next_cursor` 这些要等你自检时用真实响应确认。selftest 会把原始响应打出来。
- **没做回调服务**：现在是**轮询** `sync_msg`（默认 3 秒一次），够用且不用内网穿透。
  如果以后要秒级响应，再配回调 URL + Token + EncodingAESKey。
- **`customer/batchget` 取昵称需要客户联系权限**，拿不到会自动退回用 `external_userid`，不影响回复。

---

## 二、微信 PC 的截图连接功能

### 2.1 先说结论：这台机器上**装不了/没装**微信 PC

```
进程：只有 WXWork.exe / WXWorkWeb.exe
     （WeChatAppEx.exe 与 WeChatOCR.exe 的父进程是 WXWork 29004 —— 那是企业微信自带的
       小程序运行时和截图 OCR，不是微信 PC）
注册表：HKLM/HKCU 的 Tencent\WeChat 都没有
安装目录：C:\Program Files\Tencent\Weixin 等常见路径都不存在
```

**所以微信 PC 的 profile 我没法标定，也没法验证** —— 写一份没标定过的 profile 给你，
等于给你一个"看着能用、实际全错"的东西。这一步需要你先装上微信 PC。

### 2.2 我把"标定"这件事做成了通用工具（已在企业微信上验证）

`scripts/calibrate_chat_app.py` —— 对着任意聊天软件窗口自动量出 profile：

```
$ python scripts/calibrate_chat_app.py --name 企业微信 --id wecom
窗口: hwnd=394116 进程=WXWork.exe 类=WeWorkWindow 标题='企业微信' rect=(0,0,2019,1728)
量出来的区域：
    chat_x = 465   chat_w = 1012
    chat_top_margin_px = 90   chat_bottom_margin_px = 230
我方气泡主题色探测: [201, 231, 255]（该色像素 12423）
自检（按上面参数裁一遍并分边）：
    gray   '21:00' / '以上是打招呼内容' / '你已添加了客户D…'
    other  '我是客户D' / '你好' / '缺保安吗'
    me     '帮您问下缺不缺保安，稍等。'
```

人工核对：`me` 是我们自己发的、`other` 是客户发的、`gray` 是系统提示 —— **全对**。

### 2.3 顺手把 profile 机制接进了主程序（截图模式的架子）

| 文件 | 内容 |
|---|---|
| `profiles/wecom.json` | 企业微信 profile（标定器生成 + 人工钉住已验证的边距） |
| `wxbot/profile.py` | `load_profile` / `list_profiles` / `apply_to_config` |
| `main.py` | 启动时按 `config.json` 的 `profile` 字段套用，再构造 Scanner |
| `wxbot/detector/bubble.py` | 气泡判据支持 `my_rgb`（**跨软件的关键**：微信是绿气泡、企业微信是蓝气泡） |
| `tests/test_profile.py` | 13 个用例 |

**关键不变量**（有测试守着）：套用 `wecom` profile 之后，聊天区起点/上下边距与改造前一致 ——
不能让"支持多软件"把正在跑的企业微信改坏。实测启动日志：

```
已套用 profile: 企业微信（窗口类=WeWorkWindow，聊天区起点=465）
采集: PrintWindow 不抢前台，窗口=(2019, 1728)，面板边界自检通过（col1+col2=465）
通道: 截图模式（企业微信桌面端）
```

气泡颜色规则也做了跨软件的验证性测试：同一套代码，企业微信用 `[201,231,255]` 判我方、
微信 PC 用 `[149,236,105]`（微信绿）判我方，对方灰一律不算 —— 都过。

### 2.4 你装上微信 PC 之后，要做的就是两条命令

```powershell
python scripts/calibrate_chat_app.py --name 微信PC --id wechat_pc --process Weixin.exe
# 看它打印的自检：me 是不是你发的、other 是不是对方发的
# 然后把 config.json 的 "profile" 改成 "wechat_pc"，重启程序
```

如果自检里 `me`/`other` 混了，改 `profiles/wechat_pc.json` 里的 `my_color_rgb` 或
`my_color_tolerance` 重跑。**注意别把容差调大**：企业微信实测里对方灰 `(228,231,235)`
与我方蓝 `(201,231,255)` 的距离只有 47，容差给 60 会把对方的话全判成我方（这个坑已经踩过）。

### 2.5 微信 PC 已知会遇到的额外问题（提前说）

1. **微信 PC 4.x 是自绘的**：jev 的 Windows 项目实测它 UIA 树只有 2 个节点，
   所以只能截图 OCR —— 跟企业微信一样，我们这套探针结论可复用。
2. **窗口类不同**（微信 PC 4.x 是 `Qt51514QWindowIcon`，3.x 是 `WeChatMainWndForPC`），
   标定器会自动量出来。
3. **左侧会话列表的时间戳列**：截图模式的"新消息判定"靠行像素变化，若用户框的区域
   包含时间戳（"刚刚/1分钟前"），会每分钟误判一次新消息。企业微信 profile 用固定值绕过了，
   微信 PC 标定时要在 `layout` 里确认 `list_w` 不包含时间戳列。
4. **风控**：微信 PC 的自动化操作不受微信协议欢迎（企业微信宽松得多）。建议微信侧只做
   "读+填入草稿"这类低风险动作，别做高频自动发送 —— 这一点我们现在**没有**做限制，
   需要你拍板要不要加。

---

## 四、API 模式端到端验证（**不需要你的凭据**）

上面 1.4 说过"字段名没跑过真实响应"。用本地假企业微信服务器把这条缺口补上了：

* `scripts/mock_wecom_server.py` —— 假 qyapi：`gettoken` / `account_list` / `sync_msg`（**认游标**）
  / `send_msg`（记录收到的回复）/ `customer/batchget`。可用作库，也可 `python scripts/mock_wecom_server.py` 单独跑。
* `scripts/run_api_e2e.py` —— 起假服务器 → 自检 → 拉消息 → **走真实 RAG 与 knowledge base** → 分流 → 用 API 回复。
* `gateway/api_policy.py` —— 把"收到一条客户消息之后怎么办"抽成可测函数，
  `main.py` 与测试共用一份实现（原来写在闭包里没法测）。
* `tests/test_wecom_api_e2e.py` —— 15 个用例（真 HTTP 往返 + 分流策略四分支）。

实测输出：

```
1) 自检: ✓ OK：token 可用、客服账号 ['wkMOCK']、sync_msg 通（本轮探到 1 条客户文本，游标未推进）
2) 拉消息：MSG1 '你们有富士 X-T5 吗' / MSG2 '谢谢' / MSG3 '缺保安吗'
3) 第二轮拿到 0 条（游标已推进，不重复）
4) 分流：
   ['你们有富士 X-T5 吗'] → auto_send  理由=all_checks_passed
   ['谢谢']               → no_reply   理由=收尾语/确认，无需回复
   ['缺保安吗']            → escalate   理由=置信度不足(0.48)  占位语=帮您问下缺不缺保安，稍等。
5) 真的通过 API 发出去：
   → wmCUST01: 有的，富士 X-T5 日租 95 元，押金 4000 元。4020 万像素，胶片模拟直出，适合人文街拍。租期 3 天起，7 天以上有折扣。
   → wmCUST02: 帮您问下缺不缺保安，稍等。
6) 待人工队列 1 条
```

三条分支（直发 / 不回 / 转人工）全部走通，回复内容来自真实知识库。

### 这个过程抓到一个真 bug

**`selftest()` 会吃掉第一条客户消息**：它内部调 `sync_msg(limit=1)` 试探连通性，
而 `sync_msg` 会**推进并落盘游标** —— 于是第一条真实消息被永久跳过。
E2E 第一轮跑出来 MSG1 消失、第二轮才有 MSG2/MSG3，就是这个原因。

修法：自检前存下游标，探完还原（`try/finally`），并把 selftest 的文案改成"游标未推进"。
回归测试：`test_selftest_ok_and_does_not_advance_cursor`。
**如果你在没有这个修复的版本上跑过自检，第一条消息可能已经丢了。**

另外给假服务器补上"真的认游标"（原来只按投递计数器走），否则"游标推进/未推进"这类 bug
在假服务器上根本测不出来。

## 五、profile 失效检测（`--check`）

软件改版会让 profile 悄悄失效（侧边栏挪位、气泡换色），所以标定器加了校验模式：

```
$ python scripts/calibrate_chat_app.py --name 企业微信 --id wecom --check
    chat_x                   profile=465    实测=465    ✓
    chat_w                   profile=1012   实测=1012   ✓
    chat_top_margin_px       profile=100    实测=90     ✓
    chat_bottom_margin_px    profile=240    实测=230    ✓
    window_class             profile='WeWorkWindow' 实测='WeWorkWindow' ✓
    分边自检: {'me': 9, 'gray': 4, 'other': 8}
✓ profile 仍有效（区域与窗口类都没漂）
```

不写文件、只比对 + 重跑分边自检；漂了就提示重新标定。以后可以挂个定时任务，或者
在主程序启动时顺手调一次（现在启动时做的是同类检查：`check_layout` 面板边界自检）。

---

## 六、一条我查到但**没有采信**的信息

搜索时搜到一份第三方文章，声称腾讯 2026 年通过 "OpenClaw / iLink" 开放了**个人微信 Bot API**
（`ilinkai.weixin.qq.com`、npm 包 `@tencent-weixin/openclaw-weixin`、附官方使用条款）。
这份内容来自第三方仓库，**我无法验证真伪**（腾讯官方域名与企业微信文档里都没有这条），
而且里面还写了"需要 OpenClaw 账号体系、状态未知"。**我没有据此写任何代码**。
如果你对这个渠道有兴趣，正确做法是先自己去腾讯官方渠道核实，别拿它当依据。

---

## 七、测试总览

```
.venv\Scripts\python.exe -m pytest tests/ -q
361 passed, 2 warnings in 105.21s
```

（上一轮 346 → 现在 361：`test_wecom_api_e2e.py` 15 个。累计：截图相关 20、judge 48、
按客户冷却 6、profile 13、API 客户端 17、API 端到端 15。）
程序当前在跑，截图模式，启动于 22:59:11，无 ERROR。
