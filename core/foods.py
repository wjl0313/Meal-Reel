#!/usr/bin/env python3
"""食物成分与营养折算 —— 纯函数，不联网。

数据来源：《中国食物成分表（第6版）》，经 tools/build_ingredients.py 筛出
健康低热量池（data/ingredients.csv，103 条），每条带：
  · 每 100g 可食部的能量/蛋白/脂肪/碳水/纤维/钠/胡萝卜素
  · 可食部比例（买 200g 西兰花实际吃到 166g）
  · 角色（蔬菜/蛋白/主食/坚果/乳品/调味）—— 组合枚举靠它
  · 价格（元/公斤）与价格档 —— 推荐时控成本靠它
  · 深色蔬菜标记（按胡萝卜素 ≥300μg 判定，不是手写名单）

两条必须做对的折算（做错了用户一算就发现不对）：
  1. 可食部：买回来 200g 西兰花，实际可食只有 166g
  2. 生熟比：150g 生鸡胸煎熟约 105g，重量变了但营养不变 ——
     报「熟重 30g 蛋白」是错的，蛋白要按生重算
所有数值按「每 100g 可食部 · 生重」计。
"""
import csv
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LIB_PATH = os.path.join(ROOT, "data", "ingredients.csv")

# 烹调油不在食材库里（被筛选规则排除了：899kcal/100g 不是食材），
# 但算营养时必须计入，否则「561 千卡」会少算 70 千卡。所以单列。
MANUAL = {
    "oil": {"name": "植物油", "role": "调味", "kcal": 899, "protein": 0.0,
            "fat": 99.9, "carb": 0.0, "fiber": 0.0, "na": 0.0,
            "edible": 1.0, "dark": False, "dry": False,
            "price_kg": 20, "category": "植物油"},
}

# 价格档口径与 tools/build_ingredients.py 的 TIERS 保持一致
_TIERS = [(0, 10, "经济", "¥"), (10, 25, "常规", "¥¥"),
          (25, 60, "偏高", "¥¥¥"), (60, 10 ** 9, "昂贵", "¥¥¥¥")]


def _fill_tier(d):
    p = d.get("price_kg") or 0.0
    for lo, hi, name, sym in _TIERS:
        if lo <= p < hi:
            d.setdefault("tier", name)
            d.setdefault("tier_sym", sym)
            return d
    d.setdefault("tier", "未知")
    d.setdefault("tier_sym", "?")
    return d

# 单件约重（g，毛重）。成分表只有可食部比例、没有单件重量，
# 只能按常见规格补 —— 这是唯一靠经验的地方，所以宁可给保守值。
UNIT_WEIGHT = {
    "鸡蛋": 55, "鹌鹑蛋": 10, "鸭蛋": 65, "番茄": 150, "马铃薯": 200,
    "洋葱": 180, "黄瓜": 220, "茄子": 300, "甜椒": 120, "西兰花": 300,
    "花椰菜": 600, "胡萝卜": 120, "白萝卜": 500, "大白菜": 1200,
    "结球甘蓝": 800, "生菜": 300, "莴笋": 200, "藕": 400, "山药": 300,
    "芋头": 120, "香菇": 15, "平菇": 25, "金针菇": 100, "杏鲍菇": 60,
    "白蘑菇": 12, "南瓜": 800, "冬瓜": 1000, "丝瓜": 200, "西葫芦": 250,
    "苦瓜": 250, "韭菜": 200, "豆腐": 400, "豆腐干": 100, "馒头": 100,
    "玉米": 200, "草鱼": 700, "鲫鱼": 250, "鲈鱼": 500, "鲤鱼": 600,
    "罗非鱼": 400, "鳙鱼": 1500, "带鱼": 400, "大蒜": 40, "姜": 100,
    "大葱": 120, "纯牛奶": 1000, "酸奶": 200, "竹笋": 200, "荸荠": 15,
    "植物油": 500, "菠菜": 400, "油菜": 400, "小白菜": 400, "蕹菜": 400, "苋菜": 400,
    "茼蒿": 300, "芹菜": 500, "芦笋": 300, "落葵": 300, "菜薹": 400,
    "赤小豆": 100, "绿豆": 100, "黄豆": 100, "黑豆": 100, "鹰嘴豆": 100,
    "花生仁": 100, "核桃": 30, "杏仁": 30, "芝麻子": 100,
}
# 件数类单位：需要乘以单件重量
PIECE_UNITS = {"颗", "根", "个", "块", "条", "头", "只", "把", "串",
               "盒", "袋", "瓶", "包", "片", "张", "枚", "粒", "瓣"}
# 体积单位：按 1kg/L 折（油另算 0.92）
VOLUME_UNITS = {"l": 1000, "升": 1000, "ml": 1, "毫升": 1}
# 生 → 熟 重量比（营养不变，只影响文案里「多少克」的说法）
RAW_TO_COOKED = {"鸡胸脯肉": 0.70, "猪肉": 0.68, "牛肉": 0.65, "羊肉": 0.65,
                 "草鱼": 0.85, "鲫鱼": 0.85, "鲈鱼": 0.85, "稻米": 2.30,
                 "粳米": 2.30, "糙米": 2.30, "挂面": 2.20, "西兰花": 0.92,
                 "菠菜": 0.72, "油菜": 0.75, "小白菜": 0.75}


def _f(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _strip(name):
    """剥掉括号：「结球甘蓝（绿）」→「结球甘蓝」。

    成分表的正名多数带括注（品种/状态），用户填的是不带括号的通名，
    所以索引必须同时收录剥括号后的形式，否则「包菜」这类词映射不进来。
    """
    import re
    return re.sub(r"[（(\[［].*?[)）\]］]", "", name).strip()


def load(path=None):
    """读食材库。文件缺失时返回空表 —— 由调用方给出明确错误，
    而不是悄悄退化成 11 条内置数据（那样推荐结果会莫名变差又查不出原因）。"""
    path = path or LIB_PATH
    if not os.path.exists(path):
        return {}, {}
    foods, names, aliases = {}, {}, {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            code = (r.get("foodCode") or "").strip()
            name = (r.get("食材名称") or "").strip()
            if not code or not name:
                continue
            foods[code] = {
                "code": code, "name": name, "role": (r.get("角色") or "").strip(),
                "category": (r.get("类别") or "").strip(),
                "kcal": _f(r.get("千卡")), "protein": _f(r.get("蛋白质")),
                "fat": _f(r.get("脂肪")), "carb": _f(r.get("碳水")),
                "fiber": _f(r.get("膳食纤维")), "na": _f(r.get("钠")),
                "carotene": _f(r.get("胡萝卜素")),
                "edible": _f(r.get("可食部"), 100.0) / 100.0,
                "dark": (r.get("深色蔬菜") or "").strip() == "是",
                "dry": (r.get("干货") or "").strip() == "是",
                "price_kg": _f(r.get("元每公斤")),
                "tier": (r.get("价格档") or "").strip(),
                "tier_sym": (r.get("价格符号") or "").strip(),
            }
            names[name] = code
            # 别名单独收着，最后才并入 —— 别名优先级必须低于通名，
            # 否则某行别名栏写了「猪肉」，就会把「里脊」抢到那个部位上。
            for seg in (r.get("别名") or "").split("，"):
                seg = seg.strip()
                if seg:
                    aliases.setdefault(seg, code)

    # 第二遍补「剥括号通名」。必须单独一遍并显式排序，不能在读取时顺手写：
    # 文件里「猪肉（瘦）」「猪肉（腿）」「猪肉（fat12g）」都会归到「猪肉」，
    # 谁先读到谁占位，结果取决于行序 —— 那样「里脊」会随机匹到某个部位。
    # 排序口径：代表值优先，其次原始名最短（越短越接近通名）。
    cands = sorted(((_strip(f["name"]), c, f["name"]) for c, f in foods.items()),
                   key=lambda x: (0 if x[1].endswith("x") else 1, len(x[2])))
    for base, code, _full in cands:
        if base:
            names.setdefault(base, code)
    for seg, code in aliases.items():
        names.setdefault(seg, code)
    return foods, names


FOODS, NAME2KEY = load()
FOODS.update(MANUAL)
for _d in FOODS.values():
    _fill_tier(_d)
NAME2KEY.setdefault("植物油", "oil")


# 用户填表的口语 → 库里的正名。成分表用学名（蕹菜、结球甘蓝），
# 用户写的是「空心菜」「包菜」；不做这层映射就会整行丢弃，
# 而用户只会看到库存少了几条，不知道为什么。
COLLOQUIAL = {
    "鸡胸": "鸡胸脯肉", "鸡脯肉": "鸡胸脯肉", "鸡大胸": "鸡胸脯肉",
    "鸡胸肉": "鸡胸脯肉", "鸡腿肉": "鸡腿", "鸡翅": "鸡腿",
    "西蓝花": "西兰花", "绿花菜": "西兰花", "青花菜": "西兰花",
    "包菜": "结球甘蓝", "卷心菜": "结球甘蓝", "圆白菜": "结球甘蓝",
    "洋白菜": "结球甘蓝", "菜花": "花椰菜", "白花菜": "花椰菜",
    "空心菜": "蕹菜", "通菜": "蕹菜", "油麦菜": "生菜",
    "西红柿": "番茄", "圣女果": "番茄", "小番茄": "番茄",
    "土豆": "马铃薯", "洋芋": "马铃薯", "地瓜": "甘薯", "红薯": "甘薯",
    "山芋": "甘薯", "红苕": "甘薯",
    "青椒": "甜椒", "菜椒": "甜椒", "灯笼椒": "甜椒",
    "豆角": "四季豆", "芸豆角": "四季豆", "长豆角": "豇豆",
    "蒜薹": "大蒜", "蒜苗": "大蒜", "蒜": "大蒜", "蒜头": "大蒜",
    "姜": "姜", "生姜": "姜", "老姜": "姜",
    "葱": "大葱", "小葱": "大葱", "香葱": "大葱",
    "木耳": "木耳", "黑木耳": "木耳", "香菇": "香菇", "冬菇": "香菇",
    "海带丝": "海带", "海带结": "海带",
    "老豆腐": "豆腐", "北豆腐": "豆腐", "南豆腐": "豆腐", "嫩豆腐": "豆腐",
    "卤水豆腐": "豆腐", "豆干": "豆腐干", "香干": "豆腐干",
    "豆皮": "豆腐干", "豆浆": "豆浆",
    "米": "稻米", "大米": "稻米", "生米": "稻米", "粳米": "粳米",
    "香米": "稻米", "东北米": "粳米", "饭": "稻米",
    "面条": "挂面", "挂面": "挂面", "面": "挂面",
    "燕麦片": "燕麦", "燕麦": "燕麦", "玉米棒": "玉米", "甜玉米": "玉米",
    "小米": "小米", "黄小米": "小米", "荞麦面": "荞麦",
    "面粉": "小麦粉", "白面": "小麦粉", "全麦粉": "小麦粉",
    "牛肉": "牛肉", "牛腱": "牛肉", "牛腩": "牛肉", "瘦牛肉": "牛肉",
    "猪肉": "猪肉", "里脊": "猪肉", "猪里脊": "猪肉", "瘦肉": "猪肉",
    "猪瘦肉": "猪肉", "排骨": "猪肉",
    "鸡血": "鸡血", "鸭血": "鸭血", "猪肝": "猪肝",
    "虾": "对虾", "虾仁": "对虾", "基围虾": "对虾", "大虾": "对虾",
    "花蛤": "蛤蜊", "蛤蜊": "蛤蜊", "扇贝肉": "扇贝",
    "三文鱼": "鲑鱼", "鲑鱼": "鲑鱼", "大马哈鱼": "鲑鱼",
    "鸡蛋": "鸡蛋", "土鸡蛋": "鸡蛋", "蛋": "鸡蛋",
    "牛奶": "纯牛奶", "纯牛奶": "纯牛奶", "鲜奶": "纯牛奶",
    "酸奶": "酸奶", "花生": "花生仁", "花生米": "花生仁",
    "核桃": "核桃", "核桃仁": "核桃", "芝麻": "芝麻子", "黑芝麻": "芝麻子",
    "杏仁": "杏仁", "巴旦木": "杏仁", "油": "oil", "植物油": "oil",
    "橄榄油": "oil", "花生油": "oil", "菜籽油": "oil", "色拉油": "oil",
}
for _a, _n in COLLOQUIAL.items():
    # 目标既可能是正名（→ 查索引），也可能是直接给的 key（如 "oil"）
    if _n in FOODS:
        NAME2KEY.setdefault(_a, _n)
    elif _n in NAME2KEY:
        NAME2KEY.setdefault(_a, NAME2KEY[_n])


def info(key):
    return FOODS[key]


# 学名太长，菜名和字幕都要短。只改展示，不碰数据。
DISPLAY = {
    "鸡胸脯肉": "鸡胸", "结球甘蓝（绿）": "包菜", "结球甘蓝": "包菜",
    "蕹菜": "空心菜", "马铃薯": "土豆", "甘薯（白心）": "红薯",
    "甘薯": "红薯", "甜椒": "青椒", "鲑鱼": "三文鱼",
    "猪肉（瘦）": "瘦猪肉", "牛肉（后腱）": "牛腱", "羊肉（后腿）": "羊后腿",
    "菜薹": "菜心", "豌豆（带荚，鲜）": "豌豆", "玉米（鲜）": "玉米",
    "纯牛奶（代表值，全脂）": "牛奶", "大蒜（白皮，鲜）": "大蒜",
    "稻米": "大米", "粳米（标一）": "大米", "燕麦": "燕麦片",
    "豆腐（代表值）": "豆腐", "豆腐干（菜干）": "豆腐干",
    "芹菜（茎）": "芹菜", "菠菜（鲜）": "菠菜", "木耳（水发）": "木耳",
}


_NOISE = __import__("re").compile(r"[（(](?:代表值[^）)]*|散装|标一)[）)]")


def disp(key):
    """展示名：先查别名表，再剥掉「（代表值）」这类成分表专有的括注。
    这些字出现在菜名和字幕里毫无意义，而它们会一路传到成片上。
    """
    n = DISPLAY.get(FOODS[key]["name"], FOODS[key]["name"])
    return _NOISE.sub("", n).strip()


def label(key):
    return FOODS[key]["name"]


def role(key):
    return FOODS[key]["role"]


def grams(key, qty, unit="g"):
    """把用户填的数量+单位换算成可食部克数。返回 (可食部, 毛重)。

    单位优先级：重量 → 体积 → 件数（查单件重量）→ 报错。
    报错而不是猜：猜错会让"561 千卡"整体偏移，而用户看不出是哪一行错的。
    """
    it = FOODS[key]
    unit = (unit or "g").strip()
    u = unit.lower()
    if unit in ("g", "克", ""):
        purchased = float(qty)
    elif unit in ("kg", "千克", "公斤"):
        purchased = float(qty) * 1000
    elif unit in ("斤", "市斤"):
        purchased = float(qty) * 500
    elif u in VOLUME_UNITS:
        density = 0.92 if it["role"] == "调味" else 1.0
        purchased = float(qty) * VOLUME_UNITS[u] * density
    elif unit in PIECE_UNITS:
        w = UNIT_WEIGHT.get(_strip(it["name"])) or UNIT_WEIGHT.get(it["name"])
        if not w:
            raise ValueError(
                f"{it['name']}：不知道「1{unit}」约多重，请按 g 填"
                f"（或在此处补 UNIT_WEIGHT）")
        purchased = float(qty) * w
    else:
        raise ValueError(f"{it['name']}：单位「{unit}」无法换算，请按 g/kg 填")
    return round(purchased * it["edible"], 1), round(purchased, 1)


def nutri(key, edible_g):
    """可食部克数 → 营养。"""
    it = FOODS[key]
    k = edible_g / 100.0
    return {"kcal": it["kcal"] * k, "protein": it["protein"] * k,
            "fat": it["fat"] * k, "carb": it["carb"] * k,
            "fiber": it["fiber"] * k}


def total(items):
    """items: [(key, 可食部g)] → 合计（含蔬菜总量、深色蔬菜）。"""
    acc = {"kcal": 0.0, "protein": 0.0, "fat": 0.0, "carb": 0.0,
           "fiber": 0.0, "veg_g": 0.0, "dark_veg_g": 0.0}
    for key, g in items:
        n = nutri(key, g)
        for k in ("kcal", "protein", "fat", "carb", "fiber"):
            acc[k] += n[k]
        it = FOODS[key]
        if it["role"] == "蔬菜":
            acc["veg_g"] += g
            if it["dark"]:
                acc["dark_veg_g"] += g
    return {k: round(v, 1) for k, v in acc.items()}


# ---------------------------------------------------------------- 价格
def price_kg(key):
    return FOODS[key].get("price_kg") or 0.0


def tier(key):
    return FOODS[key].get("tier", "未知")


def tier_sym(key):
    return FOODS[key].get("tier_sym", "?")


def cost(key, edible_g):
    """一顿里这个食材花多少钱（元）。

    按可食部折价：买 200g 西兰花实际吃 166g，但钱是按 200g 付的，
    所以要除回可食部比例 —— 否则高损耗的食材（竹笋可食部 63%）
    成本会被低估 40%。
    """
    it = FOODS[key]
    edible = it["edible"] or 1.0
    purchased_g = edible_g / edible
    return it.get("price_kg", 0.0) * purchased_g / 1000.0


def basket_cost(items):
    return round(sum(cost(k, g) for k, g in items), 2)


def raw_to_cooked(key, edible_g):
    r = RAW_TO_COOKED.get(FOODS[key]["name"])
    return None if r is None else round(edible_g * r, 0)


def pretty_grams(key, edible_g):
    """给文案用：能写生的写生重，不能说"熟重"当营养依据。"""
    it = FOODS[key]
    if it["dry"]:
        return f"干{int(round(edible_g))}克"
    return f"{int(round(edible_g))}克"
