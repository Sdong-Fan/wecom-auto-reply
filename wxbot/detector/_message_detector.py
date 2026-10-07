# wxbot/detector.py
"""消息检测器 — 红点 / 气泡 / OCR / 客户判定

所有参数通过 config dict 配置，提供合理默认值。
"""

import hashlib
import json
import logging
import time
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple, Optional

import numpy as np
from PIL import Image, ImageEnhance

# 注意：这里**不要**在模块顶层 import paddleocr。
# 默认 OCR 后端已经是 RapidOCR（wxbot/detector/ocr_engine.py），paddle 只是可回退项；
# 而顶层 import paddleocr 会连锁 import albumentations → torch，
# 在没有先 import torch 的进程里直接炸：
#     OSError: [WinError 127] Error loading torch/lib/shm.dll
# （实测：脚本单独 import wxbot.detector 就崩，main.py 只是因为先导入了 torch 才没事）
logger = logging.getLogger(__name__)

# 底部窗口扫不到气泡时的**全区域兜底**窗口（见 extract_bubbles_detail 的说明）。
# 取值近乎 0 = 把整个聊天区都纳入扫描；实测灰泡判定不会命中聊天区背景
# （背景 (245,247,250) 落在 gray_bubble 的 190–242 之外），所以不会扫出假气泡。
_WIDE_SCAN_BOTTOM = 0.01
_WIDE_SCAN_TOP = 0.01


# ═══ 数据结构 ══════════════════════════════

@dataclass
class Bubble:
    """气泡信息"""
    top: int
    bottom: int
    is_blue: bool
    image: Optional[Image.Image] = None
    nick_image: Optional[Image.Image] = None
    text: str = ""


@dataclass
class ChatState:
    """聊天状态"""
    has_red_dot: bool = False
    red_dot_positions: List[int] = field(default_factory=list)
    customer_name: str = ""
    is_customer: bool = False
    unreplied_messages: List[str] = field(default_factory=list)
    last_reply_is_ours: bool = False
    bubbles: List[Bubble] = field(default_factory=list)
    has_blue: bool = False


# ═══ 检测器 ═══════════════════════════════

class MessageDetector:
    """消息检测器 — 所有参数由 config 驱动"""

    def __init__(self, config: dict = None):
        self._cfg = config or {}

        self._ocr = None   # 懒加载的 PaddleOCR（回退后端），见 ocr property
        self._seen_col2: set = set()
        self._tried_y: dict = {}
        self._row_timeout: dict = {}   # y -> 该行单独的冷却秒数（默认用 _click_cooldown）
        self._row_fp: dict = {}   # y -> 该行「名字+预览」区域指纹（判断内容是否变化）
        self._last_reply = 0.0
        self._seen_messages: dict = {}  # OCR文本hash → (timestamp, original_text)
        # 「已经有人回过」的老气泡（人工回过也算）—— 单独一本**长记忆**账。
        # 为什么不跟 _seen_messages 合用：那本是给"待回复"的气泡用的短窗口账，
        # 两本混在一起必然二选一 —— 要么漏答（长窗口把新问题也吞了），
        # 要么重复回（短窗口拦不住重启后重读的旧气泡）。
        self._replied_seen: dict = {}
        self._cooldown = self._cfg.get("reply_cooldown_seconds", 10)
        # ★ 默认值本身就该是"安全值"：调用方只给了 `{"state_dir": ...}` 之类的
        #   局部配置时，用的是这里的默认 —— 实测配置里改成 120、但默认还留着 300/600，
        #   于是同一句话 147 秒后又被吞掉。两个窗口一起降（见下面那段注释）。
        self._msg_dedup_sec = self._cfg.get("message_dedup_seconds", 120)
        # 「这条已经回过」要**落盘**且记得久一点。原来只存在内存里、只有 5 分钟：
        # 实测 21:45 回过一句，21:59（中途重启过程序）又把同一段气泡读成"未回复"，
        # 于是同一句话回了客户两遍 —— 店主看到的就是"这个问题明明已经回过了"。
        self._seen_ttl = self._cfg.get("seen_message_ttl_seconds", 86400)
        # 「还没回过」的气泡只用**短窗口**去重。
        #
        # 为什么必须分层：长窗口（24h）是为了"回过的不再回"，但用在**待回复**的气泡上
        # 会漏答 —— 客户 10:00 问"有货吗"、11:00 又问一次同样的话，会被判成
        # "已经回过"→ 一声不吭。漏答比重复回严重得多。
        # 短窗口只用来防"同一轮/相邻轮重复处理同一个气泡"。
        #
        # ★ 2026-10-07 600→120：实测客户 18:06 问「索尼zve10有吗」、18:08 又发一遍
        #   （147 秒后），被 600 秒窗口判成"回过"→ **一声不吭**；而那个气泡
        #   **结构上明明是未回复的**（在最新蓝泡之下），是文字去重把它盖住了。
        #   OCR 抖动/同一轮扫描内的重复是**秒级**的 —— 120 秒绰绰有余，
        #   600 秒却会把"客户又问一遍"一起吞掉（漏答比重复回严重得多）。
        #   注意：光降这一个不够，模糊那层（`_msg_dedup_sec`）也得降，否则
        #   147 < 300 还会被它拦住。
        self._unreplied_dedup_sec = self._cfg.get("unreplied_dedup_seconds", 120)
        state = self._cfg.get("state_dir", "data/state")
        self._seen_path = Path(state) / "seen_messages.json"
        self._seen_saved_at = 0.0
        self._load_seen()

        # --- OCR ---
        ocr = self._cfg.get("ocr", {})
        self._ocr_backend = ocr.get("backend", "rapidocr")
        self._ocr_lang = ocr.get("lang", "ch")
        self._ocr_min_h = ocr.get("min_image_height", 50)
        self._ocr_enhance = ocr.get("enhance_contrast", False)
        self._ocr_contrast = ocr.get("contrast_factor", 2.0)
        self._cust_suffixes = ocr.get("customer_name_suffixes", ["@微信", ")微信"])
        # "suffix"=会话名必须带后缀（企业微信）；"any"=排除法（微信 PC 没有 @微信 后缀）
        self._cust_match = ocr.get("customer_match", "suffix")
        self._cust_pattern = ocr.get("customer_name_pattern", r'@[一-鿿]{2,}')
        self._ts_filter = ocr.get("timestamp_filter_pattern", r'^[\d:;.\-\s]+$')
        self._stop_keywords = ocr.get("stop_reply_keywords", [])
        self._sys_keywords = ocr.get(
            "non_customer_keywords",
            ["行业资讯", "企业微信日报", "系统通知", "工作通知",
             "日程提醒", "待办提醒", "会议邀请", "打卡提醒",
             "审批通知", "汇报通知", "公告通知",
             "此群为", "群公告", "群成员", "全员群", "群聊",
             "加入了群", "退出了群", "邀请你加入"],
        )

        # --- OCR 置信度 ---
        occ = self._cfg.get("ocr_confidence", {})
        self._conf_default = occ.get("default", 0.4)
        self._conf_name = occ.get("name_detection", 0.05)
        self._conf_bubble = occ.get("bubble_text", 0.15)

        # --- 去重与冷却 ---
        dedup = self._cfg.get("dedup", {})
        self._hash_cache_size = dedup.get("col2_hash_cache_size", 50)
        self._click_cooldown = dedup.get("click_cooldown_seconds", 60)
        # 回复冷却按客户分开记：给 A 回完话不该把 B 的消息一起挡住
        self._reply_by_customer: dict = {}

        # --- 各检测模块参数 ---
        det = self._cfg.get("detection", {})

        # 红点
        rd = det.get("red_dot", {})
        self._rd_scan_w_ratio = rd.get("scan_width_ratio", 0.05)
        self._rd_scan_w_min = rd.get("scan_width_min_px", 20)
        self._rd_r_min = rd.get("r_min", 90)
        self._rd_r_max = rd.get("r_max", 220)
        self._rd_gb_max = rd.get("gb_max", 130)
        self._rd_min_px = rd.get("min_pixels", 2)
        self._rd_excess_g = rd.get("red_excess_g", 20)
        self._rd_excess_b = rd.get("red_excess_b", 20)
        self._rd_cluster_gap = rd.get("cluster_gap", 5)
        self._rd_cluster_min = rd.get("cluster_min_points", 2)

        # 蓝泡
        bb = det.get("blue_bubble", {})
        self._bb_excess_r = bb.get("b_excess_r", 15)
        self._bb_min_rgb = bb.get("min_rgb", 200)
        self._bb_row_ratio = bb.get("row_threshold_ratio", 0.15)
        self._bb_right_ratio = bb.get("right_edge_ratio", 0.85)
        self._bb_max_h_ratio = bb.get("max_height_ratio", 0.15)
        self._bb_top_limit = bb.get("scan_top_limit_ratio", 0.05)
        self._bb_bottom_start = bb.get("scan_bottom_start_ratio", 0.15)

        # 灰泡
        gb = det.get("gray_bubble", {})
        self._gb_max_diff = gb.get("max_channel_diff", 10)
        self._gb_r_range = tuple(gb.get("r_range", [230, 254]))
        self._gb_g_range = tuple(gb.get("g_range", [230, 254]))
        self._gb_b_range = tuple(gb.get("b_range", [230, 254]))
        self._gb_row_ratio = gb.get("row_threshold_ratio", 0.15)
        self._gb_left_ratio = gb.get("left_edge_ratio", 0.15)
        self._gb_max_h_ratio = gb.get("max_height_ratio", 0.80)
        self._gb_min_h_px = gb.get("min_height_px", 20)
        self._gb_nick_offset = gb.get("nickname_top_offset", 15)

        # 绿badge
        grn = det.get("green_badge", {})
        self._grn_min_g = grn.get("min_green", 100)
        self._grn_excess = grn.get("green_excess_ratio", 1.3)
        self._grn_min_px = grn.get("min_green_pixels", 10)

        # 名字区域
        nr = det.get("name_region", {})
        self._nr_rgb_thr = nr.get("text_rgb_threshold", 100)
        self._nr_min_dark = nr.get("min_dark_pixels", 3)
        self._nr_width_chg = nr.get("width_change_ratio", 0.5)
        self._nr_min_w_ratio = nr.get("min_width_ratio", 0.15)
        self._nr_min_h_px = nr.get("min_height_px", 5)
        self._nr_pad_l = nr.get("crop_padding_left", 20)
        self._nr_pad_r = nr.get("crop_padding_right", 20)
        self._nr_pad_t = nr.get("crop_padding_top", 1)
        self._nr_pad_b = nr.get("crop_padding_bottom", 2)

    # ═══ OCR ════════════════════════════════

    @property
    def ocr(self):
        """PaddleOCR 实例（懒加载，且是回退后端；默认走 ocr_engine 的 RapidOCR）。

        保留这个属性只为兼容旧调用点。import 放在函数里，避免顶层 import
        把 paddle + albumentations + torch 一起拖进来（见文件顶部注释）。
        """
        if self._ocr is None:
            from paddleocr import PaddleOCR
            logger.info("Loading PaddleOCR...")
            self._ocr = PaddleOCR(lang=self._ocr_lang)
            logger.info("PaddleOCR ready")
        return self._ocr

    # PaddleOCR 误读的 WeChat UI 产物
    _DATE_ARTIFACT_RE = re.compile(
        r'^(周[一二三四五六日]|星期[一二三四五六日]|期[一二三四五六日])\s*'
    )
    # 语音转文字、系统提示等 UI 文字被 OCR 误读
    _UI_ARTIFACTS = [
        "√转换完成", "✓转换完成", "转换完成",
        "√ 转换完成", "✓ 转换完成",
    ]

    @staticmethod
    def _strip_ocr_artifacts(text: str) -> str:
        """Delegate to wxbot.detector.ocr_engine."""
        from wxbot.detector.ocr_engine import strip_ocr_artifacts
        return strip_ocr_artifacts(text)

    def extract_text(self, img: Image.Image, min_conf: float = None,
                     enhance: bool = None) -> str:
        """Delegate to wxbot.detector.ocr_engine."""
        from wxbot.detector.ocr_engine import extract_text
        ocr_cfg = {
            "backend": self._ocr_backend,
            "lang": self._ocr_lang,
            "min_image_height": self._ocr_min_h,
            "enhance_contrast": self._ocr_enhance,
            "contrast_factor": self._ocr_contrast,
            "timestamp_filter_pattern": self._ts_filter,
            "default_confidence": min_conf if min_conf is not None else self._conf_default,
        }
        return extract_text(img, config=ocr_cfg)

    # ═══ 红点检测 ═══════════════════════════

    def detect_red_dots(self, img: Image.Image) -> List[int]:
        """Delegate to wxbot.detector.red_dot for standalone red-dot detection."""
        from wxbot.detector.red_dot import detect_red_dots_legacy
        return detect_red_dots_legacy(img, config=self._cfg.get("detection", {}).get("red_dot"))

    # ═══ 图像去重 ═══════════════════════════

    def col2_seen(self, img: Image.Image) -> bool:
        h = hashlib.md5(img.tobytes()).hexdigest()
        if h in self._seen_col2:
            return True
        self._seen_col2.add(h)
        if len(self._seen_col2) > self._hash_cache_size:
            self._seen_col2.clear()
        return False

    def is_clickable(self, y: int, timeout: int = None,
                     fingerprint: str = None) -> bool:
        """该行现在是否值得处理。

        ``fingerprint`` 传该行"名字+消息预览"区域的指纹时：指纹与上次不同
        说明这一行内容变了（有新消息），直接放行、无视冷却。

        这一点很关键：冷却本意是"这行刚看过，别再花 OCR"，但客户的新消息
        永远把会话顶到行 0 —— 只按时间冷却会让同一客户 120 秒内的每一句
        追问都被跳过（实测就是这个问题）。按内容变化判断才对。
        """
        if timeout is None:
            timeout = self._row_timeout.get(y, self._click_cooldown)
        last = self._tried_y.get(y)
        if last is None:
            return True
        if fingerprint is not None and fingerprint != self._row_fp.get(y):
            return True
        return (time.time() - last) >= timeout

    def mark_clicked(self, y: int, fingerprint: str = None, timeout: int = None):
        """记下"这行刚处理过"。

        ``timeout`` 给一个比默认更长的冷却：确认"点开后聊天区是别人"时用 ——
        列表还在滚动，立刻重试同一行只是白花 2 秒 OCR。
        """
        self._tried_y[y] = time.time()
        if timeout is not None:
            self._row_timeout[y] = timeout
        if fingerprint is not None:
            self._row_fp[y] = fingerprint

    # ═══ 像素判定 ═══════════════════════════

    def _is_gray_pixel(self, r: int, g: int, b: int) -> bool:
        if max(r, g, b) - min(r, g, b) > self._gb_max_diff:
            return False
        return (self._gb_r_range[0] <= r <= self._gb_r_range[1]
                and self._gb_g_range[0] <= g <= self._gb_g_range[1]
                and self._gb_b_range[0] <= b <= self._gb_b_range[1])

    def _is_blue_pixel(self, r: int, g: int, b: int) -> bool:
        from wxbot.detector.bubble import is_blue_pixel
        bb = self._cfg.get("detection", {}).get("blue_bubble", {})
        return is_blue_pixel(r, g, b, config=bb)

    def mask_bubble(self, bubble: Image.Image) -> Image.Image:
        from wxbot.detector.bubble import mask_bubble
        gb = self._cfg.get("detection", {}).get("gray_bubble", {})
        return mask_bubble(bubble, config=gb)

    # ═══ 气泡扫描 ═══════════════════════════

    def extract_unreplied_bubbles(self, chat_img: Image.Image
                                  ) -> Tuple[Optional[List[Tuple[Image.Image, Image.Image]]], bool]:
        """没被回复过的客户气泡 + 有没有蓝泡（我们/人工回过的痕迹）。"""
        unreplied, has_blue, _replied = self._scan_bubbles(chat_img)
        return unreplied, has_blue

    def extract_bubbles_detail(self, chat_img: Image.Image):
        """``(未回复的气泡, 有没有蓝泡, 已经被回复过的客户气泡)``。

        **第三项是用来做"人工回过也算回过"的**：蓝泡（我们或店主手动发的）
        之上的客户气泡＝已经有人回过了。把它们的文字记下来，
        以后界面读到旧内容（列表重排、没刷新、OCR 多读进几条）也不会再回一遍。

        ★ 2026-10-05：底部窗口一条都没扫到时**全区域兜底重扫**。
        起因是真实故障：会话内容不足一屏时，企业微信把消息**顶部对齐**渲染，
        一条消息停在 y≈60，而底部窗口从 y=208 才开始扫 → 永远"无气泡/已回复"
        → 客户发消息完全没反应（另一个会话内容长、新消息在底部，所以照常工作）。
        """
        unreplied, has_blue, replied = self._scan_bubbles(chat_img)
        if unreplied is None:
            wide = self._scan_bubbles(chat_img, bottom_start=_WIDE_SCAN_BOTTOM,
                                      top_limit=_WIDE_SCAN_TOP)
            if wide[0] is not None:
                logger.info("底部扫描为空 → 全区域兜底扫到 %d 条气泡（内容可能不足一屏）",
                            len(wide[0]))
                return wide
        return unreplied, has_blue, replied

    def _scan_bubbles(self, chat_img: Image.Image,
                      bottom_start: float = None, top_limit: float = None):
        w, h = chat_img.size
        if w < 20 or h < 20:
            return None, False, []
        # 扫描窗口可覆盖：默认只扫视口底部（新消息总在底部），
        # 兜底调用时放宽到接近整个聊天区。
        bs = self._bb_bottom_start if bottom_start is None else bottom_start
        tl = self._bb_top_limit if top_limit is None else top_limit

        gray_thr = w * self._gb_row_ratio
        blue_thr = w * self._bb_row_ratio  # 蓝泡独立阈值，低于灰泡（文字行蓝色像素少）
        right_edge = int(w * self._bb_right_ratio)
        left_edge = int(w * self._gb_left_ratio)
        max_bubble_h = int(h * self._bb_max_h_ratio)
        bubbles = []

        # ── 逐行判定提前向量化 ────────────────────────────────────────
        # 原实现每行都遍历整行像素做 Python 循环：1089x1388 的聊天区约 150 万次
        # 迭代，单次耗时 ~0.85s，是整条回复链路里最大的一段可优化开销。
        # 这里用 numpy 一次算出每行的灰/蓝/文字像素统计，下面的扫描循环
        # 只做数组查表。判定条件逐条对应，结果完全等价。
        arr = np.asarray(chat_img.convert("RGB"), dtype=np.int16)
        pr, pg, pb = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]

        channel_diff = (np.maximum(np.maximum(pr, pg), pb)
                        - np.minimum(np.minimum(pr, pg), pb))
        gray_mask = ((pr >= self._gb_r_range[0]) & (pr <= self._gb_r_range[1])
                     & (pg >= self._gb_g_range[0]) & (pg <= self._gb_g_range[1])
                     & (pb >= self._gb_b_range[0]) & (pb <= self._gb_b_range[1])
                     & (channel_diff < self._gb_max_diff))
        gray_count = gray_mask.sum(axis=1)
        # 首个灰像素所在列；整行无灰像素时取 w（等价于原实现的 min(..., default=w)）
        gray_first = np.where(gray_mask.any(axis=1),
                              gray_mask.argmax(axis=1), w)

        blue_count = ((pb > pr + self._bb_excess_r) & (pb >= pg)
                      & (pb >= self._bb_min_rgb)).sum(axis=1)

        dark = (pr < 150) & (pg < 150) & (pb < 150)
        text_count = dark[:, :int(w * 0.4)].sum(axis=1)

        def _is_gray_row(y: int) -> bool:
            return bool(gray_count[y] >= gray_thr and gray_first[y] < left_edge)

        # 文字行检测: 左半区有深色像素 = 可能是语音转文字等非灰泡消息
        text_min_pixels = 3

        def _is_text_row(y: int) -> bool:
            return bool(text_count[y] >= text_min_pixels)

        # 蓝泡最小高度阈值：低于此值的蓝条是语音消息UI元素
        BLUE_MIN_HEIGHT = 15

        def _is_any_blue_row(y: int) -> bool:
            """当前行是否有足够蓝色像素（不检查文字），使用蓝泡独立阈值"""
            return bool(blue_count[y] >= blue_thr)

        y = h - 1
        while y > int(h * bs):
            # 检测蓝泡：连续蓝色行（含或不合文字），判为客服回复
            if _is_any_blue_row(y):
                bot = y
                while y > int(h * tl) and _is_any_blue_row(y):
                    y -= 1
                top = y + 1
                block_h = bot - top
                if block_h >= BLUE_MIN_HEIGHT:
                    # 真正的蓝泡 → 标记为蓝色气泡，阻断未回复收集
                    if block_h <= max_bubble_h:
                        bubbles.append((top, bot, True))
                # 薄蓝条（<BLUE_MIN_HEIGHT）→ 忽略，继续扫描
                continue

            # 检测灰泡（但不能吞掉蓝泡行 — 背景像素可能伪装成灰泡）
            if _is_gray_row(y) and not _is_any_blue_row(y):
                bot = y
                while y > int(h * tl) and _is_gray_row(y) and not _is_any_blue_row(y):
                    y -= 1
                top = y + 1
                if bot - top <= h * self._gb_max_h_ratio:
                    bubbles.append((top, bot, False))
                continue

            # 检测文字行（语音转文字等非灰泡消息）
            if _is_text_row(y) and not _is_any_blue_row(y):
                bot = y
                while y > int(h * tl) and _is_text_row(y) and not _is_any_blue_row(y):
                    y -= 1
                top = y + 1
                if bot - top <= h * 0.5:
                    bubbles.append((top, bot, False))
                continue

            y -= 1

        # 合并相邻非蓝泡碎片（灰泡被文字行切碎后合并恢复）
        MERGE_GAP = 8
        i = len(bubbles) - 1
        while i > 0:
            top_i, bot_i, blue_i = bubbles[i]
            top_j, bot_j, blue_j = bubbles[i - 1]
            if not blue_i and not blue_j and (top_j - bot_i) <= MERGE_GAP:
                # 合并：上气泡的 top + 下气泡的 bot
                bubbles[i - 1] = (top_i, bot_j, False)
                bubbles.pop(i)
            i -= 1

        has_blue = any(is_blue for _, _, is_blue in bubbles)
        logger.debug(f"bubbles={len(bubbles)} hasBlue={has_blue} "
                     f"blue_count={sum(1 for _,_,b in bubbles if b)}")

        # 找最底部蓝泡的位置（客服最新回复）
        newest_blue_top = None
        for top, bot, is_blue in bubbles:
            if is_blue and (newest_blue_top is None or top > newest_blue_top):
                newest_blue_top = top

        # 收集未回复气泡：只取底部蓝泡之下的内容
        # 蓝泡**之上**的客户气泡＝已经有人回过了（我们自动回的，或店主手动回的）
        #
        # 注意：`bubbles` 是**从下往上**收集的，所以蓝泡最先遇到。
        # 这里**不能 break** —— 一 break 就再也看不到蓝泡上面的气泡了，
        # "人工回过也算回过"就无从判定。
        unreplied = []
        replied = []
        for top, bot, is_blue in bubbles:
            if is_blue:
                continue                  # 蓝泡只用来定边界
            if bot - top < 5:
                continue
            if newest_blue_top is not None and top < newest_blue_top:
                replied.append(chat_img.crop((0, top, w, bot + 1)))
                continue
            nick_top = max(0, top - self._gb_nick_offset)
            bubble = chat_img.crop((0, top, w, bot + 1))
            bubble_nick = chat_img.crop((0, nick_top, w, bot + 1))
            unreplied.append((bubble, bubble_nick))

        return (unreplied if unreplied else None), has_blue, replied

    def extract_bottom_bubble(self, chat_img: Image.Image
                              ) -> Tuple[Optional[Image.Image], bool, Optional[Image.Image]]:
        unreplied, has_blue = self.extract_unreplied_bubbles(chat_img)
        if unreplied:
            bubble, bubble_nick = unreplied[-1]
            return bubble, False, bubble_nick
        if has_blue:
            return None, True, None
        return None, False, None

    # ═══ 名字检测 ═══════════════════════════

    def detect_name_region(self, row_img: Image.Image) -> Optional[Image.Image]:
        """Delegate to wxbot.detector.name_region."""
        from wxbot.detector.name_region import detect_name_region
        return detect_name_region(row_img, config=self._cfg.get("detection", {}).get("name_region"))

    def read_row_name(self, row_img: Image.Image):
        """读一行的会话名，返回 (名字区图, OCR 文本)。

        ``detect_name_region`` 靠暗像素宽度变化去猜名字区位置，对某些行
        （实测：新消息把会话顶到最上面那几行）只裁出几像素高的碎片，
        OCR 必然为空 —— 结果那个客户被当成非客户跳过、消息永远不回。

        所以这里加一层兜底：启发式裁出来的区域太扁或 OCR 读不出字时，
        改按固定几何裁行顶部约 45%（实测名字行就落在这里）。
        """
        nc = self.detect_name_region(row_img)
        text = ""
        if nc is not None and nc.size[1] >= 12:
            text = self.extract_text(nc, min_conf=self._conf_name)
        if text:
            return nc, text

        w, h = row_img.size
        fixed = row_img.crop((0, 0, w, max(12, int(h * 0.45))))
        text2 = self.extract_text(fixed, min_conf=self._conf_name)
        if text2:
            return fixed, text2
        return nc, text

    # ═══ 绿badge ═══════════════════════════

    def has_green_badge(self, img: Image.Image) -> bool:
        """Delegate to wxbot.detector.name_region."""
        from wxbot.detector.name_region import has_green_badge
        return has_green_badge(img, config=self._cfg.get("detection", {}).get("green_badge"))

    # ═══ 客户判定 ═══════════════════════════

    def is_customer_name(self, name_img: Image.Image = None,
                         ocr_text: str = "") -> bool:
        """对话名判定。

        两种规则（由 profile 里的 ``customers.name_rule`` 决定，见 wxbot/profile.py）：

        * ``suffix``（默认，企业微信）：会话名带 ``@微信`` / ``)微信`` 才是客户 ——
          企业微信里同事、部门群、系统号都不带这个后缀。
        * ``any``（微信 PC）：微信客户端里**不存在** ``@微信`` 后缀（那本来就是
          "微信用户加了企业微信" 之后才显示的），所以按"排除法"判 ——
          只要不是系统号/群聊等排除项，就算客户。
        """
        if not ocr_text:
            return False
        if self._cust_match == "any":
            return not any(k and k in ocr_text for k in self._sys_keywords)
        for suffix in self._cust_suffixes:
            if suffix in ocr_text:
                return True
        if re.search(self._cust_pattern, ocr_text):
            return True
        return False

    _NAME_SYS_MARKERS = [
        "[语音]", "[图片]", "[视频]", "[文件]", "[链接]", "[位置]",
        "[红包]", "[名片]", "[小程序]", "[聊天记录]", "[动画表情]",
        "[通话]", "[位置共享]",
    ]

    def clean_name_text(self, name_text: str) -> str:
        """清理OCR名字中的系统标记（[语音]、[图片]等），并截取客户名片段"""
        if not name_text:
            return ""
        # 1. 去掉系统消息标记
        for marker in self._NAME_SYS_MARKERS:
            name_text = name_text.replace(marker, "")
        name_text = name_text.strip()
        if not name_text:
            return ""
        # 2. 截取到客户后缀（@微信等）结束，去除后面的消息预览文字
        for suffix in self._cust_suffixes:
            idx = name_text.find(suffix)
            if idx >= 0:
                end = idx + len(suffix)
                name_text = name_text[:end]
                break
        return name_text.strip()

    def is_customer(self, bubble_with_nick: Image.Image,
                    has_blue: bool = False, text: str = "") -> bool:
        """客户判定三层"""
        if has_blue:
            return True

        if bubble_with_nick is not None:
            if self.has_green_badge(bubble_with_nick):
                return True
            nick_text = self.extract_text(bubble_with_nick)
            if nick_text:
                for suffix in self._cust_suffixes:
                    if suffix in nick_text:
                        return True
                if re.search(self._cust_pattern, nick_text):
                    return True
            else:
                # 昵称区域存在但OCR失败，不判定为客户
                return False

        if text:
            for kw in self._sys_keywords:
                if kw in text:
                    return False
            return True

        return False

    # ═══ 冷却 ═══════════════════════════════

    def is_in_cooldown(self, customer_name: str = None) -> bool:
        """回复冷却。

        传 ``customer_name``：只看**这个客户自己**的冷却。原来是全局 30 秒 ——
        给 A 回完话之后，同一个时间窗里 B 发来的消息会被兜底扫描整个跳过
        （实测多客户场景会漏人）。
        不传：退回全局冷却，兼容旧调用。
        """
        now = time.time()
        if customer_name:
            return now - self._reply_by_customer.get(customer_name, 0) < self._cooldown
        return now - self._last_reply < self._cooldown

    def mark_replied(self, customer_name: str = None):
        """记录一次回复。带客户名时同时记该客户自己的冷却时间。"""
        self._last_reply = time.time()
        if customer_name:
            self._reply_by_customer[customer_name] = self._last_reply

    def is_message_seen(self, customer_name: str, text: str,
                        long_term: bool = True) -> bool:
        """检查OCR文本是否近期已处理过（防止重复回复）

        三层检查：
        1. 「已经有人回过」的老气泡 —— 长记忆（``seen_message_ttl_seconds``，
           24h），重启也不忘。**这一层不受 ``long_term`` 影响**。
        2. 精确哈希匹配 (customer_name:text) —— 窗口由 ``long_term`` 决定
        3. 模糊子串匹配 —— 同一客户下，兜底路径OCR可能只产生红点路径文本的子集

        ``long_term``：
          ``True``（默认）＝「这条**回过了**吧」—— 用 24h 长记忆。
          ``False`` ＝「这条**还没回过**，要不要现在回」—— 只用短窗口
          （``unreplied_dedup_seconds``）。长记忆用在这里会**漏答**：
          客户 10:00 问「有货吗」、11:00 又问一次同样的话，会被判成"已经回过"，
          于是一声不吭。漏答比重复回严重得多。
        """
        if not text or len(text) < 3:
            return False
        dedup_key = f"{customer_name}:{text}"
        h = hashlib.md5(dedup_key.encode()).hexdigest()
        now = time.time()
        # 1. 有人回过的老气泡（人工回过也算回过）—— 长记忆
        got = self._replied_seen.get(h)
        if got and now - got[0] < self._seen_ttl:
            return True
        # 2. 精确匹配：窗口看 long_term
        window = self._seen_ttl if long_term else self._unreplied_dedup_sec
        got = self._seen_messages.get(h)
        if got and now - got[0] < window:
            return True
        # 模糊匹配：同一客户下，兜底路径可能产生红点路径的子串
        for seen_h, (ts, seen_key) in list(self._seen_messages.items()):
            if now - ts > self._msg_dedup_sec:
                continue
            # 只对同一客户的记录做模糊匹配
            if not seen_key.startswith(f"{customer_name}:"):
                continue
            seen_text = seen_key[len(customer_name) + 1:]
            if len(text) >= 4 and len(seen_text) >= 4:
                if text in seen_text or seen_text in text:
                    # OCR accumulates visible bubbles: "A" seen, then
                    # "B A" appears — that's a NEW message, not a dup.
                    # Only dedup when lengths are comparable (ratio >= 0.6).
                    ratio = min(len(text), len(seen_text)) / max(len(text), len(seen_text))
                    if ratio >= 0.6:
                        logger.info(f"消息模糊去重: [{text[:40]}] ⊆ [{seen_text[:40]}]")
                        return True
        return False

    def mark_message_seen(self, customer_name: str, text: str):
        """标记OCR文本已处理（**会落盘**，重启不忘）"""
        if text:
            dedup_key = f"{customer_name}:{text}"
            h = hashlib.md5(dedup_key.encode()).hexdigest()
            self._seen_messages[h] = (time.time(), dedup_key)
            self._maybe_save_seen()

    def unseen_texts(self, customer_name: str, texts,
                     long_term: bool = False) -> list:
        """从一堆气泡文本里挑出**还没处理过**的。

        为什么要逐条挑：整段合并文本只要多读进一个新气泡就对不上，
        于是"旧气泡 + 一个新气泡"会被当成全新消息，把旧问题再回一遍。

        ``long_term=False``（默认）：这里挑的是**待回复**的气泡，短窗口就够 ——
        客户隔一阵子又问同一句话，要能正常回答（见 ``is_message_seen``）。
        """
        return [t for t in (texts or [])
                if t and not self.is_message_seen(customer_name, t,
                                                  long_term=long_term)]

    def mark_messages_seen(self, customer_name: str, texts,
                           already_replied: bool = False):
        """把这一批气泡**逐条**记为已处理（下次才能逐条过滤）。

        ``already_replied=True``：这些气泡**上面已经有我们的蓝泡**，
        也就是已经有人回过了 —— 同时记进 24h 长记忆。界面重排 / 没刷新 /
        程序重启之后又把它们读成"未回复"时，长记忆能拦住
        （实测 21:45 回过一句，21:59 中途重启后同一段气泡又回了一遍）。
        """
        for t in (texts or []):
            if t:
                dedup_key = f"{customer_name}:{t}"
                h = hashlib.md5(dedup_key.encode()).hexdigest()
                self._seen_messages[h] = (time.time(), dedup_key)
                if already_replied:
                    self._replied_seen[h] = (time.time(), dedup_key)
        self._maybe_save_seen()

    # ── 「已处理消息」的落盘 ────────────────────────────────────────

    def _load_seen(self):
        try:
            if not self._seen_path.is_file():
                return
            raw = json.loads(self._seen_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"读取已处理消息记录失败（不影响运行）: {e}")
            return
        now = time.time()
        n = 0
        for h, item in (raw.get("seen") or {}).items():
            try:
                ts, key = float(item[0]), str(item[1])
            except Exception:
                continue
            if now - ts < self._seen_ttl:
                self._seen_messages[h] = (ts, key)
                n += 1
        if n:
            logger.info(f"读回 {n} 条已处理消息记录（重启后不会重复回复）")
        # 「已经有人回过」的长记忆（24h）
        n2 = 0
        for h, item in (raw.get("replied") or {}).items():
            try:
                ts, key = float(item[0]), str(item[1])
            except Exception:
                continue
            if now - ts < self._seen_ttl:
                self._replied_seen[h] = (ts, key)
                n2 += 1
        if n2:
            logger.info(f"读回 {n2} 条「已经回过」记录（重启后不会重复回复）")

    def _maybe_save_seen(self, force: bool = False):
        """节流落盘：最多每 2 秒写一次，别把磁盘写爆。"""
        now = time.time()
        if not force and now - self._seen_saved_at < 2.0:
            return
        self._seen_saved_at = now
        try:
            self._seen_path.parent.mkdir(parents=True, exist_ok=True)
            keep = {h: v for h, v in self._seen_messages.items()
                    if now - v[0] < self._seen_ttl}
            self._seen_messages = keep
            keep_replied = {h: v for h, v in self._replied_seen.items()
                            if now - v[0] < self._seen_ttl}
            self._replied_seen = keep_replied
            tmp = self._seen_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"seen": keep,
                                       "replied": keep_replied},
                                      ensure_ascii=False),
                           encoding="utf-8")
            tmp.replace(self._seen_path)
        except Exception as e:
            logger.warning(f"保存已处理消息记录失败（不影响运行）: {e}")

    def mark_text_sent(self, text: str):
        """记录刚发送的回复文本，用于防止将自己的回复识别为客户消息"""
        if not text:
            return
        # 取前60字符做特征（太短不可靠，太长浪费）
        key = text[:60].strip()
        if len(key) < 1:
            return
        if not hasattr(self, '_sent_texts'):
            self._sent_texts: dict = {}
        self._sent_texts[key] = time.time()

    def contains_own_reply(self, ocr_text: str) -> bool:
        """检查OCR结果是否包含自己刚发送的回复文本"""
        if not ocr_text or not hasattr(self, '_sent_texts'):
            return False
        now = time.time()
        # 清理过期 (>600s)
        self._sent_texts = {k: v for k, v in self._sent_texts.items()
                            if now - v < 600}
        for sent_text, ts in self._sent_texts.items():
            # 短回复(<4字)跳过子串匹配：太短容易误判
            # 如"好"会匹配到客户说的"好的"、"您好"
            if len(sent_text) < 4:
                continue
            if sent_text in ocr_text:
                logger.info(f"OCR包含自己发送的回复，跳过: {sent_text[:40]}...")
                return True
        return False

    def has_stop_keyword(self, text: str) -> bool:
        """检查文本是否包含停止回复关键词"""
        for kw in self._stop_keywords:
            if kw in text:
                return True
        return False

    def clear_seen(self):
        """清除已见集合。

        ★ **只清短窗口那本账**（``_seen_messages``）。「已经有人回过」的长记忆
        （``_replied_seen``，24h）必须留着 —— 每次自动回复后都会调这个函数，
        原来顺手按 300 秒剪一刀，等于把"回过了"的记忆一起抹掉，
        界面再读到旧气泡时又会回一遍（21:59 重复回复的原因之一）。
        """
        self._seen_col2.clear()
        # 清理过期消息哈希（v[0] = timestamp in (ts, text) tuple）
        now = time.time()
        self._seen_messages = {k: v for k, v in self._seen_messages.items()
                               if now - v[0] < self._msg_dedup_sec}
        self._replied_seen = {k: v for k, v in self._replied_seen.items()
                              if now - v[0] < self._seen_ttl}
