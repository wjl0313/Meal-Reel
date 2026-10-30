#!/usr/bin/env python3
"""从《中国食物成分表（第6版）》数据集筛出「健康低热量」食材池，并定价格档。

数据源：E:/myProject/china-food-composition-data
        json_data_v3_..._fixed_en/food_composition_full.csv（1677 条，含英文名）

为什么需要这一步，而不是把 1677 条全塞进推荐器：

  1. 表里混了大量非「做菜原料」的条目 —— 脱水菜（脱水白菜 305kcal/100g）、
     炼乳、品牌豆奶粉、油炸品、腌菜。它们进池子会污染打分：脱水白菜看着
     比猪肉还高热量，但它根本不是一回事。
  2. 表里没有价格字段。推荐器只看营养，会稳定推出三文鱼、牛腱、对虾 ——
     营养上确实优秀，但一周吃三次不现实。价格必须作为独立维度进来，
     这正是本脚本要解决的第二个问题。
  3. 命名是「学名［俗名］」，用户填表写俗名，中间需要一层归一。

本脚本只做「筛选 + 归一 + 定档」，不做打分；打分在 core/planner.py。

用法：
    python tools/build_ingredients.py --report       # 只出报告
    python tools/build_ingredients.py --write        # 写 data/ingredients.csv
                                                     # + data/price_ref.csv
"""
import argparse
import collections
import csv
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

DEFAULT_SRC = (r"E:/myProject/china-food-composition-data/"
               r"json_data_v3_20260825_qwen38max_kimi_k3_fixed_en/"
               r"food_composition_full.csv")

# ---------------------------------------------------------------- 价格档位
# 单位：元 / 公斤（按可食部折算）。这是量级判断，不是报价。
# 数据集里没有价格 —— 这一层全部来自日常采买经验，按 2026 年一二线城市
# 商超均价估的。要按本地菜场校准，改 data/price_ref.csv，脚本优先读它。
TIERS = [
    ("经济", 0, 10, "¥"),
    ("常规", 10, 25, "¥¥"),
    ("偏高", 25, 60, "¥¥¥"),
    ("昂贵", 60, 10 ** 9, "¥¥¥¥"),
]


def tier_of(price):
    for name, lo, hi, sym in TIERS:
        if lo <= price < hi:
            return name, sym
    return "未知", "?"


# ---------------------------------------------------------------- 排除规则
# 每一条对应一类「不该进日常做菜池」的东西。附理由，便于复核时判断
# 是不是筛过头了 —— 筛错比放过更危险，因为筛掉的东西不会报错。
EXCLUDE = [
    (r"脱水|干制|风干(?!豆)|晒干|（干）|\(干\)",
     "干制品：能量按干重计，与鲜品不可比（脱水白菜 305kcal/100g）"),
    (r"[（(][^）)]*牌[）)]|[（(](雀巢|伊利|蒙牛|光明|三元|维维|多力|大磨|燕山)",
     "品牌商品，非通用食材"),
    (r"香肠|腊肠|腊肉|火腿|培根|熏肉|肉松|肉干|午餐肉|肉丸|肉饼|炸|酥肉|扣肉|罐头",
     "加工肉制品：高钠高脂，非原料"),
    (r"腌|酱菜|泡菜|咸菜|酸菜|榨菜|梅干菜|腐乳|豆豉|咸鱼|虾皮|海米|鱼露|酱油|醋|味精",
     "腌制/调味品：钠含量不适合作为主料"),
    (r"肠|肚|百叶|脑|肺|蹄|爪|骨|皮$|耳$|舌|尾|鞭", "边角料或内脏杂件"),
    (r"奶油|黄油|猪油|牛油|羊油|植物油|调和油|花生油|豆油|菜籽油|玉米油|葵花|橄榄油|香油|芝麻油",
     "纯油脂：899kcal/100g，只做烹调计量，不进食材池"),
    (r"糖$|饴|蜜饯|果脯|巧克力|可可|冰淇淋|雪糕|蛋糕|饼干|糕点|面包|月饼|汤圆|粽子",
     "高糖点心"),
    (r"淀粉|粉丝|粉条|粉皮|凉粉|藕粉|麦芽|糊精|面酱|酱$", "纯淀粉或酱料：营养密度低"),
]

# 被上面正则误伤的常见食材，逐条救回（「皮$」会打到豆皮）。
RESCUE = {"豆皮", "油豆腐", "千张", "素鸡"}
# 干豆/腐竹本身就是干货，不是「鲜品脱水」，豁免第一条规则
DRY_KEEP = re.compile(r"^(黄豆|黑豆|绿豆|赤小豆|芸豆|蚕豆|豌豆|鹰嘴豆|扁豆|眉豆|腐竹)")

# 分类 → 角色。角色决定它在餐盘里占什么位置，是组合枚举的依据。
ROLE_OF = {
    "蔬菜类及其制品": "蔬菜",
    "菌藻类": "蔬菜",
    "水果类及其制品": "水果",
    "畜肉类及其制品": "蛋白",
    "禽肉类及其制品": "蛋白",
    "鱼虾蟹贝类": "蛋白",
    "蛋类及其制品": "蛋白",
    "干豆类及其制品": "蛋白",
    "乳类及其制品": "乳品",
    "谷类及其制品": "主食",
    "薯类淀粉及其制品": "主食",
    "坚果种子类": "坚果",
    "植物油": "油脂",
    "动物油脂类": "油脂",
    "其他类": "其他",
}

# 能量上限（kcal/100g 可食部，生重）。按角色分别设，因为「低热量」的尺子
# 对叶菜和干豆完全不同：菠菜 24、黄豆 390 —— 但黄豆一顿只吃 40g，
# 拿 200g 蔬菜的尺子去量它必然误杀。所以干豆单列一档。
ENERGY_CAP = {"蔬菜": 120, "水果": 90, "蛋白": 230, "乳品": 90,
              "主食": 400, "坚果": 700, "其他": 150, "调味": 200}
DRY_ENERGY_CAP = 520          # 干豆/腐竹：按干重算，但份量小

# 蛋白类的脂肪上限：五花肉 37g、肋排 20g 这类不该当日常蛋白主力
FAT_CAP = {"畜肉": 15, "禽肉": 12, "水产": 8, "蛋": 12, "豆制品": 12}

NA_CAP = 700.0                # 钠上限：超过就不是主料而是腌/调制品

# 调味料：只做风味，不进主料池，份量按 5-10g 计
SEASONING = {"大蒜", "蒜", "姜", "大葱", "小葱", "葱", "香菜", "芫荽"}

# 深色蔬菜判定：膳食指南的口径是「深绿/红/橘红/紫红色」。数据集有
# carotene 字段，正好是可用代理指标，比手写名单可复现。
# 阈值 300μg/100g 对应深色蔬菜典型下限（菠菜 2920、胡萝卜 4130、大白菜 120）。
CAROTENE_DARK = 300.0
DARK_BY_NAME = re.compile(r"紫甘蓝|紫菜|红苋|紫背|紫苏|红心萝卜|心里美|南瓜|胡萝卜")


# ---------------------------------------------------------------- 常用清单
# 为什么要第二层：上面能筛出 1000 条出头，但大量是同一种菜的地方品种
# （瓢儿白／乌菜／花叶萝卜／卞萝卜…）。从 1000 条里枚举，会推出
# 「清炒瓢儿白配乌菜」这种现实里买不到的菜。
#
# 这一层是显式白名单：只放商超和菜场稳定有货、家常做法成熟、老年人吃得惯
# 的品种。名字写「剥掉括号后的正名」，精确匹配不做模糊 —— 模糊匹配会把
# 红薯匹到红薯叶、鸡蛋匹到鸡蛋白，这是必须避免的串行。
#
# 每行：(正名, 元/公斤)。价格是量级判断，要精确就改 data/price_ref.csv。
COMMON = [
    # ---- 叶菜 / 茎菜 ----
    ("菠菜", 12), ("油菜", 8), ("小白菜", 6), ("大白菜", 4),
    ("结球甘蓝", 5), ("西兰花", 16), ("花椰菜", 12), ("芥蓝", 14),
    ("生菜", 12), ("蕹菜", 8), ("韭菜", 10), ("茼蒿", 12),
    ("芹菜", 8), ("苋菜", 10), ("荠菜", 16), ("莴笋", 6),
    ("芦笋", 30), ("落葵", 10), ("菜薹", 10),
    # ---- 根茎 / 葱蒜 ----
    ("胡萝卜", 6), ("白萝卜", 4), ("洋葱", 5), ("大葱", 6),
    ("姜", 14), ("大蒜", 12), ("马铃薯", 6), ("甘薯", 5),
    ("山药", 14), ("芋头", 8), ("藕", 12), ("荸荠", 12),
    ("竹笋", 20), ("魔芋精粉", 8),
    # ---- 茄果 / 瓜 / 鲜豆 ----
    ("番茄", 10), ("黄瓜", 8), ("茄子", 8), ("甜椒", 10),
    ("冬瓜", 4), ("南瓜", 6), ("苦瓜", 10), ("丝瓜", 8),
    ("西葫芦", 7), ("四季豆", 12), ("豇豆", 12), ("豌豆", 14),
    ("绿豆芽", 5), ("黄豆芽", 5),
    # ---- 菌藻 ----
    ("香菇", 18), ("平菇", 12), ("金针菇", 12), ("杏鲍菇", 14),
    ("白蘑菇", 20), ("木耳", 10), ("海带", 8),
    # ---- 蛋白：蛋 ----
    ("鸡蛋", 11), ("鹌鹑蛋", 20),
    # ---- 蛋白：肉禽 ----
    ("鸡胸脯肉", 22), ("鸡腿", 20), ("猪肉", 32), ("牛肉", 90),
    ("羊肉", 80), ("猪肝", 20), ("鸡血", 8), ("鸭血", 8),
    # ---- 蛋白：水产（成分表里三文鱼叫「鲑鱼」）----
    ("草鱼", 16), ("鲫鱼", 20), ("鲈鱼", 45), ("带鱼", 40),
    ("罗非鱼", 20), ("鲤鱼", 15), ("鳙鱼", 18), ("对虾", 85),
    ("蛤蜊", 20), ("扇贝", 50), ("鲑鱼", 180), ("银鱼", 8),
    # ---- 蛋白：豆制品与干豆 ----
    ("豆腐", 8), ("豆腐干", 14), ("豆浆", 4), ("腐竹", 20),
    ("黄豆", 7), ("黑豆", 9), ("绿豆", 8), ("赤小豆", 10),
    ("鹰嘴豆", 16),
    # ---- 主食 ----
    ("稻米", 7), ("粳米", 7), ("糙米", 11), ("小米", 10),
    ("燕麦", 12), ("玉米", 6), ("荞麦", 10), ("小麦粉", 6),
    ("挂面", 8), ("馒头", 6), ("玉米面", 8), ("藜麦", 40),
    # ---- 乳品 ----
    ("纯牛奶", 12), ("酸奶", 16),
    # ---- 坚果种子 ----
    ("花生仁", 16), ("核桃", 60), ("芝麻子", 24), ("杏仁", 60),
]

# 成分表用的是规范名，菜场不这么叫。价格表要给人改，就得用抬头能对上的说法：
# 「结球甘蓝 5 元/kg」没人看得懂，「包菜」才看得懂。
# 只列**叫法不同**的，同名的不写；没列到的就原样显示。
MARKET_NAME = {
    "结球甘蓝": "包菜", "蕹菜": "空心菜", "落葵": "木耳菜", "菜薹": "菜心",
    "芥蓝": "芥兰", "马铃薯": "土豆", "甘薯": "红薯", "藕": "莲藕",
    "荸荠": "马蹄", "竹笋": "春笋", "魔芋精粉": "魔芋豆腐",
    "甜椒": "彩椒", "四季豆": "豆角", "豇豆": "长豆角",
    "白蘑菇": "口蘑", "鸡胸脯肉": "鸡胸肉", "稻米": "大米",
    "粳米": "大米（粳）", "小麦粉": "面粉", "玉米面": "棒子面",
    "纯牛奶": "牛奶", "花生仁": "花生米", "芝麻子": "芝麻",
    "鲑鱼": "三文鱼", "鳙鱼": "胖头鱼", "银鱼": "小银鱼",
    "鸡血": "鸡血（盒装）", "鸭血": "鸭血（盒装）",
}


def _pick_key(k):
    """同正名多条时的优先级：代表值 > 短名字。"""
    return (0 if k["code"].endswith("x") else 1, len(k["name"]))


# 代表值未必最贴合「低热量」目标：猪肉代表值是肥瘦（181kcal / 11.7g 脂肪），
# 而瘦部位是 143kcal / 6.2g 脂肪，价格同档。既然整个池子是按低热量筛的，
# 畜肉就明确取瘦部位，而不是机械地取代表值。
PREFER = {
    "猪肉": "猪肉（瘦）",
    "牛肉": "牛肉（后腱）",
    "羊肉": "羊肉（后腿）",
}


# ---------------------------------------------------------------- 解析工具
def num(x, default=None):
    """成分表里的非数值：Tr（微量）→0.0、—／空 → default。"""
    if x is None:
        return default
    s = str(x).strip()
    if s in ("Tr", "tr", "TR"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return default


def split_name(raw):
    """「菠菜［赤根菜，波斯草］」→ (菠菜, 赤根菜，波斯草)。"""
    m = re.match(r"^(.+?)\s*[［\[](?:[［\[])?(.+?)[］\]]*$", raw)
    if not m:
        return raw.strip(), ""
    return m.group(1).strip(), re.sub(r"[［\[]|[］\]]", "", m.group(2)).strip()


def norm(name):
    """剥掉括号：「菠菜（鲜）」→「菠菜」、「红菜薹［紫菜薹］」→「红菜薹」。

    只剥括号不做别的归一，是因为括注里混着品种（圆/长）、状态（鲜/干）、
    别名（紫菜薹）三类信息。状态影响该不该采纳，但已由 EXCLUDE 单独处理。
    """
    return re.sub(r"[（(\[［].*?[)）\]］]", "", name).strip()


# 数据集里有几条把「脂肪含量」漏进了名字（猪肉（fat12g））。
# 这种名字出现在字幕里是灾难（"香煎猪肉（fat12g）"），必须清洗。
_FAT_IN_NAME = re.compile(r"[，,]?\s*fat\s*\d+\s*g", re.I)


def clean_name(name):
    s = _FAT_IN_NAME.sub("", name)
    s = re.sub(r"[，,]\s*[)）]", "）", s)      # 去掉清洗后悬空的逗号
    s = re.sub(r"[，,]\s*$", "", s)
    return s.strip() or name


def read_name(row):
    name, alias = split_name(row["foodName"])
    return clean_name(name), alias


# 数据集里有几条把「脂肪含量」漏进了名字（猪肉（fat12g）），
# 这种名字出现在字幕里是灾难（"香煎猪肉（fat12g）"）。清洗掉。
_FAT_IN_NAME = re.compile(r"[，,]?\s*fat\s*\d+\s*g", re.I)


def clean_name(name):
    s = _FAT_IN_NAME.sub("", name)
    s = re.sub(r"[，,]\s*[）)]", "）", s)      # 去掉清洗后悬空的逗号
    s = re.sub(r"[，,]\s*$", "", s)
    return s.strip() or name


def is_dark_veg(row, name):
    return ((num(row.get("carotene"), 0.0) or 0.0) >= CAROTENE_DARK
            or bool(DARK_BY_NAME.search(name)))


def classify(row):
    """返回 (role, dry) 或 (None, 排除理由)。"""
    category = row["category"]
    main_cat = category.split("-")[0]
    name, _alias = read_name(row)

    role = ROLE_OF.get(main_cat)
    if role in (None, "油脂"):
        return None, f"非食材类别（{main_cat}）"

    dry = bool(DRY_KEEP.search(name))
    # 用剥括号后的正名判调味：「大蒜（白皮，鲜）」也要认出来，
    # 否则它会走蔬菜 120kcal 的尺子被误杀（蒜 128kcal）。
    if norm(name) in SEASONING:
        role = "调味"

    # 干豆/腐竹豁免「干制品」排除：它们是干货本身，不是鲜品脱水
    if not dry:
        for pat, why in EXCLUDE:
            if name in RESCUE:
                break
            if re.search(pat, name):
                return None, why

    kcal = num(row["energyKCal"])
    if kcal is None:
        return None, "能量缺失"
    cap = DRY_ENERGY_CAP if dry else ENERGY_CAP.get(role, 150)
    if kcal > cap:
        return None, f"能量 {kcal:.0f} 超过{role}上限 {cap}"

    na = num(row["Na"])
    if na is not None and na > NA_CAP:
        return None, f"钠 {na:.0f}mg 超上限（腌/调制品）"

    if role == "蛋白":
        fat = num(row["fat"], 0.0) or 0.0
        key = ("水产" if main_cat == "鱼虾蟹贝类" else
               "禽肉" if main_cat == "禽肉类及其制品" else
               "蛋" if main_cat == "蛋类及其制品" else
               "豆制品" if main_cat == "干豆类及其制品" else "畜肉")
        # 干豆不套脂肪上限：黄豆 16g 脂肪是「干重里的植物脂肪」，
        # 和五花肉的 37g 动物脂肪不是一回事，而它一顿只吃 40g。
        if fat > FAT_CAP[key] and not dry:
            return None, f"脂肪 {fat:.1f}g/100g 超过{key}上限 {FAT_CAP[key]}"
        protein = num(row["protein"], 0.0) or 0.0
        if protein < 5 and key != "豆制品":
            return None, f"蛋白仅 {protein:.1f}g，不足以作主料"
    return role, dry


def load_rows(src):
    with open(src, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def curate(src):
    rows = load_rows(src)
    kept, dropped = [], collections.Counter()
    samples = collections.defaultdict(list)
    for r in rows:
        role, extra = classify(r)
        if role is None:
            k = re.sub(r"[\d.]+", "N", extra)
            dropped[k] += 1
            if len(samples[k]) < 3:
                samples[k].append(r["foodName"])
            continue
        name, alias = read_name(r)
        kept.append({
            "code": r["foodCode"], "name": name, "alias": alias,
            "role": role, "category": r["category"],
            "kcal": num(r["energyKCal"], 0.0),
            "protein": num(r["protein"], 0.0),
            "fat": num(r["fat"], 0.0),
            "carb": num(r["CHO"], 0.0),
            "fiber": num(r["dietaryFiber"], 0.0),
            "na": num(r["Na"], 0.0),
            "carotene": num(r["carotene"], 0.0),
            "edible": num(r["edible"], 100.0),
            "dark": is_dark_veg(r, name) if role == "蔬菜" else False,
            "dry": bool(extra),
            "remark": (r.get("remark") or "").strip(),
        })
    return kept, dropped, samples, len(rows)


def match_common(kept):
    """正名精确匹配。返回 (命中, 未命中, 同正名多条)。"""
    by_norm = collections.defaultdict(list)
    for k in kept:
        by_norm[norm(k["name"])].append(k)
        for seg in re.split(r"[，,]", k["alias"]):
            seg = seg.strip()
            if seg:
                by_norm.setdefault(seg, []).append(k)

    hits, miss, dup = [], [], []
    for name, price in COMMON:
        cands = sorted({c["code"]: c for c in by_norm.get(name, [])}.values(),
                       key=_pick_key)
        if not cands:
            miss.append((name, price))
            continue
        want = PREFER.get(name)
        if want:
            exact = [c for c in cands if c["name"] == want]
            if exact:
                cands = exact + [c for c in cands if c["name"] != want]
        hits.append((name, price, cands[0]))
        if len(cands) > 1:
            dup.append((name, [c["name"] for c in cands]))
    return hits, miss, dup


def load_price_override():
    """data/price_ref.csv 优先：用户按本地菜场改过的价格。

    两列名字都当键：`食材名称`（成分表里的正名，如"结球甘蓝"）和
    `菜场叫法`（如"包菜"）。用户改哪一行的数字都生效，不必关心列名。
    """
    p = os.path.join(ROOT, "data", "price_ref.csv")
    if not os.path.exists(p):
        return {}
    out = {}
    with open(p, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            try:
                v = float(r["元每公斤"])
            except (KeyError, TypeError, ValueError):
                continue
            for col in ("食材名称", "菜场叫法"):
                k = (r.get(col) or "").strip()
                if k:
                    out[k] = v
    return out


def build(src):
    kept, dropped, samples, total = curate(src)
    hits, miss, dup = match_common(kept)
    override = load_price_override()

    lib = []
    for name, price, it in hits:
        # 两种键都认：菜场叫法（新表格）与成分表正名（老表格）
        p = override.get(name, override.get(MARKET_NAME.get(name, ""),
                                            override.get(it["name"], price)))
        tier, sym = tier_of(p)
        # 只有真被改过才算 price_ref，否则这个字段 100% 是 price_ref 等于没有
        lib.append({**it, "common_name": name, "price_kg": p,
                    "tier": tier, "tier_sym": sym,
                    "price_src": "price_ref" if abs(p - price) > 1e-9 else "builtin"})
    return lib, dict(total=total, kept=len(kept), dropped=dropped,
                     samples=samples, miss=miss, dup=dup)


# ---------------------------------------------------------------- 落盘
LIB_COLS = ["foodCode", "食材名称", "别名", "角色", "类别", "千卡", "蛋白质",
            "脂肪", "碳水", "膳食纤维", "钠", "胡萝卜素", "可食部", "深色蔬菜",
            "干货", "元每公斤", "价格档", "价格符号", "价格来源"]


def write_library(lib, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(LIB_COLS)
        for x in lib:
            w.writerow([x["code"], x["name"], x["alias"], x["role"], x["category"],
                        f"{x['kcal']:.0f}", f"{x['protein']:.1f}", f"{x['fat']:.1f}",
                        f"{x['carb']:.1f}", f"{x['fiber']:.1f}", f"{x['na']:.0f}",
                        f"{x['carotene']:.0f}", f"{x['edible']:.0f}",
                        "是" if x["dark"] else "", "是" if x["dry"] else "",
                        f"{x['price_kg']:.0f}", x["tier"], x["tier_sym"],
                        x["price_src"]])
    return path


def write_price_ref(lib, path):
    """导出可编辑的价格表。先给内置估值，按本地菜场改完再跑一次即可生效。

    每次重建都会覆盖这个文件，所以**改价要改在这里**、不要手改 ingredients.csv。
    代价是重跑会把你改过的数字冲掉 —— 所以先把这个文件另存一份再重跑。
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["食材名称", "菜场叫法", "元每公斤", "价格档", "备注"])
        for x in sorted(lib, key=lambda k: (k["role"], k["price_kg"])):
            db = x["common_name"]
            w.writerow([db, MARKET_NAME.get(db, ""), f"{x['price_kg']:.0f}",
                        x["tier"], "内置估值（改成你家附近的价格即可）"])
    return path


# ---------------------------------------------------------------- 报告
def report(lib, stats):
    print(f"源数据 {stats['total']} 条 → 营养池 {stats['kept']} 条 "
          f"（{stats['kept']/stats['total']*100:.1f}%）"
          f" → 常用清单 {len(lib)} 条\n")

    print("=== 排除原因 ===")
    for why, n in stats["dropped"].most_common():
        print(f"  {n:>4}  {why}")
        print(f"        例：{'、'.join(stats['samples'][why])}")

    if stats["miss"]:
        print("\n=== 常用清单未命中（名字对不上，需修正） ===")
        for n, p in stats["miss"]:
            print(f"  {n:<10} {p:>4} 元/kg")

    print("\n=== 入池按角色 ===")
    by = collections.Counter(x["role"] for x in lib)
    for r, n in by.most_common():
        print(f"  {r:<4} {n:>3}")

    print("\n=== 价格档分布 ===")
    bt = collections.Counter(x["tier"] for x in lib)
    for name, lo, hi, sym in TIERS:
        n = bt.get(name, 0)
        if n:
            print(f"  {sym:<5} {name:<3} {lo:>3}-{hi if hi < 10**8 else '∞':>5} 元/kg "
                  f"{n:>3} 条")
    for x in sorted(lib, key=lambda k: -k["price_kg"])[:5]:
        print(f"      最贵：{x['name']:<8} {x['price_kg']:>5.0f} 元/kg  "
              f"{x['protein']:.1f}g蛋白/100g")

    print("\n=== 能量最低的 8 种蔬菜 ===")
    for x in sorted([k for k in lib if k["role"] == "蔬菜"],
                    key=lambda k: k["kcal"])[:8]:
        print(f"  {x['name']:<8} {x['kcal']:>4.0f} kcal  "
              f"深色={'是' if x['dark'] else '否'}  "
              f"{x['price_kg']:>4.0f}元/kg {x['tier']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=DEFAULT_SRC)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()

    lib, stats = build(a.src)
    report(lib, stats)
    if a.write:
        p = write_library(lib, os.path.join(ROOT, "data", "ingredients.csv"))
        q = write_price_ref(lib, os.path.join(ROOT, "data", "price_ref.csv"))
        print(f"\n已写 {p}\n已写 {q}")
