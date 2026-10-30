#!/usr/bin/env python3
"""生成「食材清单」上传模板 —— 从食材库派生，带价格档与保质期。

为什么不直接把 103 条全写进清单：这份清单是**库存**，不是食材字典。
用户的冰箱里不会有 47 种蔬菜，把字典倒进去只会让推荐器在纸面上准确、
在现实中荒谬。所以这里配的是一户人家真实会备的量：约 30 条，覆盖
蛋白/蔬菜/主食/调味四类，能撑起一周不重样的晚餐。

价格档写进「备注」列 —— 模板没有价格列，而价格又是推荐的关键输入。
不写的话用户会以为推荐器在乱推三文鱼。

用法：
    python tools/make_inventory.py                     # 用今天日期
    python tools/make_inventory.py --date 2026-10-30
    python tools/make_inventory.py --user U002 --out data/01_食材清单_U002.csv
"""
import argparse
import csv
import datetime as dt
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import foods  # noqa: E402

# (清单里写的名字, 数量, 单位, 存放位置, 可放天数, 备注)
# 名字用口语，正是用户会手写的那种；能不能识别由 core/foods 的口语映射决定，
# 认不出来就说明映射表缺词，是必须暴露的问题，不要在这里绕开。
STOCK = [
    # ---- 蔬菜：深色叶菜是主力，根茎类负责耐放 ----
    ("大白菜", 1, "颗", "冷藏", 14, "整颗，吃一半"),
    ("包菜", 1, "颗", "冷藏", 14, ""),
    ("菠菜", 400, "g", "冷藏", 2, "先吃，快蔫了"),
    ("小白菜", 400, "g", "冷藏", 4, ""),
    ("西兰花", 1, "颗", "冷藏", 6, ""),
    ("番茄", 500, "g", "冷藏", 7, ""),
    ("胡萝卜", 500, "g", "冷藏", 20, ""),
    ("白萝卜", 1, "根", "冷藏", 20, ""),
    ("香菇", 200, "g", "冷藏", 5, ""),
    ("木耳", 200, "g", "冷藏", 3, "已泡发"),
    ("洋葱", 500, "g", "常温", 25, ""),
    ("土豆", 1, "kg", "常温", 30, ""),
    ("冬瓜", 800, "g", "冷藏", 10, ""),
    # ---- 蛋白 ----
    ("鸡蛋", 10, "个", "冷藏", 20, ""),
    ("鸡胸", 400, "g", "冷冻", 60, "分两顿"),
    ("豆腐", 1, "块", "冷藏", 4, ""),
    ("豆腐干", 200, "g", "冷藏", 7, ""),
    ("草鱼", 1, "条", "冷冻", 30, "已处理"),
    ("对虾", 300, "g", "冷冻", 90, "留到周末"),
    ("猪肝", 200, "g", "冷藏", 1, "明天必须吃掉"),
    ("黄豆", 500, "g", "常温", 180, ""),
    # ---- 主食 ----
    ("大米", 2, "kg", "常温", 180, ""),
    ("糙米", 1, "kg", "常温", 180, ""),
    ("挂面", 500, "g", "常温", 180, ""),
    ("玉米", 2, "根", "冷藏", 5, ""),
    # ---- 调味 ----
    ("大蒜", 1, "头", "常温", 30, ""),
    ("姜", 100, "g", "常温", 20, ""),
    ("植物油", 1, "瓶", "常温", 365, "按 8g/顿 计"),
    # ---- 坚果/乳品 ----
    ("花生", 200, "g", "常温", 120, ""),
    ("牛奶", 1, "L", "冷藏", 7, ""),
]

HEADER = ["食材名称", "数量", "单位", "类别", "购买日期", "保质期至",
          "存放位置", "是否深色蔬菜", "备注"]


def build(today):
    rows, warn = [], []
    for name, qty, unit, place, days, note in STOCK:
        key = foods.NAME2KEY.get(name)
        if not key:
            warn.append(f"「{name}」在食材库里找不到，已跳过（口语映射需补词）")
            continue
        it = foods.FOODS[key]
        tier = foods.tier(key)
        price = foods.price_kg(key)
        tag = f"{tier} ¥{price:.0f}/kg"
        rows.append({
            "食材名称": name, "数量": qty, "单位": unit,
            # 写角色而不是完整分类（「蔬菜类及其制品-嫩茎叶花菜类」在 Excel 里
            # 挤满一列，而这一列本来只是备注性质）
            "类别": it["role"],
            "购买日期": today.isoformat(),
            "保质期至": (today + dt.timedelta(days=days)).isoformat(),
            "存放位置": place,
            "是否深色蔬菜": "是" if it["dark"] else "",
            "备注": f"{tag}；{note}" if note else tag,
            "_key": key, "_tier": tier, "_price": price,
        })
    return rows, warn


def write(rows, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADER, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return path


def summary(rows):
    import collections
    by_tier = collections.Counter(r["_tier"] for r in rows)
    print(f"共 {len(rows)} 条")
    print("价格档分布：" + "，".join(f"{k} {v}" for k, v in
                                by_tier.most_common()))
    total = 0.0
    for r in rows:
        k = r["_key"]
        if foods.FOODS[k]["role"] == "调味":
            continue
        total += foods.cost(k, r["数量"] * 1.0) if r["单位"] in ("g",) else 0
    print("（此列按克计价的条目小计，仅作量级参考：¥%.0f）" % total)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="购买日期，默认今天")
    ap.add_argument("--user", default="U001")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    today = (dt.date.fromisoformat(a.date) if a.date else dt.date.today())
    out = a.out or os.path.join(ROOT, "data", f"01_食材清单_{a.user}.csv")
    rows, warn = build(today)
    p = write(rows, out)
    print(f"已写 {p}")
    for w in warn:
        print("  !", w)
    summary(rows)
