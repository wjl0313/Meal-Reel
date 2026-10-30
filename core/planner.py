#!/usr/bin/env python3
"""库存 → 菜单。硬过滤 + 组合枚举 + 加权打分，不含任何模型调用。

为什么不用「让模型想一道菜」：模型给不出稳定的排序依据，也无法保证过敏原
一定被排除。这里全是可复现的算术，同样的库存永远得到同样的 Top3，
出问题能一眼看出是哪一项分值导致的。

价格怎么进来（这是本版的关键改动）：
  只看营养的推荐器会稳定推出三文鱼、牛腱、对虾 —— 营养上确实最优，但一周
  吃三次不现实，用户会直接放弃这个工具。所以价格做成两层：
    · 硬约束：单餐成本超过预算的组合直接淘汰（不是扣分，是不参与排序）
    · 软扣分：预算内的组合按剩余空间给 cost 分，越省给分越高
  再加两条配额，防止「一道菜里堆三个贵价食材」：
    · 「昂贵」(¥¥¥¥) 最多 1 个
    · 「偏高」及以上合计最多 2 个
"""
import csv
import datetime as dt
import difflib
import os
import re

from . import foods

_QTY_RE = re.compile(r"^([\d.]+)\s*(.*)$")

NAME2KEY = foods.NAME2KEY

# ---------------------------------------------------------------- 角色与份量
# 份量按「一个人一顿」定。给的是可食部克数，成本按毛重折算（见 foods.cost）。
PORTION = {"蔬菜": 150, "蛋白": 150, "主食": 60, "坚果": 15,
           "乳品": 200, "调味": 8}

# 晚餐目标（成人 2000kcal/日 的 32%）
TARGET = {"kcal": 650, "protein": 35, "dark_veg": 100, "veg": 200}

# 七项权重。cost 占 0.15，是拿 kcal/protein 各让 2-3 个点换来的 ——
# 不给价格留权重，营养分就会把贵价食材顶上来。
WEIGHT = {"kcal": .18, "protein": .22, "guide": .17, "expiry": .13,
          "time": .08, "diversity": .07, "cost": .15}

BUDGET_DEFAULT = 25.0     # 单餐食材预算（元）。可用 DIET_BUDGET_CNY 覆盖

# 枚举规模控制：32 种蛋白 × 47 种蔬菜 × 15 种主食 = 54 万组合，跑不动。
# 先按营养挑出候选，再枚举 —— 注意是「按营养」而不是「按性价比」，
# 否则贵价食材会在候选阶段就被剔掉，价格维度就形同虚设了。
SHORTLIST = {"蛋白": 10, "蔬菜": 14, "主食": 6}

# 限量食材：内脏的维生素 A 和胆固醇都很高，老人一周吃 1-2 次合适，
# 天天吃不行。不给它一个惩罚项，它会靠「便宜 + 高蛋白 + 高铁」稳居第一。
OCCASIONAL = {"猪肝": "内脏，维生素A与胆固醇偏高，每周 1-2 次为宜"}
OCCASIONAL_PENALTY = 0.80

# 贵价配额
TIER_RANK = {"经济": 0, "常规": 1, "偏高": 2, "昂贵": 3}
TIER_CAP = {"昂贵": 1, "偏高及以上合计": 2}

# 菜名用的烹饪法，按类别定，而不是按具体食材穷举 —— 池子里有 103 种食材，
# 逐个写"黑椒嫩煎"式的名字既写不全也维护不了。
COOK_PROTEIN = {"禽肉类": "香煎", "畜肉类": "小炒", "鱼虾蟹贝类": "清蒸",
                "蛋类": "嫩炒", "干豆类": "家常", "乳类": "凉拌"}
COOK_VEG = {"嫩茎叶花菜类": "蒜蓉", "根菜类": "清炒", "茄果瓜菜类": "清炒",
            "葱蒜类": "清炒", "薯芋类": "清炒", "鲜豆类": "干煸",
            "水生蔬菜类": "清炒", "菌类": "清炒", "藻类": "凉拌",
            "野生蔬菜类": "蒜蓉"}

# 展示名统一在 foods.DISPLAY 维护，文案层也要用
def disp(key):
    return foods.disp(key)


def _cat_role(key):
    return foods.FOODS[key]["role"]


def _sub(key):
    """类别细分，如「禽肉类及其制品-鸡」→「禽肉类」。
    必须去掉「及其制品」——成分表的分类名带这个后缀，不去就跟菜谱表对不上，
    结果所有肉都落到默认的「家常」上，菜名千篇一律。
    """
    return foods.FOODS[key]["category"].split("-")[0].replace("及其制品", "")


def _leaf(key):
    """类别叶子，如「蔬菜类及其制品-嫩茎叶花菜类」→「嫩茎叶花菜类」。"""
    return foods.FOODS[key]["category"].split("-")[-1]


def load_inventory(path, today=None):
    """按表头名解析，不依赖列顺序；单行坏掉不阻断整批。"""
    today = today or dt.date.today()
    rows, bad = [], []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for i, r in enumerate(csv.DictReader(f), start=2):
            name = (r.get("食材名称") or "").strip()
            if not name:
                continue                      # 空行不算错
            key = NAME2KEY.get(name)
            if not key:
                near = difflib.get_close_matches(name, list(NAME2KEY),
                                                 n=2, cutoff=0.6)
                hint = f"，可用相近的：{'／'.join(near)}" if near else "，请查 --list-foods"
                bad.append((i, f"未知食材「{name}」{hint}"))
                continue
            try:
                # 容忍把单位写进数量列（"300g" / "1颗"），用户多半凭记忆填，
                # 不该因为这点格式问题丢一行。
                qty_raw = str(r.get("数量") or "").strip()
                m = _QTY_RE.match(qty_raw)
                if m:
                    qty, unit_inline = float(m.group(1)), m.group(2).strip()
                else:
                    qty, unit_inline = float(qty_raw), ""
                unit = unit_inline or (r.get("单位") or "g").strip()
                edible, purchased = foods.grams(key, qty, unit)
                exp = dt.date.fromisoformat((r.get("保质期至") or "").strip())
                rows.append({
                    "key": key, "name": name, "cat": (r.get("类别") or "").strip(),
                    "purchased_g": purchased, "edible_g": edible,
                    "expire": exp, "days_left": (exp - today).days,
                    "place": (r.get("存放位置") or "").strip(),
                    "note": (r.get("备注") or "").strip(),
                    "tier": foods.tier(key), "price_kg": foods.price_kg(key),
                })
            except Exception as e:
                bad.append((i, f"{name}: {type(e).__name__} {e}"))
    return rows, bad


def _filter(inv, allergens):
    """过敏原命中即淘汰，任何理由不能覆盖；过期淘汰（冷冻除外）。"""
    keep, drop = [], []
    for it in inv:
        if it["key"] in allergens or it["name"] in allergens:
            drop.append((it["name"], "过敏原"))
        elif it["days_left"] < 0 and "冷冻" not in it["place"]:
            drop.append((it["name"], f"已过期 {abs(it['days_left'])} 天"))
        else:
            keep.append(it)
    return keep, drop


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


def _minutes(protein_key, n_veg):
    """估时：清蒸/凉拌快，红烧/炖慢，按类别给基线再加蔬菜数。"""
    base = {"鱼虾蟹贝": 12, "蛋类": 6, "干豆类": 10,
            "禽肉类": 11, "畜肉类": 10}.get(_sub(protein_key), 10)
    return base + 3 * n_veg


def _portion(key):
    """单个食材在一顿里的可食部克数。干豆、蛋、豆腐不能按 150g 一刀切。"""
    it = foods.FOODS[key]
    r = it["role"]
    if it["dry"]:
        return 40.0
    if _sub(key) == "蛋类":
        return 100.0
    # 豆干是压实的，150g 相当于一大盘；100g 更接近实际
    if "豆腐干" in it["name"]:
        return 100.0
    if _sub(key) == "干豆类":
        return 150.0
    if r == "蔬菜" and _leaf(key) in ("菌类", "藻类"):
        return 120.0
    return float(PORTION.get(r, 100))


def _shortlist(keys, by_key, k):
    """按营养单位价值挑候选，保留「临期」项（临期优先是产品要求，不能被截断）。"""
    def value(key):
        it = foods.FOODS[key]
        if it["role"] == "蛋白":
            return it["protein"]
        if it["role"] == "蔬菜":
            return it["fiber"] * 3 + (2.0 if it["dark"] else 0.5) - it["kcal"] / 100
        return 1.0 - it["kcal"] / 1000.0

    must = [x for x in keys if by_key[x]["days_left"] <= 2]
    rest = sorted([x for x in keys if x not in must], key=value, reverse=True)
    return must + rest[:max(0, k - len(must))]


def plan(inv, exclude=None, allergens=None, topk=3, budget=None):
    exclude = exclude or []
    allergens = allergens or set()
    budget = float(budget or os.environ.get("DIET_BUDGET_CNY", BUDGET_DEFAULT))
    keep, dropped = _filter(inv, allergens)
    by_key = {}
    for it in keep:
        by_key.setdefault(it["key"], it)

    proteins = [k for k in by_key if _cat_role(k) == "蛋白"]
    veggies = [k for k in by_key if _cat_role(k) == "蔬菜"]
    staples = [k for k in by_key if _cat_role(k) == "主食"]
    if not proteins or not veggies:
        return {"error": "库存不足以组餐（需要至少 1 种蛋白 + 1 种蔬菜）",
                "dropped": dropped, "budget": budget}

    proteins = _shortlist(proteins, by_key, SHORTLIST["蛋白"])
    veggies = _shortlist(veggies, by_key, SHORTLIST["蔬菜"])
    staples = _shortlist(staples, by_key, SHORTLIST["主食"])

    combos, over_budget, over_tier = [], 0, 0
    for p in proteins:
        for i, v1 in enumerate(veggies):
            for v2 in [None] + veggies[i + 1:]:
                for s in staples or [None]:
                    c = {"protein": p, "vegs": [v for v in (v1, v2) if v],
                         "staple": s}
                    sc = _score(c, by_key, exclude, budget)
                    if sc.get("_over_budget"):
                        over_budget += 1
                        continue
                    if sc.get("_over_tier"):
                        over_tier += 1
                        continue
                    combos.append(sc)

    if not combos:
        return {"error": f"预算 ¥{budget:.0f} 内组不出餐，或库存不足",
                "dropped": dropped, "budget": budget,
                "hint": "调 DIET_BUDGET_CNY，或补充平价食材（鸡蛋/豆腐/白菜）"}

    combos.sort(key=lambda c: -c["score"])
    # 去重：主食不同但主料和蔬菜相同的，算同一道菜。不去重的话 Top3
    # 会变成「同一盘菜配米饭/配面条/配糙米」，用户看不出差别。
    seen, uniq = set(), []
    for c in combos:
        sig = (c["protein_key"], tuple(c["veg_keys"]))
        if sig in seen:
            continue
        seen.add(sig)
        uniq.append(c)
        if len(uniq) >= topk:
            break
    return {"best": uniq[0], "top3": uniq, "dropped": dropped,
            "n_combos": len(combos), "inventory_used": len(keep),
            "budget": budget, "over_budget": over_budget,
            "over_tier": over_tier,
            "cheapest": min(c["cost"] for c in combos),
            "dearest": max(c["cost"] for c in combos)}


def _dish_name(p, vegs):
    head = f"{COOK_PROTEIN.get(_sub(p), '家常')}{disp(p)}"
    v = vegs[0]
    tail = f"{COOK_VEG.get(_leaf(v), '清炒')}{disp(v)}"
    if len(vegs) > 1:                      # 两个蔬菜时补上第二个，否则 Top3 会重名
        tail += f"＋{disp(vegs[1])}"
    return f"{head}配{tail}"


def _score(combo, by_key, exclude, budget):
    items, used = [], []
    p = combo["protein"]
    pg = min(_portion(p), by_key[p]["edible_g"])
    items.append((p, pg)); used.append(p)

    for idx, v in enumerate(combo["vegs"]):
        want = PORTION["蔬菜"] if idx == 0 else 100
        g = min(float(want), by_key[v]["edible_g"])
        items.append((v, g)); used.append(v)

    s = combo["staple"]
    if s:
        items.append((s, min(_portion(s), by_key[s]["edible_g"])))
        used.append(s)
    items.append(("oil", float(PORTION["调味"])))
    used.append("oil")
    # 大蒜只在库存里有且不是主蔬菜时加
    for gk in by_key:
        if foods.FOODS[gk]["role"] == "调味" and gk != "oil" and gk not in used:
            items.append((gk, min(6.0, by_key[gk]["edible_g"])))
            used.append(gk)
            break

    n = foods.total(items)
    cost = foods.basket_cost(items)
    minutes = _minutes(p, len(combo["vegs"]))

    # ---- 报价与配额：超预算直接淘汰，不参与排序 ----
    if cost > budget:
        return {"_over_budget": True, "cost": round(cost, 2)}
    tiers = [TIER_RANK.get(foods.tier(k), 1) for k in used]
    if sum(1 for t in tiers if t >= 3) > TIER_CAP["昂贵"]:
        return {"_over_tier": True, "cost": round(cost, 2)}
    if sum(1 for t in tiers if t >= 2) > TIER_CAP["偏高及以上合计"]:
        return {"_over_tier": True, "cost": round(cost, 2)}

    f_kcal = 1 - min(1.0, abs(n["kcal"] - TARGET["kcal"]) / TARGET["kcal"])
    f_prot = min(1.0, n["protein"] / TARGET["protein"])
    f_guide = (0.5 * min(1.0, n["dark_veg_g"] / TARGET["dark_veg"]) +
               0.5 * min(1.0, n["veg_g"] / TARGET["veg"]))
    f_exp = sum(_urgency(by_key[k]["days_left"]) for k in used
                if k in by_key) / max(1, sum(1 for k in used if k in by_key))
    f_time = 1 - min(1.0, (minutes - 12) / 25.0)
    f_div = 0.3 if any(k in exclude for k in used) else 0.85
    # 成本分：预算 ¥5 以内给满，接近预算给 0。用固定下限而不是比例，
    # 是因为 ¥3 和 ¥6 的差别对用户几乎无感，没必要在低价段继续细分。
    f_cost = 1 - min(1.0, max(0.0, (cost - 5.0) / max(1.0, budget - 5.0)))

    parts = {"kcal": f_kcal, "protein": f_prot, "guide": f_guide,
             "expiry": f_exp, "time": f_time, "diversity": f_div,
             "cost": f_cost}
    score = sum(parts[k] * WEIGHT[k] for k in WEIGHT)
    flags = [msg for k, msg in OCCASIONAL.items()
             if any(foods.FOODS[x]["name"].startswith(k) for x in used)]
    if flags:
        score *= OCCASIONAL_PENALTY

    return {
        "name": _dish_name(p, combo["vegs"]),
        "short": f"{COOK_PROTEIN.get(_sub(p), '家常')}{disp(p)}",
        "items": [{"key": k, "name": foods.label(k), "disp": disp(k),
                   "edible_g": g, "cooked_g": foods.raw_to_cooked(k, g),
                   "role": foods.FOODS[k]["role"],
                   "tier": foods.tier(k), "tier_sym": foods.tier_sym(k),
                   "cost": round(foods.cost(k, g), 2),
                   "price_kg": foods.price_kg(k)} for k, g in items],
        "facts": {"kcal": int(round(n["kcal"])),
                  "protein": round(n["protein"], 1),
                  "fat": round(n["fat"], 1), "carb": round(n["carb"], 1),
                  "fiber": round(n["fiber"], 1), "veg_g": int(n["veg_g"]),
                  "dark_veg_g": int(n["dark_veg_g"])},
        "minutes": minutes, "score": round(score, 4), "parts": parts,
        "cost": round(cost, 2), "budget": budget,
        "cost_ratio": round(cost / budget, 2),
        "tier_mix": {t: sum(1 for x in tiers if TIER_RANK.get(t) == x)
                     for t in TIER_RANK},
        "flags": flags,
        "expensive_items": [disp(k) for k in used
                            if TIER_RANK.get(foods.tier(k), 1) >= 2],
        "expiring_used": [k for k in used
                          if k in by_key and by_key[k]["days_left"] <= 2],
        "protein_key": p, "veg_keys": combo["vegs"],
    }


def why_not(top3, name_keyword):
    """给用户「为什么没选某道菜」的一句话解释。"""
    for c in top3:
        if name_keyword in c["name"]:
            return (f"它排在第 {top3.index(c) + 1} 位，分差主要在" +
                    "、".join(f"{k} {v:.2f}" for k, v in
                              sorted(c["parts"].items(), key=lambda x: x[1])[:2]))
    return f"库存里没有能组成「{name_keyword}」的食材"


def premium_alternatives(inv, chosen, budget=None, limit=3):
    """列出「营养更好但因为贵没被选」的食材，给用户一个明确的升级入口。

    不加这个，用户会觉得价格逻辑是黑箱；加上之后，「没推荐三文鱼」这件事
    有了解释，也能一键升级。
    """
    budget = float(budget or os.environ.get("DIET_BUDGET_CNY", BUDGET_DEFAULT))
    used = {i["key"] for i in chosen["items"]}
    out = []
    for it in inv:
        k = it["key"]
        if k in used or foods.FOODS[k]["role"] != "蛋白":
            continue
        if TIER_RANK.get(foods.tier(k), 1) < 2:
            continue
        g = min(_portion(k), it["edible_g"])
        add = foods.cost(k, g) - foods.cost(chosen["protein_key"],
                                            next(i["edible_g"] for i in chosen["items"]
                                                 if i["key"] == chosen["protein_key"]))
        out.append({"key": k, "name": foods.label(k), "tier": foods.tier(k),
                    "tier_sym": foods.tier_sym(k),
                    "protein": round(foods.FOODS[k]["protein"] * g / 100, 1),
                    "extra_cost": round(add, 2),
                    "new_total": round(chosen["cost"] + add, 2),
                    "over_budget": chosen["cost"] + add > budget})
    out.sort(key=lambda x: -x["protein"])
    return out[:limit]
