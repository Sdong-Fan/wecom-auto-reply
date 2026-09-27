# 探针报告：企业微信 Windows 能不能用 UIA 读聊天文字

**探针**：`scripts/probe_uia_wework.py`（只读：不点击、不输入、不 Invoke、不发送）
**日期**：2026-09-24　**环境**：企业微信 `WXWork.exe` PID 29004，主窗口 2019×1728，Windows
**参考**：jev-chat-windows `probe/probe_win2.py`（微信 PC 4.x 的同类探针）

## 结论

**路线 B（截图 + 本地 OCR）。UIA 路线证伪。**

会话列表与聊天区在 UIA 里**没有节点**，在 Win32 里**没有子窗口** —— 和微信 PC 4.x 一样是自绘渲染，
企业微信没有为它们实现无障碍。唯一能读到的是右侧的 **CEF/Chromium AI 侧边栏**，与客服消息无关。

## 证据

### 1. UIA 树：主窗口只有 8 个节点，唯一文字是窗口标题

```
[1] wxwork.exe  窗口标题='企业微信'  class=WeWorkWindow  hwnd=394116
    节点总数=8  有文字=1  含中文=1  遍历耗时=100ms
    每层节点数: {0: 1, 1: 3, 2: 1, 3: 1, 4: 2}
    控件类型: {'PaneControl': 6, 'WindowControl': 1, 'DocumentControl': 1}
    >>> 含中文节点只有 '企业微信'（窗口标题本身）
```

全量节点树（`--tree`，带屏幕坐标）：

```
d0  WindowControl  WeWorkWindow          rect=[0, 0, 2019, 1728]       '企业微信'
  d1  PaneControl    TitleBarWindow      rect=[0, 0, 2020, 42]         ''      ← 标题栏
  d1  PaneControl    PerryShadowWnd      rect=[-27,-27, 2046, 1755]    ''      ← 窗口阴影
  d1  PaneControl    WXWorkWebViewHostWin rect=[1479, 289, 2017, 1726] ''      ← 只有右侧 CEF
        d2..d9  CefBrowserWindow / Chrome_WidgetWin_1 / Chrome_RenderWidgetHostHWND
                → '客户A' '@微信' '客户需求' '客户意向' '成交卡点'
                  '服务建议' '待办事项' '跟进纪要' '立即总结' '使用规则' '隐私规则'
```

**整棵树只有 3 个一级子节点：标题栏、窗口阴影、右侧 CEF 面板。**
占屏幕绝大部分的会话列表（col1）与聊天区（col2）**一个节点都没有**。

### 2. Win32 子窗口：聊天区不是独立窗口

```
wxwork.exe hwnd=394116 的 Win32 子窗口：
    d1 hwnd=526390 vis=1 WXWorkWebViewHostWindow   rect=[1479, 289, 2017, 1726]
    d2 hwnd=526374 vis=1 CefBrowserWindow          rect=[1479, 289, 2019, 1726]
    d3 hwnd=526392 vis=1 Chrome_WidgetWin_1        rect=[1479, 289, 2019, 1726]
    d4 hwnd=526388 vis=1 Chrome_RenderWidgetHostHWND rect=[1479,289,2019,1726]
```

唯一的子窗口链就是那块 CEF。**没有给聊天区留独立的渲染子窗口** → 屏幕内容全部画在
`WeWorkWindow` 这一块画布上（和微信 PC 4.x 的 `MMUIRenderSubWindowHW` 同一类做法）。

### 3. A/B 对照：设系统读屏器标志（唤醒无障碍）

Qt / Chromium 的无障碍是懒加载的，见到读屏器才填充树。用 `SystemParametersInfoW(SPI_SETSCREENREADER, 1)`
（不动企业微信、不注入、可还原）之后重跑：

| | 节点总数 | 有文字 | 含中文 |
|---|---|---|---|
| 标志关闭 | 8 | 1 | 1（窗口标题） |
| 标志打开 | 29 | 15 | 14（**全部在 CEF 面板里**） |

标志确实唤醒了 CEF 那部分的无障碍（8 → 29），**但聊天区依旧 0 节点**。
跑完已还原（`SPI_GETSCREENREADER now = False`）。

### 4. 探针自身的对照（排除"是我代码没起来"）

反正据在同一个探针、同一次遍历里：**CEF 面板的文字读得到**（`客户A`、`客户需求`…），
说明 UIA 客户端本身工作正常、权限正常；聊天区读不到不是探针的问题。

按 jev 探针协议还应在普通 App（如系统设置）上跑一遍基线 —— 第 4 条已等价满足，
且探针已内置 `--hint`（讲述人 / Accessibility Insights / inspect.exe 三条排查路）。

## 对采集层的直接影响

1. **别指望 UIA**。聊天文字只能 OCR，能优化的只有"OCR 的时机、区域、分边、去重、引擎"。
2. **窗口句柄选 `WeWorkWindow`（顶层）**，不是子窗口 —— WGC / PrintWindow 都对着它抓。
   （`hwnd=394116`，类名 `WeWorkWindow`，进程 `wxwork.exe`。）
3. **右侧那块 CEF（x 1479–2019）是界面里唯一无障碍可见的部分**，也是企业微信自带的
   AI 客户分析面板（客户需求/客户意向/成交卡点/服务建议/待办事项/跟进纪要 + 立即总结）。
   如果哪天想用它的分析结果，用 `SPI_SETSCREENREADER` + UIA 就能白捡；但需要点"立即总结"，
   属于侵入操作，本期不做。
4. 既然只能 OCR，"帧停稳再 OCR / 像素锚点定位 / 颜色分边 / 换 OCR 引擎 / OCR 挪子进程"
   这五条就是稳定性的全部来源 —— 进入任务 2。

## 自验缺口

- 未验证：企业微信在"独立聊天窗口"形态（双击会话弹窗）下是否暴露节点。本次只有主窗口形态。
- 未验证：`Accessibility Insights for Windows` / `inspect.exe` 交叉验证未跑（未安装）。
  但第 4 条的内部对照已能说明问题，且即使交叉验证能读到，也说明企业微信只对"官方读屏器"
  开树 —— 那种情况下需要把探针包成一个读屏器，属于另一条路。
- 未验证：企业微信版本号与"是否曾在某版本暴露过节点"。结论只对当前版本成立。
