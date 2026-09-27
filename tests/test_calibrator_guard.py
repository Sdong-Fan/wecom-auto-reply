"""标定器必须拒绝在"空白屏"上写出 profile。

实测踩到：微信 PC 没打开任何聊天会话时，右半边是纯白（暗像素 0），
标定器照样量出一堆数字并写盘 —— 那种 profile 用起来就是静默读错区域。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = (ROOT / "scripts/calibrate_chat_app.py").read_text(encoding="utf-8")


def test_refuses_to_write_when_chat_area_empty():
    assert "拒绝写 profile" in SRC
    # 守卫必须在写文件之前
    guard = SRC.index("拒绝写 profile")
    write = SRC.index('json.dump(profile, f')
    assert guard < write, "守卫要放在写盘之前，不能先写了再报警"
    assert "return 3" in SRC[guard - 200:guard + 800]


def test_guard_checks_both_theme_color_and_ocr():
    """两种空白都要拦：没主题色（没有我发的消息）与一个字都没识别到。"""
    seg = SRC[SRC.index("别在空白屏上写出垃圾 profile"):]
    seg = seg[:seg.index("人工确认要点")]
    assert "my_color is None" in seg
    assert "not rows" in seg


def test_guard_tells_user_what_to_do():
    seg = SRC[SRC.index("拒绝写 profile"):SRC.index("人工确认要点")]
    assert "打开一个" in seg and "聊天记录" in seg, "要告诉用户下一步怎么做"


def test_prints_grab_quality_before_calibrating():
    """先验抓图质量：微信是 GPU 合成窗口，jev 实测 PrintWindow 容易黑屏。"""
    assert "抓图质量" in SRC
    assert "windows-capture" in SRC, "黑帧要给出换 WGC 的退路"
    # 黑帧早退必须发生在写 profile 之前，否则会拿黑图算出一堆坐标
    assert SRC.index("整张黑") < SRC.index('json.dump(profile, f')
    assert 'grab_window(hwnd, check_blank=False)' in SRC, \
        "标定器要自己看黑帧（不能让它直接返回 None，那样看不到尺寸/亮度）"


def test_title_option_used_for_window_pick():
    """微信同进程还有工具窗/看图窗，必须按标题挑，且标题只有两个字「微信」。"""
    assert '"--title"' in SRC
    assert "args.title" in SRC
