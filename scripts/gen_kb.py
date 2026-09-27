#!/usr/bin/env python3
"""摄影器材租赁网店 —— 客服知识库生成器。

把结构化的产品/FAQ 数据渲染成 `data/chat_raw/*.txt`（企业微信 PC 端复制格式），
供 pipeline 建向量库使用。

用法：
    python scripts/gen_kb.py            # 生成全部文件
    python scripts/gen_kb.py --check    # 只做校验，不写文件

设计要点（都是踩过的坑）：
1. 输出必须是 UTF-8；客户行含 `@微信` 才会被判定为客户。
2. 每个主题写多条客户问法（各自独立成一对），提高检索命中率 —— 分数 > 0.7 才自动发送。
3. 答案里出现的**每个数字都必须写在答案里**：rag/guard.py 的数字校验会拦截
   "回复中 ≥30% 的数字不在知识片段里" 的情况（防编造价格）。
4. 答案不得包含 guard 的封禁词（`可能`/`也许`/`请咨询`/`请联系客服` 等），
   否则回复被判 block 或降级 low_risk，永远进不了自动发送。
"""
import argparse
import os
import re
import sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "data", "chat_raw")

SALES = "客服小朱"
CUSTOMERS = ["小陈", "阿伟", "小林", "张女士", "王工", "老李",
             "阿杰", "周先生", "小雅", "刘经理"]

# ══════════════════════════════════════════════════════════════════════
#  产品库：(型号, 日租, 押金, 说明)
# ══════════════════════════════════════════════════════════════════════

BODIES = [
    ("索尼 A7M4", 90, 4000, "3300 万像素，4K 60 帧，五轴防抖，最热门的全能机型"),
    ("索尼 A7R5", 160, 8000, "6100 万像素，8K 视频，适合商业静物和大画幅输出"),
    ("索尼 A7C2", 80, 3500, "3300 万像素，机身轻便，适合旅行和日常拍摄"),
    ("佳能 R6 Mark II", 100, 4500, "2420 万像素，40 张每秒连拍，人像肤色讨喜"),
    ("佳能 R5", 150, 7000, "4500 万像素，8K 30 帧，双卡槽设计"),
    ("尼康 Z6III", 110, 5000, "2450 万像素，6K 视频，弱光表现优秀"),
    ("富士 X-T5", 95, 4000, "4020 万像素，胶片模拟直出，适合人文街拍"),
    ("松下 S5M2", 85, 3800, "2420 万像素，相位对焦，视频规格高"),
]

LENSES = [
    ("索尼 24-70 F2.8 GM II", 70, 5000),
    ("索尼 70-200 F2.8 GM II", 100, 6000),
    ("索尼 16-35 F2.8 GM", 65, 4500),
    ("索尼 85 F1.4 GM", 55, 3500),
    ("佳能 RF 24-70 F2.8", 75, 5000),
    ("适马 35 F1.4 DG DN", 40, 2000),
    ("腾龙 28-75 F2.8 G2", 35, 1800),
]

ACCESSORIES = [
    ("大疆 RS4 稳定器", 50, 2000),
    ("大疆 RS3 Mini 稳定器", 35, 1200),
    ("神牛 V1 闪光灯", 25, 800),
    ("神牛 SL60 常亮灯套装", 45, 1500),
    ("罗德 Wireless GO II 麦克风", 30, 1000),
    ("曼富图 055 三脚架", 20, 600),
    ("5 寸监视器", 25, 800),
    ("1.2 米电动滑轨", 40, 1200),
    ("大疆 Air 3 无人机", 180, 6000),
    ("索尼 64G CFexpress A 存储卡", 15, 500),
]

# ══════════════════════════════════════════════════════════════════════
#  FAQ：(主题, [客户问法...], 答案)
# ══════════════════════════════════════════════════════════════════════

FAQS = [
    ("起租天数",
     ["最少租几天", "起租天数是多少", "可以只租一天吗"],
     "起租 3 天，也就是最短按 3 天计费。租 1 天也可以下单，租金仍按 3 天起算。"),

    ("租期计算方式",
     ["租期从哪天开始算", "租期怎么计算", "物流在途时间算租期吗"],
     "从签收次日 0 点开始计租，到寄回当天 24 点结束。物流在途时间不计入租期，"
     "所以收到货当天不收费。"),

    ("续租",
     ["想续租怎么操作", "到期了还能续吗", "续租要重新交押金吗"],
     "到期前一天告诉我们即可续租，续租按原日租价的 9 折计算，不需要重新交押金。"),

    ("提前归还",
     ["提前归还怎么算", "用两天就还回去可以吗", "提前还退租金吗"],
     "提前归还按实际使用天数计费，多付的租金在还机验收后原路退回。"),

    ("长期租优惠",
     ["租久一点有优惠吗", "月租有折扣吗", "长租怎么算"],
     "7 天 9 折，15 天 8.5 折，30 天 8 折，租期越长日单价越低。"),

    ("学生优惠",
     ["学生有优惠吗", "学生证能便宜吗", "学生租有折扣吗"],
     "凭学生证首单立减 30 元，和租期折扣可以叠加使用。"),

    ("老客户优惠",
     ["老客户有优惠吗", "租过几次有折扣吗", "复租有优惠吗"],
     "累计租满 10 次升级老客户，之后全场租金 9 折，长期有效。"),

    ("打包套餐",
     ["机身和镜头一起租便宜吗", "有套餐吗", "打包租有优惠吗"],
     "机身加 2 支镜头打包租金按 9 折计算，配件一起租同样享受 9 折。"),

    ("押金标准",
     ["押金多少", "押金怎么算", "要交多少押金"],
     "押金按设备市场价的 3 成收取。例如索尼 A7M4 押金 4000 元，"
     "佳能 R5 押金 7000 元，还机验收无损后原路退回。"),

    ("芝麻信用免押",
     ["可以免押金吗", "芝麻信用能免押吗", "信用分高能免押吗"],
     "芝麻信用分 650 以上支持免押租机，下单时选择芝麻信用免押，通过后不用付押金。"),

    ("支付方式",
     ["怎么付款", "支持哪些支付方式", "可以公对公转账吗",
      "可以用花呗吗", "支持信用卡吗"],
     "支持微信、支付宝（支付宝内可用花呗和信用卡）、对公转账三种方式，"
     "对公转账需提供开票信息。"),

    ("押金退还时间",
     ["押金什么时候退", "押金几天到账", "还机后押金多久退"],
     "还机验收无损后当天提交退款：微信和支付宝 24 小时内到账，"
     "对公转账 3 个工作日内到账。"),

    ("押金能否抵租金",
     ["押金可以抵租金吗", "押金能当租金用吗"],
     "押金不能抵扣租金，租金需单独支付；押金在还机验收后全额退回。"),

    ("运费与包邮",
     ["运费谁出啊", "你们包邮吗", "寄回运费谁承担", "怎么还要运费",
      "运费怎么算", "要运费吗"],
     "单笔租金满 200 元顺丰包邮，不满 200 元收 15 元运费；寄回运费由我们承担。"),

    ("快递与时效",
     ["发什么快递", "几天能到", "多久能收到"],
     "默认顺丰空运，江浙沪次日达，其他省份 1 到 3 天，新疆西藏内蒙古 3 到 5 天。"),

    ("发货时间",
     ["今天下单今天能发吗", "几点前下单当天发货", "发货时间是几点"],
     "每天 16 点前下单当天发出，16 点后下单次日发出。"),

    ("自提与门店地址",
     ["可以自提吗", "自提地址在哪", "门店在哪里",
      "线下店在哪", "实体店在哪", "你们店在哪", "有线下门店吗",
      "自提点在哪个位置", "门店的具体地址"],
     "我们在深圳福田华强北有线下门店，支持自提。工作日 10 点到 19 点营业，"
     "需提前 2 小时预约，自提免运费。"),

    ("偏远地区发货",
     ["新疆能发吗", "偏远地区发货吗", "西藏能租吗"],
     "全国可发，新疆西藏内蒙古 3 到 5 天到达，偏远地区需加购异地保障 20 元。"),

    ("跨省与出境",
     ["可以带到外地拍吗", "能带出国吗", "出差携带可以吗"],
     "国内跨省拍摄需加购异地保障 20 元；出境租赁需提前 3 天申请，"
     "并加购出境保障 80 元。"),

    ("发票",
     ["能开发票吗", "可以开专票吗", "支持开票吗"],
     "可以开 6% 增值税专用发票和普通发票，下单时备注公司名称和税号，"
     "发票随设备一起寄出。"),

    ("租赁合同",
     ["可以签合同吗", "对公租赁要签合同吗", "有租赁协议吗",
      "能签协议吗", "签合同的流程是什么"],
     "对公租赁提供正式租赁合同，加盖公章后随设备寄出，也可以先寄合同再发货。"),

    ("损坏赔付标准",
     ["设备坏了怎么赔", "摔坏了怎么办", "损坏赔付标准是什么"],
     "轻微磕碰不收费；屏幕、卡口、镜片损坏按官方维修报价赔付；"
     "机身进水按设备残值 50% 赔付。"),

    ("意外保障",
     ["有保险吗", "意外保障多少钱", "需要买保险吗"],
     "意外保障 30 元一单，覆盖轻微磕碰、意外跌落和进水，购买后这几类损坏免赔。"),

    ("设备丢失",
     ["设备丢了怎么办", "丢了怎么赔", "遗失赔付标准"],
     "设备丢失按市场残值赔付，例如索尼 A7M4 残值 12000 元，已付押金可以抵扣。"),

    ("收到货有问题",
     ["收到货有问题怎么办", "开箱是坏的怎么办", "收到坏的怎么处理",
      "机器到手是坏的", "收到机器有故障"],
     "签收 24 小时内提供开箱视频，我们免费换机并承担往返运费。"),

    ("拍摄中故障",
     ["拍摄中坏了怎么办", "用着用着故障了怎么办", "现场设备出问题"],
     "拍摄中故障立即联系我们，深圳同城 4 小时内送替换机，其他城市顺丰次日送到。"),

    ("标配配件",
     ["配件都带什么", "含哪些配件", "自带什么配件"],
     "机身标配原装电池 2 块、充电器、背带、机身盖和防潮箱；"
     "镜头配前后盖和 UV 镜；存储卡需单独租。"),

    ("存储卡",
     ["存储卡要自己带吗", "提供存储卡吗", "卡怎么租"],
     "存储卡需单独租，索尼 64G CFexpress A 卡日租 15 元、押金 500 元，也可以自带。"),

    ("电池",
     ["电池带几块", "电池够用一天吗", "需要自己带电池吗"],
     "每台机身配 2 块原装电池，各带充电器；全天外拍建议加租 1 块备用电池，日租 15 元。"),

    ("营业时间",
     ["你们上班时间是几点", "营业时间", "几点上班"],
     "工作时间每天 9 点到 18 点，中午 12 点到 13 点半休息，周末和法定节假日照常值班。"),

    ("周末营业",
     ["周末上班吗", "双休日有人吗", "周六日能下单吗",
      "周六上班吗", "周日有人值班吗", "节假日休息吗"],
     "周末正常营业，值班时间 10 点到 17 点，可以正常下单和寄回设备。"),

    ("夜间客服",
     ["晚上能联系到你们吗", "下班后还回消息吗", "夜间有人值班吗"],
     "18 点以后留言，客服次日 9 点前统一回复，紧急情况可拨打客服电话。"),

    ("联系方式",
     ["客服电话多少", "怎么联系你们", "微信客服号是多少",
      "电话号码是多少", "客服微信多少"],
     "客服电话 0755-88886666，微信同号 13800001234，也可以直接在企微上留言。"),

    ("到店试机",
     ["可以到店试机吗", "能先试试机器吗", "到店看机器"],
     "支持到店试机，深圳福田华强北门店工作日 10 点到 19 点开放，需提前 2 小时预约。"),

    ("使用指导",
     ["有使用教学吗", "不会用能教吗", "提供操作指导吗"],
     "提供免费 30 分钟线上一对一使用指导，下单后预约时间即可。"),

    ("影像数据隐私",
     ["相机里的照片会保留吗", "归还前要清空吗", "数据安全怎么保障"],
     "归还前请自行备份并格式化存储卡，我们不查看也不保留任何影像数据。"),

    ("指定收货时间",
     ["可以指定时间送到吗", "能约周六收货吗", "可以指定日期发货吗"],
     "下单时备注期望收货日期，我们按备注安排发货，急单可加急处理。"),

    ("企业批量租赁",
     ["企业批量租有优惠吗", "公司长期合作怎么谈", "批量租赁价格"],
     "单次租 5 台以上可谈企业价，签订年度框架合同后全场租金 8 折，并支持月结。"),

    ("推荐返现",
     ["推荐朋友有奖励吗", "有返现吗", "老带新有优惠吗"],
     "推荐新客户下单，双方各得 50 元租金抵扣券，抵扣券可以叠加使用。"),
]

# ══════════════════════════════════════════════════════════════════════
#  渲染
# ══════════════════════════════════════════════════════════════════════


class Emitter:
    """按「客户提问 -> 销售回答」成对输出，时间戳自动递增。"""

    def __init__(self, start=None):
        self.t = start or datetime(2026, 7, 1, 9, 0, 0)
        self.buf = []
        self.n = 0

    def _stamp(self):
        s = self.t.strftime("%-m/%-d %H:%M:%S") if os.name != "nt" \
            else f"{self.t.month}/{self.t.day} {self.t.strftime('%H:%M:%S')}"
        return s

    def pair(self, q: str, a: str):
        cust = CUSTOMERS[self.n % len(CUSTOMERS)]
        self.buf.append(f"{cust}@微信@微信联系人 {self._stamp()}")
        self.buf.append(q)
        self.t = self.t + timedelta(seconds=37)
        self.buf.append(f"{SALES} {self._stamp()}")
        self.buf.append(a)
        self.t = self.t + timedelta(seconds=90)
        self.n += 1

    def text(self):
        return "\n".join(self.buf) + "\n"


def build_products():
    em = Emitter()
    # 机身：3 种问法
    for model, day, dep, note in BODIES:
        ans = f"{model} 日租 {day} 元，押金 {dep} 元。{note}。租期 3 天起，7 天以上有折扣。"
        for q in (f"{model}一天多少钱", f"{model}押金多少", f"想租{model}"):
            em.pair(q, ans)
    return em.text()


def build_lenses():
    em = Emitter()
    for model, day, dep in LENSES:
        ans = f"{model} 日租 {day} 元，押金 {dep} 元。和机身一起租，总租金按 9 折计算。"
        for q in (f"{model}日租多少钱", f"{model}押金多少"):
            em.pair(q, ans)
    return em.text()


def build_accessories():
    em = Emitter()
    for model, day, dep in ACCESSORIES:
        ans = f"{model} 日租 {day} 元，押金 {dep} 元。和机身一起租，总租金按 9 折计算。"
        for q in (f"{model}怎么租", f"{model}日租多少"):
            em.pair(q, ans)
    return em.text()


def build_pricelist():
    em = Emitter()
    body = "、".join(f"{m} 日租 {d} 元" for m, d, _p, _n in BODIES)
    lens = "、".join(f"{m} 日租 {d} 元" for m, d, _p in LENSES)
    acc = "、".join(f"{m} 日租 {d} 元" for m, d, _p in ACCESSORIES)
    for q in ("价格表发我一下", "你们都有什么设备", "租金一览表", "有哪些机器可以租"):
        em.pair(q, f"机身：{body}。")
        em.pair(q, f"镜头：{lens}。")
        em.pair(q, f"配件：{acc}。")
    return em.text()


def build_overview():
    """镜头/配件的通用问法 —— 客户常按品类问而不说具体型号。"""
    em = Emitter()
    lens = "、".join(f"{m} 日租 {d} 元" for m, d, _p in LENSES)
    for q in ("镜头怎么租", "有什么镜头", "镜头多少钱", "长焦镜头有吗",
              "定焦镜头有吗", "变焦镜头怎么租", "24-70镜头有吗"):
        em.pair(q, f"镜头租金：{lens}。和机身一起租，总租金按 9 折计算。")
    acc = "、".join(f"{m} 日租 {d} 元" for m, d, _p in ACCESSORIES)
    for q in ("配件怎么租", "有什么配件", "稳定器怎么收费", "灯光怎么租",
              "麦克风能租吗", "三脚架有吗", "无人机能租吗", "监视器有吗",
              "云台怎么租"):
        em.pair(q, f"配件租金：{acc}。和机身一起租，总租金按 9 折计算。")
    return em.text()


def brand_faqs():
    """按品牌生成机型清单问答 —— 客户常按品牌找机器。"""
    groups = {}
    for m, d, _p, _n in BODIES:
        groups.setdefault(m.split()[0], []).append((m, d))
    result = []
    for brand, items in groups.items():
        listing = "、".join(f"{m} 日租 {d} 元" for m, d in items)
        result.append((
            f"{brand}品牌机型",
            [f"有{brand}的机器吗", f"{brand}有哪些型号", f"{brand}的机器怎么租"],
            f"{brand}的机型有：{listing}。押金按市场价 3 成收取，租期 3 天起。",
        ))
    return result


def build_faq(lo, hi):
    em = Emitter()
    for topic, questions, answer in FAQS[lo:hi]:
        for q in questions:
            em.pair(q, answer)
    return em.text()


FILES = [
    ("01-机身租赁价格.txt", build_products),
    ("02-镜头租赁价格.txt", build_lenses),
    ("03-配件租赁价格.txt", build_accessories),
    ("04-全品类价格表.txt", build_pricelist),
    ("05-租期与优惠.txt", lambda: build_faq(0, 9)),
    ("06-押金与支付.txt", lambda: build_faq(9, 13)),
    ("07-物流与自提.txt", lambda: build_faq(13, 19)),
    ("08-发票与合同.txt", lambda: build_faq(19, 21)),
    ("09-损坏与保障.txt", lambda: build_faq(21, 25)),
    ("10-售后与故障.txt", lambda: build_faq(25, 30)),
    ("11-营业与联系.txt", lambda: build_faq(30, 35)),
    ("12-其他常见问题.txt", lambda: build_faq(35, len(FAQS))),
    ("13-品牌机型.txt", lambda: build_topics(brand_faqs())),
    ("14-镜头与配件概览.txt", build_overview),
]


def build_topics(topics):
    em = Emitter()
    for _topic, questions, answer in topics:
        for q in questions:
            em.pair(q, answer)
    return em.text()


# ══════════════════════════════════════════════════════════════════════
#  校验：拿 guard 的黑名单检查所有答案
# ══════════════════════════════════════════════════════════════════════

MIN_QUESTION_LEN = 4  # 与 pipeline/chat_importer.py 一致

NOISE_PATTERNS = [
    r"^(你好|您好|在吗|哈喽|hi|hello)[\s!！。.,，]*$",
    r"^(好的|OK|ok|收到|明白|嗯嗯|哦哦|谢谢|感谢)[\s!！。.,，]*$",
    r"^[\s!！。.,，?!？!！]*$",
    r"^\[图片\]$", r"^\[语音\]$", r"^\[视频\]$", r"^\[文件\]$",
    r"^\{.*\}$",
]


def all_questions():
    """收集所有会被写进知识库的客户问法。"""
    qs = []
    for _f, builder in FILES:
        text = builder()
        lines = text.split("\n")
        for i, ln in enumerate(lines):
            if "@微信@微信联系人" in ln and i + 1 < len(lines):
                qs.append(lines[i + 1].strip())
    return qs


def validate():
    sys.path.insert(0, ROOT)
    from rag.guard import (UNCERTAINTY_KEYWORDS, GENERIC_REPLY_PATTERNS,
                           HEDGE_PHRASES)
    blocked = UNCERTAINTY_KEYWORDS + GENERIC_REPLY_PATTERNS + HEDGE_PHRASES

    answers = []
    for topic, _qs, a in FAQS + brand_faqs():
        answers.append((f"FAQ:{topic}", a))
    for m, d, p, n in BODIES:
        answers.append((f"机身:{m}", f"{m} 日租 {d} 元，押金 {p} 元。{n}。"
                                   "租期 3 天起，7 天以上有折扣。"))
    for m, d, p in LENSES + ACCESSORIES:
        answers.append((f"其他:{m}", f"{m} 日租 {d} 元，押金 {p} 元。"
                                    "和机身一起租，总租金按 9 折计算。"))

    problems = []
    for name, a in answers:
        for kw in blocked:
            if kw in a:
                problems.append((name, f"命中封禁词「{kw}」", a[:44]))

    # 问题长度 / 噪音 —— 短于 4 字会被 chat_importer 静默丢弃
    for q in all_questions():
        if len(q) < MIN_QUESTION_LEN:
            problems.append((f"问题「{q}」",
                             f"长度 {len(q)} < {MIN_QUESTION_LEN}，会被丢弃", ""))
        for pat in NOISE_PATTERNS:
            if re.match(pat, q, re.IGNORECASE):
                problems.append((f"问题「{q}」", "命中噪音规则，会被丢弃", ""))
    return problems, len(answers), len(all_questions())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验不写文件")
    args = ap.parse_args()

    problems, total, nq = validate()
    print(f"校验 {total} 条答案 + {nq} 条客户问法")
    if problems:
        print(f"  ✗ 发现 {len(problems)} 处问题：")
        for name, why, snippet in problems:
            print(f"    {name}: {why}  {snippet}")
        sys.exit(1)
    print("  ✓ 无封禁词、无过短/噪音问题")

    if args.check:
        return

    os.makedirs(OUT_DIR, exist_ok=True)
    total_pairs = 0
    for fname, builder in FILES:
        text = builder()
        total_pairs += text.count("@微信@微信联系人")
        with open(os.path.join(OUT_DIR, fname), "w",
                  encoding="utf-8", newline="") as f:
            f.write(text.replace("\n", "\r\n"))
        print(f"  ✓ {fname:<28} {len(text):>6} 字符")

    print(f"\n共 {len(FILES)} 个文件，{total_pairs} 组问答")
    print(f"输出目录: {OUT_DIR}")


if __name__ == "__main__":
    main()
