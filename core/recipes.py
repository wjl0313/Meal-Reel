#!/usr/bin/env python3
"""以「真实菜谱」为模板做推荐，并允许用库存里的食材平替。

和 planner.py 的区别（两个模式并存，不是替换）：
  planner  —— 自底向上：从库存里的蛋白/蔬菜/主食枚举组合，自己造菜名。
              优点是永远做得出来、价格可控；缺点是菜名和做法是生成的，
              翻来覆去就那几种，而且没有「步骤」。
  recipes  —— 自顶向下：从 1093 道真实菜谱里选一道，再把缺的食材换成库存里
              有的同类。优点是菜是真的、有真实步骤和分量；缺点是可能换得不伦不类。

平替是这个模块的核心。三条原则：
  1. **同角色才替**：蛋白替蛋白、蔬菜替蔬菜。拿豆腐替鸡肉会让整道菜失去意义。
  2. **同细分优先**：叶菜替叶菜、菌藻替菌藻、禽肉替禽肉。跨细分要扣分，
     但不是禁止 —— 库存里没有叶菜时，用菌藻替总比做不出来强。
  3. **替了要说**：每处替换都记下原物 → 替身 → 原因，一路带到文案和成片里。
     不说的话，用户看到的菜名和实际下锅的东西对不上，这比不推荐更糟。

营养怎么算（这是最容易糊弄的地方，说清楚）：
  菜谱自带的 nutrition 是按它自己的食材算的，替换后必然变化。做法是：
    · 能对到成分表的主料 → 用库存实算，替换后的克数按实际
    · 对不到的（五花肉、牛腩这种库里没有的部位）+ 全部调味 → 保留菜谱原值
  即：actual = Σ(可算部分实算) + max(0, 菜谱标注 - Σ(可算部分的原值))
  这样替换只影响能核算的部分，算不准的部分不会被凭空造出来，也不会被抹掉。
"""
import json
import os
import random

from . import foods, lexicon

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECIPES_PATH = os.path.join(ROOT, "data", "recipes.json")

# ---------------------------------------------------------------- 目标
# 每类菜谱的营养目标区间。分类本身就是目标（减脂/增肌/维持…），
# 所以目标不另设参数，直接跟着分类走 —— 用户选「增肌餐」就是选了这套阈值。
TARGET = {
    "fat-loss":    {"kcal": (280, 480), "protein": 30, "veg": 200, "dark": 100},
    "muscle-gain": {"kcal": (500, 820), "protein": 40, "veg": 150, "dark": 80},
    "maintain":    {"kcal": (450, 700), "protein": 30, "veg": 200, "dark": 100},
    "nutrition":   {"kcal": (400, 700), "protein": 28, "veg": 250, "dark": 120},
    "home":        {"kcal": (450, 800), "protein": 25, "veg": 150, "dark": 70},
    "therapy":     {"kcal": (250, 600), "protein": 22, "veg": 200, "dark": 90},
    "bakery":      {"kcal": (200, 500), "protein": 12, "veg": 60, "dark": 20},
    "snack":       {"kcal": (150, 450), "protein": 10, "veg": 60, "dark": 20},
}

CATEGORY_LABEL = {
    "fat-loss": "减脂餐", "muscle-gain": "增肌餐", "maintain": "维持餐",
    "nutrition": "营养餐", "home": "家常菜", "therapy": "食疗",
    "bakery": "烘焙", "snack": "小吃",
}

WEIGHT = {"target": .26, "fidelity": .24, "cost": .14,
          "expiry": .12, "time": .12, "variety": .12}

# 平替保真度。分档的依据不是"营养像不像"，而是"这东西下锅后还是不是这道菜"：
#   同一食材 1.00 / 同物种同形态（牛肉→羊肉、菠菜→小白菜）0.90 /
#   同角色跨物种（牛肉→豆腐）0.60 / 内脏只替内脏
# 早期版本只按「角色」分档，结果牛肉被替换成猪肝还算 0.95 保真，
# 「黑椒牛肉粒·猪肝版」这种菜就推出来了。物种必须进分组键。
FID_SAME_GROUP = 0.90
FID_CROSS_GROUP = 0.60
FID_UNRESOLVED = 0.88          # 原食材判不出是什么，只能按角色替
FID_STAR_EXTRA = 0.85          # 替换的是菜名里的主角，再打折
PENALTY_UNRESOLVED = 0.90      # 有主料完全无同类可替，整道菜打折

# 内脏：能替内脏，不能替肉。维生素 A 与胆固醇都高，一周 1-2 次合适。
ORGAN = {"081214"}             # 猪肝。鸡血鸭血走另一条线，见 _group
OCCASIONAL_PENALTY = 0.86

TIER_RANK = {"经济": 0, "常规": 1, "偏高": 2, "昂贵": 3}
TIER_CAP = {"昂贵": 1, "偏高及以上合计": 2}

# 库存不足时给主料的兜底份量（按角色）
DEFAULT_G = {"蛋白": 150, "蔬菜": 150, "主食": 60, "乳品": 200, "坚果": 15,
             "水果": 150, "调味": 8}

# 熟主食的每 100g 热量。菜谱里「米饭 200 克」「面条 150 克」写的是**熟重**，
# 而成分表里的稻米/挂面是**生重**（346/286 kcal）。直接把熟重套到生米上，
# 一碗饭会变成 690 千卡 —— 热量全错，而且错得很隐蔽（菜名分量看着都对）。
# 这张表就是为了把两套口径对齐：能对上熟主食时，按热量反推生重。
COOKED_STAPLE = {
    "米饭": 116, "白米饭": 116, "糙米饭": 112, "杂粮饭": 120, "米饭团": 130,
    "粥": 46, "白粥": 46, "小米粥": 46, "燕麦粥": 70, "杂粮粥": 55,
    "面条": 110, "熟面条": 110, "汤面": 110, "炒面": 160, "拉面": 130,
    "馒头": 223, "花卷": 214, "包子": 227, "饺子": 240, "馄饨": 190,
    "面包": 250, "吐司": 280, "粉丝": 80, "熟粉丝": 80, "年糕": 154,
    "藜麦": 120, "熟藜麦": 120, "意大利面": 131, "意面": 131, "土豆泥": 90,
}


def _adjust_for_cooked(m, key, want):
    """熟重 → 生重。只在原写法是熟主食、且替身热量密度差很多时才动手。"""
    base = COOKED_STAPLE.get(m["norm"]) or COOKED_STAPLE.get(m["name"].strip())
    if not base or not key:
        return want, None
    sk = foods.FOODS[key]["kcal"]
    if sk <= 0 or abs(sk - base) / base <= 0.35:
        return want, None                 # 本来就差不多，不折腾
    g = want * base / sk
    g = max(want * 0.4, min(want * 2.0, g))   # 夹一下，防极端值
    return round(g, 1), {"from_grams": want, "to_grams": round(g, 1),
                         "why": f"{m['name']}记的是熟重 {base}kcal/100g，"
                                f"{foods.disp(key)}是 {sk:.0f}kcal/100g"}

_CACHE = {}


# ---------------------------------------------------------------- 读
def load(path=None):
    path = path or RECIPES_PATH
    if path in _CACHE:
        return _CACHE[path]
    if not os.path.exists(path):
        _CACHE[path] = []
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    rs = data.get("recipes", data if isinstance(data, list) else [])
    _CACHE[path] = rs
    return rs


def facets(recipes):
    """给前端下拉框用的可选项统计。"""
    import collections
    out = {"category": collections.Counter(), "cuisine": collections.Counter(),
           "method": collections.Counter(), "flavor": collections.Counter(),
           "mealType": collections.Counter()}
    for r in recipes:
        out["category"][r["category"]] += 1
        out["cuisine"][r["cuisine"]] += 1
        for k in r["methods"]:
            out["method"][k] += 1
        for k in r["flavors"]:
            out["flavor"][k] += 1
        for k in r["mealType"]:
            out["mealType"][k] += 1
    return out


# ---------------------------------------------------------------- 筛
def filter_recipes(recipes, cfg):
    """按配置筛候选。筛完为空要能说清是哪一条筛没的，否则用户只会觉得"坏了"。"""
    cats = cfg.get("categories") or []
    slots = cfg.get("meal_types") or []
    cuis = cfg.get("cuisines") or []
    methods = cfg.get("methods") or []
    flavors = cfg.get("flavors") or []
    max_min = int(cfg.get("max_minutes") or 0)
    exclude = {lexicon.normalize(x) for x in (cfg.get("exclude") or [])}

    steps = [("分类", 0), ("餐次", 0), ("菜系", 0), ("做法", 0),
             ("口味", 0), ("时长", 0), ("忌口", 0)]
    keep = []
    for r in recipes:
        if cats and r["category"] not in cats:
            continue
        steps[0] = (steps[0][0], steps[0][1] + 1)
        if slots and not (set(r["mealType"]) & set(slots)):
            continue
        steps[1] = (steps[1][0], steps[1][1] + 1)
        if cuis and r["cuisine"] not in cuis:
            continue
        steps[2] = (steps[2][0], steps[2][1] + 1)
        if methods and not (set(r["methods"]) & set(methods)):
            continue
        steps[3] = (steps[3][0], steps[3][1] + 1)
        if flavors and not (set(r["flavors"]) & set(flavors)):
            continue
        steps[4] = (steps[4][0], steps[4][1] + 1)
        if max_min and r["minutes"] > max_min:
            continue
        steps[5] = (steps[5][0], steps[5][1] + 1)
        if exclude and any(lexicon.normalize(m["name"]) in exclude
                           or m["norm"] in exclude for m in r["mains"]):
            continue
        steps[6] = (steps[6][0], steps[6][1] + 1)
        keep.append(r)
    return keep, steps


# ---------------------------------------------------------------- 平替
def _leaf(key):
    return foods.FOODS[key]["category"].split("-")[-1]


def _group(key):
    """平替分组：同组互换才算「同类」，跨组要扣分。

    分组键按「下锅后还是不是这道菜」定，不是按成分表的目录树：
      · 畜肉/禽肉/水产 用「大类-物种」—— 牛、羊、猪各是一组，
        因为「黑椒牛肉粒」换羊肉还成立，换猪肝就不成立
      · 蔬菜用叶类 —— 叶菜、根菜、瓜果、菌藻互替效果差别很大
      · 干豆 vs 豆制品要分开 —— 成分表把黄豆和豆腐都归在「大豆」下，
        但一个是抓一把泡发、一个是买一块下锅，互换等于换个菜
      · 谷物合并成一组 —— 米饭、面条、糙米互换本来就是家常做法
    """
    it = foods.FOODS[key]
    cat = it["category"]
    head, _, leaf = cat.partition("-")
    head = head.replace("及其制品", "")

    if key in ORGAN or "血" in it["name"]:
        return f"{head}-内脏"
    if head == "蔬菜类":
        return leaf
    if head == "菌藻类":
        return "菌藻"
    if head == "干豆类":
        return "干豆" if it.get("dry") else "豆制品"
    if head in ("畜肉类", "禽肉类", "鱼虾蟹贝类"):
        return f"{head}-{leaf}"
    if head == "蛋类":
        return "蛋"
    if head == "谷类":
        return "谷物"
    if head == "薯类淀粉":
        return "薯"
    if head == "乳类":
        return "乳"
    if head == "坚果种子类":
        return "坚果"
    return head


def _role_avg(role):
    """某角色的平均营养密度（每 100g），给判不出具体食材时当替身目标。"""
    vals = [foods.FOODS[k] for k in foods.FOODS
            if foods.FOODS[k]["role"] == role and k != "oil"]
    if not vals:
        return {"kcal": 100.0, "protein": 8.0}
    n = len(vals)
    return {"kcal": sum(v["kcal"] for v in vals) / n,
            "protein": sum(v["protein"] for v in vals) / n}


_AVG = {}


def _inventory(inv):
    """库存索引：按 key 去重（保留克数最大的那条），并按角色分组。"""
    by_key, by_role = {}, {}
    for it in inv:
        k = it["key"]
        if k not in by_key or it["edible_g"] > by_key[k]["edible_g"]:
            by_key[k] = it
    for k, it in by_key.items():
        by_role.setdefault(foods.FOODS[k]["role"], []).append(k)
    return by_key, by_role


def _sub_score(cand, target_kcal, target_prot, target_groups, by_key):
    """替身打分：同组契合 > 营养相近 > 便宜 > 临期。

    同组给的分必须显著大于营养相近能给的，否则会出现「用豆腐替牛肉，
    因为豆腐的蛋白/热量比更接近目标区间」这种反直觉结果。
    """
    it = foods.FOODS[cand]
    s = 0.0
    s += 2.2 if _group(cand) in target_groups else 0.0
    s -= (abs(it["kcal"] - target_kcal) / 200.0) * 0.8
    s -= (abs(it["protein"] - target_prot) / 15.0) * 0.5
    s -= TIER_RANK.get(foods.tier(cand), 1) * 0.10
    inv_it = by_key[cand]
    if inv_it["days_left"] <= 2:
        s += 0.18                       # 临期优先消耗，但别盖过同类
    return s


def substitute(recipe, inv, cfg=None):
    """把一道菜谱落到库存上：能用的直接用，缺的换同类，并记录每处替换。

    返回 dict：
      items     实际下锅的食材（含库存 key 与克数）
      swaps     替换明细 [{from, to, reason, factor}]
      missing   库存里连同类都没有的主料
      fidelity  0-1 的保真度
    """
    by_key, by_role = _inventory(inv)
    name = recipe.get("name") or ""
    items, swaps, missing = [], [], []
    fid_num, fid_den = 0.0, 0.0

    for m in recipe["mains"]:
        role = m["role"]
        want = float(m["grams"] or 0) or DEFAULT_G.get(role, 100)
        src_key = m["key"]
        # 菜名里点名的食材是这道菜的主角，换了就不是这道菜了
        is_star = bool(m["norm"]) and m["norm"] in name

        # ---- 情形一：库存里就有这一样 ----
        if src_key and src_key in by_key and foods.FOODS[src_key]["role"] != "调味":
            w, adj = _adjust_for_cooked(m, src_key, want)
            g = min(w, by_key[src_key]["edible_g"])
            items.append({"key": src_key, "grams": round(g, 1),
                          "from": m["name"], "swapped": False, "adjust": adj})
            fid_num += g * 1.0
            fid_den += g
            continue

        # ---- 情形二：同角色找替身 ----
        pool = [k for k in by_role.get(role, []) if k != "oil"]
        if not pool:
            if role in ("调味",) or role is None:
                continue
            missing.append({"name": m["name"], "role": role})
            fid_num += want * 0.25
            fid_den += want
            continue

        # 用量很小的主料当 garnish 处理：「虾皮 10 克」不值得为它在库存里
        # 抓一块 150 克的猪肝顶上 —— 营养上是小事，菜名和成品画面全跑偏。
        # 这类只允许同组替换，同组没有就当缺。
        tiny = want < 30

        if src_key:
            tgt_kcal = foods.FOODS[src_key]["kcal"]
            tgt_prot = foods.FOODS[src_key]["protein"]
            tgt_groups = {_group(src_key)}
        else:
            if role not in _AVG:
                _AVG[role] = _role_avg(role)
            tgt_kcal = _AVG[role]["kcal"]
            tgt_prot = _AVG[role]["protein"]
            tgt_groups = set()

        if tiny:
            same = [k for k in pool if tgt_groups and _group(k) in tgt_groups]
            if not same:
                missing.append({"name": m["name"], "role": role,
                                "soft": True, "grams": want})
                fid_num += want * 0.55          # 少量配料的缺失不重罚
                fid_den += want
                continue
            pool = same

        best = max(pool, key=lambda k: _sub_score(k, tgt_kcal, tgt_prot,
                                                  tgt_groups, by_key))
        w, adj = _adjust_for_cooked(m, best, want)
        g = min(w, by_key[best]["edible_g"])
        same_group = bool(tgt_groups) and _group(best) in tgt_groups
        if src_key:
            factor = FID_SAME_GROUP if same_group else FID_CROSS_GROUP
            reason = "同物种替换" if same_group else "同角色跨物种替换"
        else:
            factor = FID_UNRESOLVED
            reason = "按角色替换（原食材不在成分表）"
        if is_star:
            factor *= FID_STAR_EXTRA
            reason += "·换掉了菜名主角"
        items.append({"key": best, "grams": round(g, 1),
                      "from": m["name"], "swapped": True, "adjust": adj})
        swaps.append({"from": m["name"], "to": foods.disp(best),
                      "grams": round(g, 1), "star": is_star,
                      "reason": reason, "factor": round(factor, 3)})
        fid_num += g * factor
        fid_den += g

    # 调味：油固定给一勺，其余库存有的调味各给一点（葱姜蒜）
    items.append({"key": "oil", "grams": 8.0, "from": "食用油", "swapped": False})
    fid_num += 8 * 1.0
    fid_den += 8
    for k in by_role.get("调味", []):
        if k != "oil" and k in by_key:
            items.append({"key": k, "grams": 6.0, "from": "葱姜蒜",
                          "swapped": False})
            fid_num += 6
            fid_den += 6

    # 同一食材可能被两道主料用到（"生抽"不会，但"鸡蛋"会），按 key 合并
    merged = {}
    for it in items:
        if it["key"] in merged:
            merged[it["key"]]["grams"] += it["grams"]
        else:
            merged[it["key"]] = dict(it)
    items = list(merged.values())

    return {"items": items, "swaps": swaps, "missing": missing,
            "fidelity": round(fid_num / fid_den, 3) if fid_den else 0.0}


# ---------------------------------------------------------------- 打分
def _urgency(days_left):
    if days_left <= 0:
        return 1.00
    if days_left == 1:
        return 0.95
    if days_left <= 3:
        return 0.75
    if days_left <= 6:
        return 0.40
    return 0.15


def _kcal_fit(actual, lo, hi):
    if lo <= actual <= hi:
        return 1.0
    span = max(1.0, (hi - lo) / 2)
    d = lo - actual if actual < lo else actual - hi
    return max(0.0, 1.0 - d / span)


def _cost_score(cost, budget):
    return 1.0 - min(1.0, max(0.0, (cost - 5.0) / max(1.0, budget - 5.0)))


def evaluate(recipe, inv, cfg, by_key=None):
    """把一道菜谱 + 库存 → 可比的分数与完整的 menu 结构。"""
    budget = float(cfg.get("budget") or 25)
    cat = recipe["category"]
    # 评分该用哪套营养目标：默认跟着菜谱自己的分类走。
    # 但「减脂 + 家常」混选时必须能把标准钉死在减脂上 —— 否则家常菜按它自己的
    # 450–800 千卡去评分，600 千卡的红烧菜会拿满分，减脂就名存实亡。
    tgt = TARGET.get(cfg.get("target_category") or cat, TARGET.get(cat, TARGET["home"]))
    # by_key 是 plan() 里为整批候选算好的库存索引，传进来是为省一次遍历。
    # 但它是可选参数，单独调用（后台看单道菜、脚本试算）时不会传 ——
    # 原来这里没兜底，直接 `i["key"] in None` 崩在列表推导里。
    if by_key is None:
        by_key, _ = _inventory(inv)

    sub = substitute(recipe, inv, cfg)
    if not sub["items"]:
        return None

    # ---- 营养：可算部分实算 + 菜谱剩余部分 ----
    resolved_now = [(i["key"], i["grams"]) for i in sub["items"]]
    actual = foods.total(resolved_now)

    # 原文里能对到成分表的主料，其"原始"营养和（用来算残差）。
    # 口径必须和 substitute 一致：熟主食按热量折算成生重，否则这里的
    # 200g 生米会把残差压成 0，菜谱里那些「对不到成分表」的部分就被吞掉了。
    orig_sum = {"protein": 0.0, "carb": 0.0, "fat": 0.0}
    for m in recipe["mains"]:
        if not m["key"] or foods.FOODS[m["key"]]["role"] == "调味":
            continue
        g = float(m["grams"] or 0) or DEFAULT_G.get(m["role"], 100)
        g, _ = _adjust_for_cooked(m, m["key"], g)
        n = foods.nutri(m["key"], g)
        orig_sum["protein"] += n["protein"]
        orig_sum["carb"] += n["carb"]
        orig_sum["fat"] += n["fat"]
    rn = recipe.get("nutrition") or {}
    residual = {k: max(0.0, float(rn.get(k) or 0) - orig_sum[k])
                for k in ("protein", "carb", "fat")}

    protein = actual["protein"] + residual["protein"]
    carb = actual["carb"] + residual["carb"]
    fat = actual["fat"] + residual["fat"]
    kcal = protein * 4 + carb * 4 + fat * 9

    cost = foods.basket_cost(resolved_now)
    if cost > budget:
        return {"_over_budget": True, "cost": round(cost, 2)}

    tiers = [TIER_RANK.get(foods.tier(i["key"]), 1) for i in sub["items"]]
    if sum(1 for t in tiers if t >= 3) > TIER_CAP["昂贵"]:
        return {"_over_tier": True, "cost": round(cost, 2)}
    if sum(1 for t in tiers if t >= 2) > TIER_CAP["偏高及以上合计"]:
        return {"_over_tier": True, "cost": round(cost, 2)}

    lo, hi = tgt["kcal"]
    f_target = (0.55 * _kcal_fit(kcal, lo, hi)
                + 0.45 * min(1.0, protein / max(1.0, tgt["protein"])))
    f_fid = sub["fidelity"]
    f_cost = _cost_score(cost, budget)
    used_urg = [by_key[i["key"]]["days_left"] for i in sub["items"] if i["key"] in by_key]
    f_exp = sum(_urgency(d) for d in used_urg) / max(1, len(used_urg))
    f_time = 1 - min(1.0, max(0.0, (recipe["minutes"] - 20) / 60.0))
    # 多样性：主料种类越多、跨的角色越多，越不容易"又是一盘鸡胸"
    roles = {foods.FOODS[i["key"]]["role"] for i in sub["items"]}
    f_var = min(1.0, (len(sub["items"]) / 6.0) * 0.5 + (len(roles) / 3.0) * 0.5)

    parts = {"target": round(f_target, 3), "fidelity": round(f_fid, 3),
             "cost": round(f_cost, 3), "expiry": round(f_exp, 3),
             "time": round(f_time, 3), "variety": round(f_var, 3)}
    score = sum(parts[k] * WEIGHT[k] for k in WEIGHT)

    penalty = 1.0
    # 只对「真的缺主料」重罚。少量配料缺（10 克虾皮）已经在 fidelity 里扣过了，
    # 这里再罚一次会把「素汤少一把虾皮」和「牛排做不出来」判成一样差。
    if any(not x.get("soft") for x in sub["missing"]):
        penalty *= PENALTY_UNRESOLVED
    # 换得越多越不是这道菜。第一处不罚（一道菜换一样很正常），
    # 之后每处 4%，下限 70% —— 不然一份沙拉被换掉四样还是高分。
    penalty *= max(0.70, 1.0 - 0.04 * max(0, len(sub["swaps"]) - 1))
    organ_hit = any(i["key"] in ORGAN for i in sub["items"])
    if organ_hit:
        penalty *= OCCASIONAL_PENALTY

    flags = []
    hard_missing = [x for x in sub["missing"] if not x.get("soft")]
    soft_missing = [x for x in sub["missing"] if x.get("soft")]
    if organ_hit:
        flags.append("含内脏，维生素A与胆固醇偏高，每周 1-2 次为宜")
    if hard_missing:
        flags.append("缺 " + "、".join(x["name"] for x in hard_missing)
                     + "，且库存里没有同类可替")
    if soft_missing:
        flags.append("少量配料缺：" + "、".join(x["name"] for x in soft_missing[:3]))
    if abs(kcal - float(recipe.get("kcal") or kcal)) / max(1.0, kcal) > 0.25:
        flags.append("换过后热量与菜谱标注差较多（菜谱写 "
                     f"{recipe.get('kcal')}，实算 {kcal:.0f}）")
    if sub["swaps"]:
        flags.append("已平替：" + "；".join(
            f"{s['from']}→{s['to']}" for s in sub["swaps"][:4]))

    items = [{"key": i["key"], "name": foods.label(i["key"]),
              "disp": foods.disp(i["key"]), "edible_g": i["grams"],
              "cooked_g": foods.raw_to_cooked(i["key"], i["grams"]),
              "role": foods.FOODS[i["key"]]["role"],
              "tier": foods.tier(i["key"]), "tier_sym": foods.tier_sym(i["key"]),
              "cost": round(foods.cost(i["key"], i["grams"]), 2),
              "price_kg": foods.price_kg(i["key"]),
              "from": i["from"], "swapped": i["swapped"],
              "adjust": i.get("adjust")}
             for i in sub["items"]]

    prots = [i["key"] for i in items if i["role"] == "蛋白"]
    vegs = [i["key"] for i in items if i["role"] == "蔬菜"]
    staples = [i["key"] for i in items if i["role"] == "主食"]

    name = recipe["name"]
    # 只有"有分量的替换"才改菜名：菜名里点名的主角，或者用量 ≥40 克的主料。
    # 10 克虾皮换成 10 克猪肝营养上无关紧要，但叫「菠菜豆腐汤·猪肝版」
    # 就把一道素汤说成了内脏汤 —— 名字比数字更容易误导人。
    big = [s for s in sub["swaps"] if s.get("grams", 0) >= 40 or s.get("star")]
    if big:
        tos = [s["to"] for s in big]
        name = f"{recipe['name']}·{'、'.join(tos[:2])}版"

    return {
        "name": name,
        "short": recipe["name"],
        "recipe_id": recipe["id"],
        "category": cat,
        "category_label": CATEGORY_LABEL.get(cat, cat),
        "cuisine": recipe["cuisine"],
        "methods": recipe["methods"],
        "flavors": recipe["flavors"],
        "emoji": recipe["emoji"],
        "desc": recipe["desc"],
        "steps": recipe["steps"],
        "serving": recipe["serving"],
        "recipe_kcal": recipe.get("kcal"),
        "items": items,
        "swaps": sub["swaps"],
        "missing": sub["missing"],
        "fidelity": sub["fidelity"],
        "facts": {"kcal": int(round(kcal)), "protein": round(protein, 1),
                  "fat": round(fat, 1), "carb": round(carb, 1),
                  "fiber": round(actual["fiber"], 1),
                  "veg_g": int(actual["veg_g"]),
                  "dark_veg_g": int(actual["dark_veg_g"])},
        "minutes": recipe["minutes"], "score": round(score * penalty, 4),
        "parts": parts, "cost": round(cost, 2), "budget": budget,
        "cost_ratio": round(cost / budget, 2),
        "tier_mix": {t: sum(1 for x in tiers if TIER_RANK.get(t) == x)
                     for t in TIER_RANK},
        "flags": flags,
        "expensive_items": [foods.disp(i["key"]) for i in items
                            if TIER_RANK.get(foods.tier(i["key"]), 1) >= 2],
        "expiring_used": [i["key"] for i in items
                          if i["key"] in by_key and by_key[i["key"]]["days_left"] <= 2],
        "protein_key": prots[0] if prots else ("oil" if "oil" in foods.FOODS else None),
        "veg_keys": vegs or ([staples[0]] if staples else []),
        "uses_stock": len(sub["swaps"]) == 0,
    }


def plan(inv, cfg, topk=3):
    """菜谱模式的入口。返回结构与 planner.plan() 基本一致，run.py 不用改。"""
    budget = float(cfg.get("budget") or 25)
    recipes = load(cfg.get("recipes_path"))
    cands, steps = filter_recipes(recipes, cfg)
    if not cands:
        return {"error": "按当前筛选条件，一道菜谱都没剩下",
                "funnel": steps, "budget": budget, "dropped": []}

    by_key, _ = _inventory(inv)
    scored, over_budget, over_tier = [], 0, 0
    for r in cands:
        m = evaluate(r, inv, cfg, by_key)
        if m is None:
            continue
        if m.get("_over_budget"):
            over_budget += 1
            continue
        if m.get("_over_tier"):
            over_tier += 1
            continue
        scored.append(m)

    if not scored:
        return {"error": f"预算 ¥{budget:.0f} 内没有做得出的菜谱", "budget": budget,
                "funnel": steps, "over_budget": over_budget,
                "dropped": [], "hint": "提高预算，或往库存里补几样便宜食材"}

    # 三餐不重样 + 跨天不重样：调用方把「要排除的菜谱」传进来。
    #   · daily3.sh 传上一餐刚选的 → 保证早/午/晚不是同一道
    #   · run.py 传「冷却期内吃过的」→ 保证今天和前几天不重样
    # 全被排除时不能直接一道都出不来：退回全集，但按「上次吃的日子」排序，
    # 先用最久没吃的那道。这样即使必须重复，也是间隔最长的那次，而不是撞车。
    excluded = {str(x) for x in (cfg.get("exclude_recipes") or []) if x}
    lru = cfg.get("lru") or {}
    relaxed = False
    if excluded:
        kept = [m for m in scored if str(m.get("recipe_id")) not in excluded]
        if kept:
            scored = kept
        else:
            relaxed = True

    if relaxed:
        scored.sort(key=lambda m: (lru.get(str(m.get("recipe_id")), -1), -m["score"]))
    else:
        scored.sort(key=lambda m: -m["score"])
    # 去重：同一道菜谱只留一行，避免 Top3 变成同一道菜的三种替身组合
    seen, uniq = set(), []
    for m in scored:
        if m["recipe_id"] in seen:
            continue
        seen.add(m["recipe_id"])
        uniq.append(m)

    # 选哪一道：
    #   best   —— 永远拿第一名（后台「预演」走这条，同样的配置要看到同样的结果）
    #   random —— 在头部优质候选里随机，每天出的菜才不一样
    # 只在前 N 名里随机、且分数不低于第一名的 pick_floor 倍，避免为了不重样
    # 硬选一道明显更差的菜。
    pick = str(cfg.get("pick") or "best")
    chosen = uniq[0]
    if pick == "random" and len(uniq) > 1:
        pool_n = int(cfg.get("pick_pool") or 0) or max(5, min(40, len(uniq)))
        pool = uniq[:max(1, pool_n)]
        if not relaxed:
            floor = uniq[0]["score"] * float(cfg.get("pick_floor") or 0.85)
            pool = [m for m in pool if m["score"] >= floor] or uniq[:1]
        chosen = random.Random(cfg.get("seed")).choice(pool)

    top = [chosen] + [m for m in uniq if m["recipe_id"] != chosen["recipe_id"]]
    top = top[:max(1, topk)]

    return {"best": chosen, "top3": top, "dropped": [],
            "relaxed": relaxed,
            "n_combos": len(scored), "inventory_used": len(by_key),
            "budget": budget, "over_budget": over_budget, "over_tier": over_tier,
            "funnel": steps, "mode": "recipe",
            "cheapest": min(m["cost"] for m in scored),
            "dearest": max(m["cost"] for m in scored)}
