#!/usr/bin/env python3
"""食材词法：把菜谱里的自由文本翻译成结构化。

菜谱是写给人看的，所以「蒜末 适量」「五花肉 200 克」「青红椒 各 1 个」
「黑胡椒、海盐 适量」都是合法写法；而库存表和成分表是按食材组织的。
这层翻译必须只存在一处，否则每加一个功能（推荐/平替/购物清单/营养核算）
就要重新写一遍解析，而且各写各的必然不一致。

三个产出：
  parse_line()  「鸡胸肉 150 克」→ {name, grams, role, seasoning}
  role_of()     名称 → 蛋白 / 蔬菜 / 主食 / 乳品 / 坚果 / 水果 / 调味 / None
  is_seasoning()名称是否调味

设计取舍：**认不出就返回 None，不硬猜成一个「差不多」的食材**。
猜错的代价是营养算错、平替换错，而这两件事用户都看不出来；
返回 None 至少会在报告里露出「未识别」这一列，能被修。
"""
import re

# ---------------------------------------------------------------- 调味品
# 调味品不进库存匹配、不算主料、不参与平替。它们通常家里常备，
# 缺了也只是「少个味儿」，不影响「这道菜能不能做」。
SEASONING = set("""
盐 食盐 海盐 精盐 生抽 老抽 酱油 蒸鱼豉油 味极鲜 蚝油 鱼露 料酒 黄酒 白酒 啤酒
醋 香醋 陈醋 米醋 白醋 黑醋 白糖 冰糖 红糖 砂糖 细砂糖 糖粉 绵糖 蜂蜜 麦芽糖
味精 鸡精 鸡粉 胡椒粉 白胡椒 白胡椒粉 黑胡椒 黑胡椒碎 花椒 花椒粉 麻椒 八角 大料 桂皮
香叶 丁香 草果 白芷 小茴香 陈皮 孜然 孜然粉 五香粉 十三香 咖喱 咖喱粉 咖喱块
辣椒粉 辣椒面 辣椒油 红油 花椒油 藤椒油 香油 芝麻油 麻油 芝麻香油 食用油 植物油 橄榄油 花生油
菜籽油 玉米油 猪油 黄油 奶油 淡奶油 起酥油 淀粉 玉米淀粉 土豆淀粉 生粉 水淀粉
豆瓣酱 郫县豆瓣 郫县豆瓣酱 甜面酱 黄豆酱 东北大酱 大酱 豆豉 腐乳 腐乳汁 韭菜花 芝麻酱 花生酱 番茄酱 沙拉酱
千岛酱 蛋黄酱 芥末 泡椒 剁椒 小米辣 小米椒 朝天椒 干辣椒 辣椒 辣椒段 辣椒碎 老酒 红糟
酵母 干酵母 泡打粉 小苏打 食用碱 明矾 吉利丁 琼脂 香草精 可可粉 柠檬汁 白芝麻 黑芝麻
熟芝麻 芝麻 葱花 葱姜蒜 姜蒜 蒜末 蒜片 蒜蓉 姜片 姜末 姜丝 葱段 葱丝 葱末
清水 水 温水 热水 凉水 开水 高汤 鸡汤 骨汤 米汤 淘米水 苏打水
盐焗粉 奥尔良腌料 烧烤料 椒盐 蒜盐 香葱 香菜 芫荽 紫苏 紫苏叶 薄荷 罗勒 迷迭香 百里香
香茅 香茅草 干桂花 桂花 黄芪 当归 茯苓 党参 白术 甘草 川芎 白芍 熟地 陈皮 罗汉果
金银花 菊花 决明子 山楂干 麦冬 玉竹 沙参
""".split())

# 以「油/酱/汁/粉/精/料」结尾但其实是主料的，必须救回来
_SEASONING_RESCUE = {"豆腐乳", "豆浆", "花生酱"}

# 形状后缀：切法不是食材。「姜片」是姜，「蒜末」是蒜。
# 注意顺序，长的在前，否则「小段」会被「段」先吃掉。
_SHAPE = ("碎末", "碎", "末", "丝", "片", "丁", "块", "段", "条", "粒", "蓉",
          "圈", "花", "小段", "小朵", "小块")

# 状态前缀：「泡发木耳」是木耳，「嫩豆腐」是豆腐，「鲜香菇」是香菇。
# 但「干辣椒」是辣椒干、不是鲜辣椒，所以「干」不放进来 —— 它在调味表里。
# 也不放「小/大/老/嫩」：它们是小苏打、小米辣、大白菜、老豆腐的一部分，
# 剥掉会直接把食材名毁掉（「小苏打」→「苏打」就查不到了）。
_PREFIX = ("泡发的", "泡发", "水发的", "水发", "新鲜的", "新鲜", "鲜的", "鲜",
           "冷冻的", "冷冻", "冷藏的", "真空", "自制", "现成", "熟的", "生的",
           "去皮", "去骨", "去芯", "无盐", "低脂", "脱脂")

# 数量词（中文）
_CN_NUM = {"半": 0.5, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "几": 3}
_NUM_CHARS = "零一二三四五六七八九十半两几"

# 单位 → 克数（仅液体/粉末类可靠；片/瓣/根这类要靠角色兜底）
_UNIT_G = {"克": 1, "g": 1, "千克": 1000, "kg": 1000, "斤": 500, "两": 50,
           "毫升": 1, "ml": 1, "升": 1000, "l": 1000,
           "勺": 15, "汤匙": 15, "大勺": 15, "茶匙": 5, "小勺": 5, "撮": 2, "滴": 0.05,
           "杯": 240, "碗": 250, "把": 30, "小把": 15}

# 按件计的单位：一个「根/颗/个」大约多少克，按角色给不同默认
_PIECE_UNIT = {"片", "瓣", "根", "个", "只", "条", "块", "颗", "朵", "张", "段", "寸",
               "包", "袋", "盒", "瓶", "罐", "串", "枚", "粒", "头", "扎", "束"}

# 按件估重（克）。分角色给，因为「1 颗白菜」和「1 颗蒜」差两个数量级。
_PIECE_G = {
    "蛋白": 80, "蔬菜": 120, "主食": 100, "乳品": 200, "坚果": 8, "水果": 150,
    "调味": 5,
}

# 明确「适量/少许」时的用量：按角色给一个合理默认，而不是记 0 克 ——
# 记 0 会让营养总和偏小，而用户看到的总数只有最后那一个。
_AS_NEEDED_G = {"蛋白": 150, "蔬菜": 120, "主食": 60, "乳品": 200, "坚果": 10,
                "水果": 100, "调味": 6}

# ---------------------------------------------------------------- 角色判定
# 顺序有意义：先精确（成分表里有的），后关键词（大类别），最后 None。
# 关键词表按「命中即返回」用，所以更有辨识度的词要排在前面。
ROLE_KEYWORDS = [
    # 乳品 / 坚果 / 水果先判，避免「花生酱」被当成酱、「牛奶」被当成牛
    ("乳品", "牛奶 酸奶 羊奶 奶粉 奶酪 芝士 起司 淡奶油 炼乳 舒化奶 纯牛奶 乳酪"),
    ("坚果", "花生 核桃 杏仁 腰果 松子 榛子 开心果 夏威夷果 瓜子 南瓜子 葵花籽 "
             "巴旦木 碧根果 花生米 花生碎 核桃仁 莲子 芡实 奇亚籽 亚麻籽 "
             "蔓越莓干 葡萄干 蓝莓干 无花果干 桂圆干 杏干"),
    ("水果", "苹果 香蕉 橙 柠檬 青柠 草莓 蓝莓 树莓 梨 桃 葡萄 提子 猕猴桃 奇异果 "
             "芒果 菠萝 凤梨 西瓜 哈密瓜 火龙果 樱桃 李子 杏 石榴 柚子 橘子 柑 荔枝 "
             "龙眼 桂圆 山楂 枣 红枣 枸杞 无花果 牛油果 柿 木瓜 椰"),
    # 蛋白
    ("蛋白", "鸡胸 鸡腿 鸡翅 鸡肉 鸡 鸭 鹅 鸽 鹌鹑 五花 里脊 梅花肉 排骨 肋排 腩 "
             "蹄 肘 肉末 肉馅 肉丝 肉片 肉 牛 羊 猪 火腿 培根 香肠 腊肠 午餐肉 "
             "虾 蟹 鱼 贝 蛤 蚬 蚝 鱿 墨鱼 章鱼 扇贝 海参 海米 虾皮 虾仁 鱼丸 鱼片 "
             "蛋 皮蛋 咸蛋 鸭蛋 鹅蛋 鹌鹑蛋 蛋清 蛋黄 蛋白液 "
             "豆腐 豆干 豆皮 腐竹 千张 素鸡 油豆腐 豆泡 豆腐丝 豆花 内酯豆腐 "
             "黄豆 黑豆 青豆 鹰嘴豆 毛豆 天贝 纳豆 蛋白粉 面筋 烤麸 油面筋"),
    # 主食
    ("主食", "米饭 大米 粳米 籼米 糙米 黑米 紫米 糯米 小米 燕麦 麦片 荞麦 藜麦 "
             "薏米 高粱 面粉 面条 挂面 拉面 意面 通心粉 粉丝 粉条 米粉 河粉 米线 年糕 "
             "馒头 花卷 包子 饺子 馄饨 面包 吐司 贝果 法棍 玉米 红薯 紫薯 山药 "
             "芋头 土豆 马铃薯 魔芋 藕 莲藕 南瓜 板栗 凉皮 疙瘩 "
             "地瓜粉 澄粉 碱水面 细面条 烙饼 白吉馍 馍 面粉 米粉干 锅巴 油条 杂粮饭 "
             "手擀面 宽面条 绿豆面 荞麦面 玉米面 黍米 黄米"),
    # 蛋白 追加（豆类与水产别名，放在主食之后不影响判定顺序）
    ("蛋白", "红豆 绿豆 白芸豆 花豆 眉豆 海蛎 生蚝 蚵 白干 血肠 皮冻 猪皮 牛肚 百叶 "
             "鸡胗 鸡心 鸭胗 猪心 猪肚 猪蹄 蹄筋"),
    # 蔬菜（最宽的一类，放最后，避免抢走上面）
    ("蔬菜", "白菜 青菜 菠菜 油菜 生菜 油麦菜 芹菜 香芹 西芹 韭菜 韭黄 茼蒿 苋菜 "
             "芥菜 雪菜 酸菜 梅干菜 芽菜 荠菜 马兰头 空心菜 蕹菜 木耳菜 落葵 "
             "西兰花 花菜 菜花 甘蓝 包菜 圆白菜 卷心菜 紫甘蓝 芥蓝 菜心 菜薹 娃娃菜 "
             "萝卜 胡萝卜 青萝卜 水萝卜 樱桃萝卜 洋葱 大葱 小葱 香葱 蒜苗 蒜薹 蒜黄 "
             "姜 蒜 葱 香菜 芹菜 莴笋 芦笋 竹笋 春笋 冬笋 茭白 荸荠 菱角 "
             "番茄 西红柿 茄子 青椒 红椒 彩椒 甜椒 尖椒 杭椒 螺丝椒 辣椒 "
             "黄瓜 冬瓜 丝瓜 苦瓜 西葫芦 佛手瓜 南瓜 瓠瓜 秋葵 豇豆 四季豆 豆角 "
             "扁豆 荷兰豆 豌豆 蚕豆 毛豆 豆芽 绿豆芽 黄豆芽 "
             "香菇 平菇 金针菇 杏鲍菇 口蘑 白蘑菇 草菇 茶树菇 海鲜菇 鸡腿菇 松茸 "
             "牛肝菌 银耳 木耳 黑木耳 蘑菇 白玉菇 榛蘑 蟹味菇 菌 "
             "笋干 榨菜 泡菜 萝卜干 梅干菜 干豆角 脱水蔬菜 笋 鲜百合 百合 上海青 "
             "酸腌菜 腌菜 雪里蕻 儿菜 菜心 芥菜 冬寒菜 苕尖 南瓜尖 红薯叶 "
             "海带 紫菜 裙带菜 海藻 石花菜 螺旋藻"),
]

_ROLE_LIST = []
for _role, _words in ROLE_KEYWORDS:
    for _w in _words.split():
        _ROLE_LIST.append((_w, _role))
# 长的优先：「青椒」要在「椒」之前，「牛奶」要在「牛」之前
_ROLE_LIST.sort(key=lambda x: -len(x[0]))

_SEQ_RE = re.compile(r"[、和及与/,，]")


def _cn_to_num(s):
    if not s:
        return None
    if s in _CN_NUM:
        return _CN_NUM[s]
    if all(c in _NUM_CHARS for c in s):
        # 十一 / 二十三 这类
        if "十" in s:
            a, _, b = s.partition("十")
            tens = _CN_NUM.get(a, 1) if a else 1
            ones = _CN_NUM.get(b, 0) if b else 0
            return tens * 10 + ones
        return _CN_NUM.get(s[0])
    return None


_AMOUNT_RE = re.compile(
    r"^(?P<name>.*?)[\s　]*"
    r"(?:(?P<num>\d+(?:\.\d+)?|[%s]+)\s*(?P<amt>半)?\s*)?" % _NUM_CHARS +
    r"(?P<unit>千克|公斤|kg|毫升|ml|克|g|斤|两|升|l|大勺|小勺|汤匙|茶匙|勺|杯|碗|"
    r"小把|把|撮|滴|片|瓣|根|个|只|条|块|颗|朵|张|段|寸|包|袋|盒|瓶|罐|串|枚|粒|头|扎|束)?"
    r"\s*(?P<tail>适量|少许|若干|各|左右|上下|约)?\s*$"
)


def normalize(name):
    """去掉切法后缀与状态前缀，得到「食材本来是什么」。"""
    s = (name or "").strip()
    s = s.replace("（", "(").replace("）", ")")
    s = re.sub(r"\([^)]*\)", "", s)          # （泡发）（去皮）这类注解
    s = re.sub(r"\s+", "", s)

    changed = True
    while changed:
        changed = False
        for p in _PREFIX:
            if s.startswith(p) and len(s) > len(p) + 1:
                s = s[len(p):]
                changed = True
                break
        for suf in _SHAPE:
            if s.endswith(suf) and len(s) > len(suf) + 1:
                s = s[: -len(suf)]
                changed = True
                break
    return s.strip()


def is_seasoning(name):
    """调味判定。顺序不能反：
    先看原名是否就是调味（「盐」「生抽」），
    再问原名能不能判出角色 —— 这一问专门救「油条」「粉条」「豆干」：
    这些名字剥掉切法后缀会变成「油」「粉」「豆」，一律会被误判成调味。
    最后才退回剥壳名。
    """
    raw = (name or "").strip()
    n = normalize(raw)
    if not n:
        return True
    if raw in _SEASONING_RESCUE or n in _SEASONING_RESCUE:
        return False
    if raw in SEASONING:
        return True
    r = role_of(raw)
    if r is not None:
        return r == "调味"      # 库里的「姜/大蒜/油」角色就是调味
    if n in SEASONING:
        return True
    for s in SEASONING:
        if len(s) >= 2 and n.endswith(s) and len(n) - len(s) <= 1:
            return True
    return False


def role_of(name):
    """名称 → 角色。先查成分表（含俗称），再走关键词。认不出返回 None。

    原名和剥壳名都试，原名优先：库里有 103 种食材，剥壳是为了兼容
    「姜片」「蒜末」，但不能让剥壳把「油条」变成「油」。
    """
    from . import foods

    raw = (name or "").strip()
    if not raw:
        return None
    cands = [raw]
    n = normalize(raw)
    if n and n != raw:
        cands.append(n)
    for cand in cands:
        key = foods.NAME2KEY.get(cand)
        if key:
            return foods.FOODS[key]["role"]
    for cand in cands:
        for w, role in _ROLE_LIST:
            if w in cand:
                return role
    return None


def resolve_key(name):
    """名称 → 成分表 key。认不出返回 None（不硬猜）。"""
    from . import foods

    raw = (name or "").strip()
    if not raw:
        return None
    for cand in (raw, normalize(raw)):
        k = foods.NAME2KEY.get(cand)
        if k:
            return k
    # 反向包含：库里有「鸡胸脯肉」，菜谱写「鸡腿肉」「猪里脊」这类带部位的写法。
    # 只在剥壳名上做，且要求命中片段够长，避免「肉」把一切都匹上。
    best = None
    n = normalize(raw)
    for k, it in foods.FOODS.items():
        for alias in (it["name"], foods._strip(it["name"])):
            if not alias or len(alias) < 2:
                continue
            if alias in n or alias in raw:
                if best is None or len(alias) > len(best[0]):
                    best = (alias, k)
    return best[1] if best else None


def parse_line(text):
    """一行食材 → {name, amount, unit, grams, role, seasoning, note}。

    「黑胡椒、海盐 适量」这类合并写法会被拆成两条，共享同一个用量。
    """
    s = (text or "").strip()
    if not s:
        return []
    m = _AMOUNT_RE.match(s)
    if not m:
        return [{"name": s, "amount": None, "unit": "", "grams": 0.0,
                 "role": role_of(s), "seasoning": is_seasoning(s), "note": ""}]

    head = (m.group("name") or "").strip()
    num = m.group("num")
    amt_half = m.group("amt")
    unit = (m.group("unit") or "").strip()
    tail = (m.group("tail") or "").strip()

    # 源数据里有「桂皮 1 小」这种被截断的单位（原意 1 小勺），
    # 正则会退化成「整串都是名字」。数字后面剩下认不出的东西一律丢掉，
    # 否则食材名里带数字，永远匹配不上任何词表。
    if re.search(r"\d", head):
        head = re.split(r"[\s　]*\d", head)[0].strip()
        if not num:
            num = "1"

    amount = None
    if num:
        amount = float(num) if re.match(r"^\d", num) else _cn_to_num(num)
    if amt_half == "半" and amount is None:
        amount = 0.5
    elif amt_half == "半" and amount is not None:
        amount += 0.5

    names = [x.strip() for x in _SEQ_RE.split(head) if x.strip()] or [head]
    if tail in ("适量", "少许", "若干", "各", "左右", "上下", "约") and amount is None:
        if unit in _UNIT_G:                   # 「1 小把」这种已带单位
            pass
        else:
            amount, unit = None, unit

    out = []
    for nm in names:
        role = role_of(nm)
        sea = is_seasoning(nm)
        grams = to_grams(amount, unit, role, nm, tail)
        out.append({"name": nm, "amount": amount, "unit": unit, "grams": grams,
                    "role": role, "seasoning": sea, "note": tail})
    return out


def to_grams(amount, unit, role, name, tail=""):
    """用量 → 克。估不出来给 0，让上层决定怎么兜。"""
    role = role or "蔬菜"
    if unit and unit in _UNIT_G:
        base = _UNIT_G[unit]
        if amount is None:
            return 0.0
        return round(amount * base, 1)
    if unit and unit in _PIECE_UNIT:
        per = _PIECE_G.get(role, 100)
        if amount is None:
            return float(per)
        return round(amount * per, 1)
    if amount is None:
        # 「盐 适量」「姜 少许」：调味按角色给很小值，主料按一份给
        if is_seasoning(name):
            return 3.0
        return float(_AS_NEEDED_G.get(role, 100))
    # 有数字没单位：「鸡蛋 2」「番茄 1」
    if amount <= 12:
        return round(amount * _PIECE_G.get(role, 100), 1)
    return round(amount, 1)


# ---------------------------------------------------------------- 份量口径
def staple_grams(recipe_serving_amount, recipe_kcal, my_kcal_per100):
    """按热量对齐反推克数 —— 用来校验菜谱标注与我们实算的偏差。"""
    if not my_kcal_per100:
        return recipe_serving_amount
    return round(recipe_kcal / my_kcal_per100 * 100, 1)
