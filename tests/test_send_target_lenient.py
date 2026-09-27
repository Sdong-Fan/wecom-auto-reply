"""发送目标校验的宽松模式：允许 OCR 错一两个字，但不允许认成另一个人。

用户要的"读错也放行"：OCR 把"客户甲乙丙丁"读成"客户甲乙丙戊"时不该拦着不发。
但**不能**变成"一律放行" —— 那样就失去了"防发错人"的意义。
所以按**字符命中率**松绑：want 的字符有多少出现在 got 里，>= 0.75 放行。
    4 个字错 1 个 = 0.75 → 放行
    读成完全另一个人（≈0）→ 拒绝

（注：这里用 4 个字的合成名字——宽松阈值就是按 4 字场景定的，
  用 3 个字的名字会把 0.75 顶到 0.67，测的就不是同一件事了。）
"""
import pytest

from wxbot.scanner import Scanner

FOUR = "客户甲乙丙丁"          # 4 字合成名
FOUR_TYPO = "客户甲乙丙戊"     # 错 1 个字 → 命中率 0.75


def _s(mode="lenient"):
    return Scanner({"send_target_verify": mode})


# ── strict（默认）──────────────────────────────────────────────────────

def test_strict_requires_exact_substring():
    s = _s("strict")
    assert s._name_matches("客户D", "客户D@微信 帮您问下…") is True
    assert s._name_matches(FOUR, f"{FOUR_TYPO}@微信 帮您问下…") is False


def test_default_is_strict():
    assert Scanner({})._verify_mode() == "strict"


# ── lenient（用户要的）────────────────────────────────────────────────

def test_lenient_allows_one_wrong_char():
    s = _s("lenient")
    assert s._name_matches(FOUR, f"{FOUR_TYPO}@微信 帮您问下…") is True, \
        "4 个字错 1 个应当放行"


def test_lenient_still_rejects_a_different_person():
    s = _s("lenient")
    assert s._name_matches("客户A", "客户D@微信") is False, "认成另一个人必须拦住"
    assert s._name_matches("客户C", "文件传输助手") is False


def test_lenient_rejects_short_name_confusion():
    """"客户A" vs "客户B" 只差一个字，但字命中率 0.67 < 0.75，仍然拦住。"""
    s = _s("lenient")
    assert s._name_matches("客户A", "客户B") is False


def test_lenient_still_accepts_exact():
    s = _s("lenient")
    assert s._name_matches("客户D", "客户D@微信 昨天") is True


def test_lenient_rejects_empty():
    s = _s("lenient")
    assert s._name_matches("", "随便什么") is False
    assert s._name_matches("张三", "") is False


# ── off ───────────────────────────────────────────────────────────────

def test_off_always_passes():
    s = _s("off")
    assert s._name_matches("张三", "完全不相干的会话") is True


def test_unknown_mode_falls_back_to_strict():
    assert Scanner({"send_target_verify": "乱写的"})._verify_mode() == "strict"


# ── 两种模式都用在"找行"和"校验"两处 ──────────────────────────────────

def test_both_sites_use_the_shared_matcher():
    """三处调用：open_conversation 找行、verify_open 校验、confirm_opened 确认。

    全部走同一个匹配器 —— 别各写一套判断（松紧不一致就会出现
    "找得到但校验不过"这种自相矛盾的行为）。
    """
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent
           / "wxbot/scanner.py").read_text(encoding="utf-8")
    assert src.count("self._name_matches(") >= 2, \
        "找行与校验都用 self._name_matches（别各写一套判断）"
    for fn in ("def open_conversation", "def verify_open", "def confirm_opened"):
        assert fn in src
    # confirm_opened（点开后确认聊天区是不是这个人）也必须用同一个匹配器
    conf = src[src.index("def confirm_opened"):src.index("def verify_open")]
    assert "self._name_matches(" in conf
    assert "want not in (text or \"\")" not in src, "旧的字面包含判断应已替换掉"


# ── 配置 ──────────────────────────────────────────────────────────────

def test_config_ships_lenient():
    import json
    from pathlib import Path
    cfg = json.loads((Path(__file__).resolve().parent.parent
                      / "config.json").read_text(encoding="utf-8"))
    assert cfg.get("send_target_verify") == "lenient"
