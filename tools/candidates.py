#!/usr/bin/env python3
"""看菜谱池子有多大 —— 决定「多久会重样」。

    python3 tools/candidates.py                 # 当前 config.json 的分类
    python3 tools/candidates.py fat-loss        # 指定分类
    python3 tools/candidates.py fat-loss,home   # 多分类合并

不生成任何图片、不调用模型，纯本地算，随时可跑。
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import history, planner, recipes as rm, settings   # noqa: E402

SLOTS = ["breakfast", "lunch", "dinner"]
LABEL = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐"}


def pool(cfg, inv, slot):
    """这个餐次下、当前条件下真正做得出来（未超预算/未超档）的菜谱。"""
    c = dict(cfg)
    c["slot"] = slot
    c["meal_types"] = [slot]
    cands, _ = rm.filter_recipes(rm.load(c.get("recipes_path")), c)
    by_key, _ = rm._inventory(inv)
    out = []
    for r in cands:
        m = rm.evaluate(r, inv, c, by_key)
        if m and not m.get("_over_budget") and not m.get("_over_tier"):
            out.append(r)
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    cfg = settings.load()
    if args:
        cfg["categories"] = [x.strip() for x in args[0].split(",") if x.strip()]

    inv, _ = planner.load_inventory(settings.inventory_path(cfg), today=dt.date.today())
    if not inv:
        print("库存为空，先填 data/01_食材清单_<user>.csv")
        return 2

    cats = cfg.get("categories") or []
    cool = int(cfg.get("repeat_cooldown_days") or 0)
    used = history.age_days()
    print(f"分类 {cats or '不限'}｜预算 ¥{cfg.get('budget')}｜"
          f"最长 {cfg.get('max_minutes')} 分｜冷却 {cool} 天｜选菜 {cfg.get('pick')}")
    print(f"历史已记录 {len(used)} 道菜\n")

    for slot in SLOTS:
        rs = pool(cfg, inv, slot)
        n = len(rs)
        # 冷却期内还"新鲜"的有多少，直接决定这次会不会撞到重复
        fresh = [r for r in rs if used.get(r["id"], 10 ** 6) >= cool]
        cycle = f"{n} 天一轮" if n else "—"
        warn = "  ← 池子太小，很快就会重样" if 0 < n <= 7 else ""
        print(f"  {LABEL[slot]}  可用 {n:>3} 道｜未冷却 {len(fresh):>3} 道｜"
              f"{cycle}{warn}")

    print("\n想扩大池子：后台多勾一个菜谱类型，或调高最长用时 / 预算。")
    print("池子 < 天数时，冷却期一满就会开始循环，这时是靠「最久没吃」优先，不是随机撞车。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
