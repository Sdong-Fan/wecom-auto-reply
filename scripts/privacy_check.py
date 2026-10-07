# scripts/privacy_check.py
"""打包前/后的隐私自检：包里（或仓库里）**不能有任何真实凭据和客户数据**。

为什么要单独一个脚本：`.env`、`data/context/`、`logs/` 这些东西
一旦跟着包发出去，就是把店主的客户聊天记录和 API Key 送出去了 ——
Excel 里删一行、脚本里漏一个目录都会出事，必须**机器检查 + 打包流程卡住**。

用法：
    python scripts/privacy_check.py                 # 检查 dist/WeComBot（默认）
    python scripts/privacy_check.py --path .        # 检查整个仓库（发布前自检）
    python scripts/privacy_check.py --path dist/WeComBot --strict

退出码：0 = 干净；1 = 发现必须修的问题（BLOCKER）。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ── 1. 不该出现的文件（路径/文件名层面）────────────────────────────────
# 匹配规则按"路径层级"来，不做子串匹配 —— 否则 .venv 里的 _logs.py、
# scipy 的 test_*.mat 都会被误报。
BLOCKER_PATHS = [
    (".env", "真实 API Key / 企业微信凭据（只能发 .env.example）"),
    ("data/context", "**真实客户聊天记录**（聊天历史）"),
    ("data/state", "运行状态（待人工队列、已处理消息，都是真实客户内容）"),
    ("data/learned", "从真实对话里学到的语气与问答"),
    ("data/unanswered.json", "转人工问题排行（真实客户原话）"),
    ("data/kb_history.jsonl", "资料库编辑历史"),
    ("data/pending", "待处理目录"),
    ("logs", "运行日志（含客户消息原文）"),
]
# 文件名规则：(限定目录前缀, 文件名前缀, 说明)。目录前缀 "" = 只查仓库根目录。
BLOCKER_NAME_RULES = [
    ("", "config.json.bak", "配置备份（可能含旧凭据）"),
    ("data/", "test_", "测试残留"),
    ("", "相机租赁客服记录", "示例聊天记录（另存一份就够，别重复发）"),
]

# ── 2. 不该出现的内容（文本层面）──────────────────────────────────────
# "开发机绝对路径"用**通用规则**，不写死成开发者的具体路径 ——
# 既不会把自己的用户名/目录结构留在代码里，也能抓别人的。
_ABS_PATH = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?:[A-Za-z]:\\Users\\[^\\\s\"'`,;)]+"
    r"|[A-Za-z]:\\(?:dev|code|work|src|projects|repos|Desktop|Documents"
    r"|Harness|personal[\w-]*)[^\\\s\"'`,;)]*)",
    re.I)
# 这些绝对路径是"公用的"，不算开发者隐私
_ALLOWED_PATH_HINT = ("your-name", "your-project", "Program Files",
                      "Program Files (x86)", "Windows\\", "<", ">", "%")

# 每条： (正则, 说明, 允许的例外)
CONTENT_RULES = [
    (r"sk-[A-Za-z0-9]{20,}", "真实 LLM API Key",
     ("sk-your-deepseek-key",)),
    (r"ww[a-f0-9]{14,}", "真实企业微信 corp id", ()),
    (r"UfKoc[A-Za-z0-9]{6,}", "真实企业微信回调 Token", ()),
    (r"183\.250\.\d+\.\d+", "自己的公网 IP", ()),
    (_ABS_PATH, "开发机绝对路径（暴露你的用户名/目录结构）", ()),
    (r"(?<!\d)1[3-9]\d{9}(?!\d)", "手机号", ("13800001234", "13800000000",
                                            "15900000202")),
]

# 我们自己用的**合成值**：文档与代码注释里的示例，允许出现
# （和"打码后的真实值"要能区分开：这些的值本身就是假的）
ALLOWED_VALUES = (
    "wwe0123456789abcd", "ww10123456789abc", "TestCallbackToken123",
    "203.0.113.10", "example.ngrok-free.app", "example.ngrok.io",
    "D:\\your-project", "C:\\Users\\your-name",
)

# 只对**指定文件**放行的值：测试里"故意做得像真的"的夹具
# （那些用来验证自检抓不抓得到，不能全局放行，否则检测形同虚设）
# 这里故意写成**前缀**而不是完整值 —— 否则自检脚本自己就会被自己报出来。
PATH_SCOPED_ALLOW = (
    ("tests/test_packaging_privacy.py", "wweffff"),
    ("tests/test_packaging_privacy.py", "sk-ABCDEF"),
    ("tests/test_packaging_privacy.py", "sk-abcdef"),
    ("tests/test_packaging_privacy.py", "15912345"),
    ("tests/test_learn_store.py", "1380013"),
)


def path_allowed(rel: str, val: str) -> bool:
    return any(frag in rel and v in val for frag, v in PATH_SCOPED_ALLOW)

# 只看这些后缀的文本内容（其余是二进制/模型/图片）
TEXT_SUFFIX = {".py", ".json", ".jsonl", ".md", ".txt", ".bat", ".spec",
               ".yml", ".yaml", ".ini", ".cfg", ".env", ".example", ".ps1"}
# _internal/ 里是 Python 运行时（几万个文件），路径要扫、内容只扫可疑的
SKIP_DIRS = {".venv", "__pycache__", ".pytest_cache", "build",
             "bge_model", ".git", "node_modules", ".superpowers"}
TEXT_SKIP_DIRS = SKIP_DIRS | {"_internal"}
MAX_TEXT_BYTES = 4 * 1024 * 1024        # 单个文本文件最多扫 4MB


def _text_worth_scanning(rel: str) -> bool:
    """_internal 里只有"数据类"文件值得读内容（路径检查仍然全覆盖）。

    这一条不能省：spec 曾经有 ("data","data")，那样的泄漏会出现在
    `_internal/data/context/*.jsonl` —— 如果整个 _internal 都跳过就永远查不出来。
    """
    if rel.startswith("_internal/"):
        tail = rel[len("_internal/"):]
        return (tail.startswith(("data/", "logs/", "prompts/", "profiles/"))
                or tail.endswith((".env", ".jsonl", ".json")))
    return True

# 二进制大文件（exe/pyd/dll）里也要查这几条"绝不能出现"的字节串。
# ★ 不把真实客户姓名/自己的 IP 写进仓库 —— 那些放本地私密名单（见下）。
BINARY_NEEDLES = [b"UfKoc"]                 # 企业微信回调 Token 前缀（通用）

# 本地私密名单：一行一个"绝不能出现"的词（客户姓名、自己的 IP、corp id…）。
# 该文件**不进仓库**（.gitignore），所以仓库里不留任何真实姓名，
# 而你本机跑自检时照样能把它们扫出来。
NEEDLE_FILE = ROOT / ".privacy_needles"


def extra_needles() -> list:
    out = []
    try:
        for line in NEEDLE_FILE.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s and not s.startswith("#"):
                out.append(s)
    except OSError:
        pass
    return out
BINARY_SUFFIX = {".exe", ".pyd", ".dll", ".bin", ".dat", ".pkg",
                 # ★ 资料库索引是 sqlite：里面的条目（含"学习"学到的真实对话）
                 #   是打包**要发出去**的内容。只按文本后缀扫会整块漏掉它 ——
                 #   之前就漏了：data/qdrant/.../storage.sqlite 从来没被查过内容。
                 ".sqlite", ".sqlite3", ".db"}


def _mask(s: str) -> str:
    s = s.strip()
    return s[:3] + "*" * max(0, len(s) - 6) + s[-3:] if len(s) > 8 else "***"


def check_files(root: Path) -> list:
    bad = []
    rels = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        rel = str(p.relative_to(root)).replace("\\", "/")
        rels.append(rel)
        if p.name == ".gitkeep":          # 空目录占位符，不是敏感文件
            continue
        why = None
        # 目录前缀（logs/xx、data/context/xx）
        for frag, w in BLOCKER_PATHS:
            if rel == frag or rel.startswith(frag.rstrip("/") + "/"):
                if frag == ".env" and rel.endswith(".env.example"):
                    why = None
                    break
                why = w
                break
        # 文件名规则
        if why is None and p.name != ".gitkeep":
            name = p.name
            for dpre, npre, w in BLOCKER_NAME_RULES:
                if not name.startswith(npre):
                    continue
                if dpre == "":
                    if "/" not in rel:          # 只查根目录
                        why = w
                        break
                elif rel.startswith(dpre):
                    why = w
                    break
        # 运行时会生成、但绝不能打进包的东西
        if why is None and rel.endswith("/.env"):
            why = "包里有 .env —— 打包时把真实凭据带进去了"
        if why:
            bad.append(("文件", rel, why))
    return bad, rels


def check_text(root: Path) -> list:
    bad = []
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in TEXT_SUFFIX:
            continue
        if any(part in TEXT_SKIP_DIRS for part in p.parts):
            continue
        rel = str(p.relative_to(root)).replace("\\", "/")
        if not _text_worth_scanning(rel):
            continue
        try:
            if p.stat().st_size > MAX_TEXT_BYTES:
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for pat, why, allowed in CONTENT_RULES:
            for m in re.finditer(pat, text):
                val = m.group(0)
                if any(a in text[max(0, m.start() - 40): m.end() + 40]
                       for a in allowed):
                    continue
                if any(a in val or val in a for a in ALLOWED_VALUES):
                    continue
                if path_allowed(rel, val):
                    continue
                if any(h in val for h in _ALLOWED_PATH_HINT):
                    continue
                bad.append(("内容", f"{rel}: {_mask(val)}", why))
        # 本地私密名单（客户姓名等）：命中就报
        for needle in extra_needles():
            if needle in text:
                bad.append(("内容", f"{rel}: {_mask(needle)}",
                            "命中本地私密名单（.privacy_needles）"))
    return bad


def check_binary(root: Path) -> list:
    """大二进制里查"绝不能出现"的字节串（凭据 / 本地私密名单）。

    为什么要扫 exe：万一有人把 data 目录打进包里，文本扫描看不到，
    但字节串会留在 exe/dll 里。
    """
    needles = BINARY_NEEDLES + [n.encode("utf-8") for n in extra_needles()]
    bad = []
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in BINARY_SUFFIX:
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        rel = str(p.relative_to(root)).replace("\\", "/")
        try:
            with open(p, "rb") as f:
                prev = b""
                while True:
                    chunk = f.read(8 * 1024 * 1024)
                    if not chunk:
                        break
                    buf = prev + chunk
                    for needle in needles:
                        if needle in buf:
                            bad.append(("二进制", rel,
                                        f"含 {needle[:8]!r}（客户数据/凭据被打进包里了）"))
                    prev = chunk[-64:]
        except Exception as e:
            bad.append(("二进制", rel, f"读不了: {e}"))
    return bad


def main():
    # Windows 控制台默认 GBK，打印勾叉/中文会 UnicodeEncodeError 把整个检查搞崩
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="打包隐私自检")
    ap.add_argument("--path", default=str(ROOT / "dist" / "WeComBot"))
    ap.add_argument("--strict", action="store_true",
                    help="把 .bak / 测试残留也当阻断项")
    a = ap.parse_args()
    root = Path(a.path)
    if not root.is_dir():
        print(f"[privacy] 目录不存在：{root}")
        return 2

    print(f"[privacy] 检查 {root}")
    file_bad, rels = check_files(root)
    text_bad = check_text(root)
    bin_bad = check_binary(root)
    allbad = file_bad + text_bad + bin_bad

    print(f"[privacy] 共有 {len(rels)} 个文件")
    if not allbad:
        print("[privacy] [OK] 没发现凭据 / 客户数据 / 开发机路径")
        return 0

    print(f"\n[privacy] [FAIL] 发现 {len(allbad)} 处问题：")
    for kind, where, why in allbad[:60]:
        print(f"    [{kind}] {where}  <- {why}")
    if len(allbad) > 60:
        print(f"    … 还有 {len(allbad) - 60} 处")
    print("\n[privacy] 这些**不能发布**。修完再打包（见 docs/打包与隐私检查.md）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
