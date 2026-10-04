# rag/prompt_store.py
"""提示词外置：可以在前端编辑，改完立即生效。

为什么要外置：提示词原来硬编码在 `rag/generator.py` / `rag/smalltalk.py` 里，
改一句要动代码 + 重启。现在落到 `prompts/*.md`，界面里就能改。

**读取规则：文件在就用文件，不在就用代码里的默认值。**
所以"恢复默认"＝删掉文件，永远能回到出厂状态。

**两处必须挡住的坑**（都是实测会把人搞崩的）：

1. **占位符**：有两类坑，方向相反 ——
   * **写了程序不认识的花括号**（`{contex}` 拼错、正文里随手打了个 `{`）
     → `str.format` 抛 KeyError → 回复生成报错 → **全部转人工**。
     这个最坑：程序不崩、界面不报，用户只看到"机器人突然什么都不答了"。
     所以保存前就把未知占位符拦掉（见 `FORMATTED`）。
   * **删掉 `{context}`** → 不报错，但知识片段填不进去，机器人只能凭印象答。
     所以它是必需占位符，缺了不让存。
   （两者都是**保存时报错**，不是运行时报错 —— 失败要吵，不能静默降级。）

2. **安全规则**：提示词里的"不许编造/用词必须确定/不暴露 AI"是**受保护区**。
   用户手一抖删了，guard 会把大量回复拦成"需要人工处理"，
   用户会以为程序坏了，其实是规则没了。所以界面上这块只读展示，不参与编辑。

（占位符校验是硬拦；安全规则是界面层只读 —— 直接改文件还是能改，但那是他自找的。）
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent.parent

# 提示词跟资料库档案走：第一个档案用 prompts/，新建的档案用 prompts/<档案>/
# （显式赋值可覆盖，测试和"从别的档案复制一份"都靠这个）
PROMPTS_DIR: Optional[Path] = None


def prompts_dir() -> Path:
    """当前档案的提示词目录。**每次调用现算**，切档案立刻生效。"""
    if PROMPTS_DIR is not None:
        return Path(PROMPTS_DIR)
    try:
        from rag import archives
        return archives.prompts_dir()
    except Exception:
        return HERE / "prompts"

# 可编辑的提示词：名字 → (文件名, 说明, 必需占位符)
PROMPTS = {
    "system": ("system.md", "业务回复提示词", ("{context}",)),
    "smalltalk": ("smalltalk.md", "闲聊提示词（非业务消息用）", ()),
    "hold": ("hold.md", "占位语提示词（转人工时给客户的一句）", ()),
    "tone_samples": ("tone_samples.md", "口吻样本（一行一条）", ()),
    # API 模式专用（不发 LLM，直接发这句，所以固定、可控、改完立即生效）
    "welcome": ("welcome.md", "欢迎语（客户进入会话时自动发）", ()),
    "nontext": ("nontext.md", "非文本应答（图片/语音/文件…，一行一条「类型：话」）", ()),
}

# 受保护区：这些内容在界面上只读展示，提示用户别删。
# 只做展示与提醒，不参与格式化，也不参与"缺了就拦"的校验。
SAFETY_RULES = """以下规则是安全底线，界面上不可编辑（改了会出现"答错"而不是"答得不好"）：

- 知识片段里没有的事实（价格、库存、政策、时间承诺）一律不许编造
- 用词必须确定：不许"大概/可能/也许/应该是/估计"
- 不透露自己是 AI 或机器人，不说"根据知识库""根据提供的信息"
- 客户问题超出知识范围时，回"需要人工处理"，不要自己发挥
- 不许在回复里出现知识片段里没有的数字"""


def _resolve(name: str) -> Path:
    return prompts_dir() / PROMPTS[name][0]


# ── 欢迎语 / 非文本应答的出厂默认 ────────────────────────────────────
# 这两条是 API 模式专用的短句，不走 LLM（多发一次调用就多等 1~2 秒，
# 而这两句话本来就该是固定的、可控的）。

DEFAULT_WELCOME = ("在的～想租什么机器？机身、镜头、配件都可以，"
                   "你说说拍摄用途我帮你挑。")

DEFAULT_NONTEXT = """图片：图片收到啦，我看看哈 —— 你顺便说一下想租什么、哪天用？
语音：语音我这边听不清，麻烦打字说一句～
文件：文件我收到了，麻烦打字说一下具体需求哈～
其它：收到～麻烦打字说下具体需求，我好帮你查。"""


def _default(name: str) -> str:
    """代码里的出厂默认值。**懒导入**避免循环依赖
    （generator 在模块层导入本模块，本模块只在调用时才导入它）。"""
    if name == "system":
        from rag.generator import SYSTEM_PROMPT
        return SYSTEM_PROMPT
    if name == "hold":
        from rag.generator import HOLD_SYSTEM_PROMPT
        return HOLD_SYSTEM_PROMPT
    if name == "smalltalk":
        from rag.smalltalk import SMALLTALK_SYSTEM_PROMPT
        return SMALLTALK_SYSTEM_PROMPT
    if name == "tone_samples":
        from rag.smalltalk import TONE_SAMPLES
        return "\n".join(TONE_SAMPLES)
    if name == "welcome":
        return DEFAULT_WELCOME
    if name == "nontext":
        return DEFAULT_NONTEXT
    raise KeyError(name)


def parse_nontext_acks(text: Optional[str] = None) -> dict:
    """解析「非文本应答」为 ``{类型: 话}``。

    格式：一行一条 ``类型：话``。类型可以是 图片 / 语音 / 文件 / 视频 / 位置 /
    链接 / 小程序 / 其它（认不出的类型落到"其它"）。
    解析不出来就用出厂默认 —— 绝不能因为用户改坏了就让客户收不到话。
    """
    raw = text if text is not None else None
    if raw is None:
        try:
            raw = get("nontext")
        except Exception:
            raw = DEFAULT_NONTEXT
    out = {}
    for line in (raw or "").splitlines():
        line = line.strip().lstrip("-*· ").strip()
        if not line or line.startswith("#"):
            continue
        for sep in ("：", ":"):
            if sep in line:
                k, _, v = line.partition(sep)
                k, v = k.strip(), v.strip()
                if k and v:
                    out[k] = v
                break
    if not out:
        for line in DEFAULT_NONTEXT.splitlines():
            k, _, v = line.partition("：")
            if k.strip() and v.strip():
                out[k.strip()] = v.strip()
    return out


# 会被 `str.format()` 填充的提示词：名字 → **允许出现**的占位符。
# 不在这张表里的提示词（占位语 / 口吻样本 / 欢迎语 / 非文本应答）不过 format，
# 所以正文里出现花括号无所谓。
FORMATTED = {
    "system": ("context", "conversation_history", "tone_samples"),
    "smalltalk": ("tone_samples",),
}

# 匹配一个花括号占位符（不跨花括号，所以 {{ 转义写法和嵌套都不会误报）
_PLACEHOLDER_RE = re.compile(r"\{([^{}]*)\}")


def validate(name: str, text: str) -> Tuple[bool, str]:
    """保存前校验。返回 (是否通过, 原因)。"""
    if name not in PROMPTS:
        return False, f"未知的提示词: {name}"
    if not (text or "").strip():
        return False, "内容不能为空"

    allowed = FORMATTED.get(name)
    if allowed is not None:
        known = set(allowed)
        unknown = [m.group(1) for m in _PLACEHOLDER_RE.finditer(text)
                   if m.group(1) not in known]
        if unknown:
            bad = "、".join(f"{{{u}}}" for u in dict.fromkeys(unknown))
            # 这句里要显示成对的花括号，用拼接避开 f-string 的转义地狱
            hint = ("（正文里确实想显示一个花括号的话，"
                    "要写成 " + "{{" + " 和 " + "}}" + "。）")
            return False, (
                f"出现了程序不认识的花括号 {bad} —— 存下去每次生成都会报错，"
                f"机器人会变成「全部转人工」，而且界面不报错。\n"
                f"「{PROMPTS[name][1]}」里只能用："
                f"{'、'.join('{%s}' % a for a in allowed)}。\n" + hint)

    for ph in PROMPTS[name][2]:
        if ph not in text:
            return False, (f"缺少必需占位符 {ph} —— 程序要把命中的知识片段填在这个位置，"
                           f"删掉它机器人就只能凭印象回答。请把它加回去。")
    return True, ""


def get(name: str) -> str:
    """取提示词：文件优先，回退默认；文件里占位符缺失时也回退默认并告警。"""
    p = _resolve(name)
    if p.is_file():
        try:
            text = p.read_text(encoding="utf-8")
            ok, why = validate(name, text)
            if ok:
                return text
            logger.warning(f"{p.name} 校验不通过（{why}），本次改用出厂默认值")
        except Exception as e:
            logger.warning(f"读取 {p} 失败（{type(e).__name__}: {e}），改用出厂默认值")
    return _default(name)


def set_prompt(name: str, text: str) -> Tuple[bool, str]:
    """保存提示词。校验不通过就不写盘。"""
    ok, why = validate(name, text)
    if not ok:
        return False, why
    prompts_dir().mkdir(parents=True, exist_ok=True)
    p = _resolve(name)
    tmp = p.with_suffix(".md.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)
    logger.info(f"提示词已保存: {p}（{len(text)} 字）")
    return True, "已保存，立即生效"


def reset(name: str = None) -> None:
    """恢复默认：删掉文件即可（读取时会回退到代码里的默认值）。"""
    names = [name] if name else list(PROMPTS)
    for n in names:
        p = _resolve(n)
        try:
            if p.is_file():
                p.unlink()
                logger.info(f"已恢复默认: {p.name}")
        except Exception as e:
            logger.warning(f"删除 {p} 失败: {e}")


def is_customized(name: str) -> bool:
    """用户是否改过（文件存在且与默认值不同）。"""
    p = _resolve(name)
    if not p.is_file():
        return False
    try:
        return p.read_text(encoding="utf-8").strip() != _default(name).strip()
    except Exception:
        return False


def ensure_files() -> list:
    """把还没落盘的提示词写出来，方便用户在文件管理器里也能看到/编辑。

    只在文件**不存在**时写，不覆盖用户改过的内容。
    """
    created = []
    try:
        prompts_dir().mkdir(parents=True, exist_ok=True)
        for name in PROMPTS:
            p = _resolve(name)
            if not p.exists():
                p.write_text(_default(name), encoding="utf-8")
                created.append(p.name)
        if created:
            logger.info(f"已生成提示词文件: {created}")
    except Exception as e:
        logger.warning(f"生成提示词文件失败: {e}")
    return created


# ── 口吻样本的解析（一行一条，忽略空行和注释）─────────────────────────

def parse_tone_samples(text: Optional[str] = None, limit: int = 12) -> list:
    raw = text if text is not None else get("tone_samples")
    out = []
    for line in raw.splitlines():
        s = re.sub(r"^\s*[-*·]\s*", "", line).strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
        if len(out) >= limit:
            break
    return out
