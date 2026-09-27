#!/usr/bin/env python3
"""Build script — PyInstaller 打包 + 组装可分发目录。

用法: python scripts/build.py

产出: dist/WeComBot/（可直接压缩发人）

★ 打包时必须剔掉的东西（否则就是把店主的客户数据送出去）：
    .env（真实 API Key）、data/context（客户聊天记录）、data/state（待人工/已处理）、
    logs（含消息原文）、data/unanswered.json、data/learned、测试残留。
  `.env.example`（占位符版）才是要发的。spec 里原来有 `("data","data")`，
  会把整个 data 目录打进 exe —— 已删掉。

★ 打包前会强制跑 `scripts/privacy_check.py`：扫出真实凭据/客户姓名/
  开发机路径就**中止打包**（exit 1），不给人"忘了删"的机会。
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist" / "WeComBot"
SPEC = ROOT / "WeComBot.spec"
BGE_MODEL_DIR = ROOT / "bge_model"

# 要带进包的"内容型资产"（用户明确要求保留：提示词 + 资料库）
KB_SRC = ROOT / "data" / "qdrant"                       # 资料库索引
DEMO_SRC = ROOT / "data" / "chat_raw"                   # 资料库原始文件（示例资料）
PROMPTS_SRC = ROOT / "prompts"                          # 提示词
PROFILES_SRC = ROOT / "profiles"                        # 窗口标定
DOCS_DIR = ROOT / "docs"                                # 详细使用说明的来源（单一源头）

# 要复制的"内容型资产"白名单（测试会检查它不含客户数据）
CONTENT_SOURCES = (KB_SRC, DEMO_SRC, PROMPTS_SRC, PROFILES_SRC)

# 直接复制到包根目录的文件。★ 只发 .env.example，绝不发 .env
EXTRAS = ("启动.bat", "setup.bat", "config.json", ".env.example")

# 只**创建空目录**（程序自己往里写），绝不从开发机复制内容进来
RUNTIME_DIRS = ("data/chat_raw/uploaded", "data/context", "data/pending",
                "data/qdrant/collection", "data/state", "data/videos",
                "data/kb", "logs")

# 程序绝不会打包的目录（写在文档里，也供测试断言）
NEVER_SHIP = ("data/context", "data/state", "data/learned", "data/pending",
              "logs", ".env")

USAGE_TXT = """企业微信智能客服机器人 —— 使用说明（简版）
================================================

【系统要求】
  Windows 10 / 11 64 位。首次启动要加载识别与向量模型，约 30~90 秒，请耐心等。

【第一次使用（4 步）】
  1. 双击「启动.bat」
  2. 界面右上「设置」→ 填模型接口（API Key / 接口地址 / 模型名）→ 保存
     （不填 Key 也能开，但机器人答不了话，只会把消息转「待人工」、不外发任何内容）
  3. 设置里选「企业微信 · 截图模式」，然后打开企业微信 PC 版并登录
  4. 界面点「开始」→ 之后它只处理**开始之后**收到的新消息

  ★ 更详细的步骤（含排错、API 模式、资料库用法）见同目录：
     使用说明-详细.md   （推荐先扫一眼「常见问题」那一节）

【资料库（你的业务知识）】
  已内置一份示例资料（相机租赁）供你试跑。
  换成你自己的：知识库 → 资料库 → 「选择文件…」上传（txt / md / csv / Excel / Word）
  → 点「重建索引」。上传完可以在「试问一句」里验证它答得对不对。

【提示词（说话方式）】
  知识库 → 提示词：系统提示、欢迎语、闲聊、占位语都能改，改完点保存立即生效。

【隐私】
  * 客户聊天记录只存在本机 data\\context\\，本程序不上传。
  * 回答时会把你配置的模型接口叫一次：发送的内容是"客户这一句话 + 命中的资料片段"。
  * 不想用云端模型，就不要配 API Key（机器人只转人工，不外发任何内容）。

【排错】
  启动闪退 / 不工作 → 先看 logs\\monitor.log
  找不到企业微信窗口 → 确认企业微信已登录，且窗口没有最小化到托盘
  答得不准 → 先补资料库（知识库 → 资料库 → 最常转人工的问题），再考虑改提示词

内置示例资料与提示词仅用于演示，请按自己的业务替换后再正式使用。
"""


def ensure_bge_model():
    """本地没有 BGE 模型就下载一份（打包要内置，离线可用）。"""
    if (BGE_MODEL_DIR / "model.safetensors").exists():
        print("[0/6] BGE model found in bge_model/")
        return
    print("[0/6] Downloading BGE model (~92MB)...")
    from huggingface_hub import snapshot_download
    snapshot_download("BAAI/bge-small-zh-v1.5", local_dir=str(BGE_MODEL_DIR))
    print("   BGE model ready")


def clean():
    for d in (ROOT / "build", ROOT / "dist"):
        if d.exists():
            shutil.rmtree(d)
    print("[1/6] Cleaned old build artifacts")


def run_pyinstaller():
    print("[2/6] Running PyInstaller...（几分钟，别关窗口）")
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", str(SPEC),
         "--distpath", str(ROOT / "dist"),
         "--workpath", str(ROOT / "build"),
         "--noconfirm"],
        cwd=str(ROOT),
    )
    if result.returncode != 0:
        print("\n[ERROR] PyInstaller failed!")
        sys.exit(1)
    print("   PyInstaller done")


def _warn_if_running():
    """程序在跑的话，data/qdrant 正在被写 —— 副本可能不完整。"""
    lock = KB_SRC / ".lock"
    if not lock.exists():
        return
    try:
        with open(lock, "a"):
            pass
    except OSError:
        print("   [警告] data/qdrant 被占用：程序可能正在运行。")
        print("          建议先关掉程序再打包，否则内置资料库可能是旧的/不完整的。")


def _copy_tree(src: Path, dst: Path, label: str):
    if not src.exists():
        print(f"   [跳过] {label}（不存在: {src.name}）")
        return
    shutil.copytree(src, dst, dirs_exist_ok=True)
    n = sum(1 for _ in dst.rglob("*") if _.is_file())
    print(f"   已复制 {label}: {n} 个文件")


def copy_extras():
    """把"给人看的东西"和"内容资产"放进 dist；顺手创建运行时目录。"""
    print("[3/6] 组装可分发目录（剔掉客户数据，保留提示词与资料库）")

    # ① 启动脚本与配置（注意：只发 .env.example，绝不发 .env）
    for f in EXTRAS:
        src = ROOT / f
        if src.exists():
            shutil.copy2(src, DIST / f)
            print(f"   已复制 {f}")
        else:
            print(f"   [警告] 缺少 {f}")

    # ② 运行时目录（空目录，程序自己往里写）
    for d in RUNTIME_DIRS:
        (DIST / d).mkdir(parents=True, exist_ok=True)

    # ③ 内容资产：资料库 + 提示词 + 标定（用户要求保留）
    _warn_if_running()
    (DIST / "data" / "qdrant").mkdir(parents=True, exist_ok=True)
    for f in ("meta.json",):
        src = KB_SRC / f
        if src.exists():
            shutil.copy2(src, DIST / "data" / "qdrant" / f)
            print(f"   已复制资料库索引 {f}")
    coll_src = KB_SRC / "collection" / "knowledge_base"
    if coll_src.exists():
        shutil.copytree(coll_src, DIST / "data" / "qdrant" / "collection"
                        / "knowledge_base", dirs_exist_ok=True)
        print("   已复制资料库 collection/knowledge_base")

    # 资料库原始文件（示例资料；只发 .txt/.md/.csv/.xlsx/.docx，跳过 .bak 与日志）
    keep_ext = {".txt", ".md", ".csv", ".xlsx", ".xls", ".docx", ".json"}
    if DEMO_SRC.exists():
        for f in sorted(DEMO_SRC.iterdir()):
            if f.is_file() and f.suffix.lower() in keep_ext:
                shutil.copy2(f, DIST / "data" / "chat_raw" / f.name)
        print(f"   已复制示例资料 {sum(1 for f in DEMO_SRC.iterdir() if f.suffix.lower() in keep_ext)} 个")

    _copy_tree(PROMPTS_SRC, DIST / "prompts", "提示词 prompts/")
    _copy_tree(PROFILES_SRC, DIST / "profiles", "窗口标定 profiles/")

    # ④ 给使用者看的说明
    #    简版 txt 给"解压就想用"的人；详细版直接复制仓库里的 docs/使用说明.md
    #    —— 单一源头：改文档只改那一份，打包自动带上，不会两处不一致。
    (DIST / "使用说明.txt").write_text(USAGE_TXT, encoding="utf-8")
    print("   已写 使用说明.txt")
    detail = DOCS_DIR / "使用说明.md"
    if detail.exists():
        shutil.copy2(detail, DIST / "使用说明-详细.md")
        print("   已复制 使用说明-详细.md（来自 docs/使用说明.md）")
    else:
        print("   [警告] 缺少 docs/使用说明.md，包里只有简版说明")


def privacy_gate():
    """打包后强制隐私自检：有真实凭据/客户数据就中止。"""
    print("[4/6] 隐私自检（扫描 dist 里的凭据、客户姓名、开发机路径、exe 字节串）")
    # 用 -X utf8：Windows 控制台是 GBK，不然自检脚本自己会因编码报错而"假失败"
    r = subprocess.run([sys.executable, "-X", "utf8",
                        str(ROOT / "scripts" / "privacy_check.py"),
                        "--path", str(DIST)], cwd=str(ROOT))
    if r.returncode != 0:
        print("\n[ERROR] 隐私自检没通过 —— 已经中止打包。")
        print("        按上面列出的条目清理后重新打包（见 docs/打包与隐私检查.md）")
        sys.exit(1)
    print("   隐私自检通过")


def verify():
    print("[5/6] 校验包体完整性")
    required = ["启动.exe", "启动.bat", "setup.bat", "config.json", ".env.example",
                "使用说明.txt", "使用说明-详细.md",
                "prompts/system.md", "profiles/wecom.json",
                "data/qdrant/meta.json",
                "data/qdrant/collection/knowledge_base/storage.sqlite"]
    missing = [f for f in required if not (DIST / f).exists()]
    if missing:
        print(f"\n[ERROR] dist 里缺这些文件: {missing}")
        sys.exit(1)

    # 内置模型与 OCR 资源（缺了会在别的电脑上崩）
    for f in ("_internal/bge_model/model.safetensors",
              "prompts/welcome.md", "prompts/smalltalk.md"):
        if not (DIST / f).exists():
            print(f"   [警告] 建议检查: {f} 不存在")
    print("   必需文件都在")


def report():
    total = sum(f.stat().st_size for f in DIST.rglob("*") if f.is_file())
    print(f"[6/6] 打包完成")
    print(f"   输出目录: {DIST}")
    print(f"   体积: ~{total / 1024 / 1024:.0f} MB")
    print(f"\n   发给别人：把整个 dist\\WeComBot 文件夹压缩后发过去，")
    print(f"   对方解压 → 双击「启动.bat」→ 按「使用说明.txt」填自己的模型 Key。")
    print(f"   ★ 自检命令（发布前再跑一次）：")
    print(f"       python scripts/privacy_check.py --path dist/WeComBot")


if __name__ == "__main__":
    # --skip-pyinstaller：PyInstaller 那步很慢（10 分钟级）。
    # 改完打包组装逻辑（复制哪些文件、隐私白名单）时，用它可以快速重跑后面几步。
    if "--skip-pyinstaller" in sys.argv:
        print("[skip] 复用已有 dist（跳过 clean + PyInstaller）")
    else:
        ensure_bge_model()
        clean()
        run_pyinstaller()
    copy_extras()
    privacy_gate()
    verify()
    report()
