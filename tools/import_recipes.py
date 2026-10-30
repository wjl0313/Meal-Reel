#!/usr/bin/env python3
"""把 EveryDay_Food 的菜谱导成 daily-diet 能吃的结构化数据。

源：E:/myProject/EveryDay_Food/public/data/meals.json（1093 道，站点构建时导出的）
产物：data/recipes.json

为什么不在运行时直接读源站 JSON：
  1. 源数据里的食材是给人看的自由文本（"盐 适量"、"泡发木耳"），
     解析要跑词法层，7500 行每次都跑一遍没必要；
  2. 解析结果要人工核查（本脚本的 --report 就是干这个的），
     核查过的中间产物比每次重新臆测可靠；
  3. 源库会更新，重新导入要能 diff 出「新增/消失/解析退化」，
     这需要一个落盘的快照。

    python tools/import_recipes.py --src E:/myProject/EveryDay_Food/public/data/meals.json
    python tools/import_recipes.py --report        # 只看解析质量，不写文件

产物字段说明见下方 RECIPE_COLS 与 _one() 的组装过程。
"""
import argparse
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from core import lexicon  # noqa: E402

SRC_DEFAULT = "E:/myProject/EveryDay_Food/public/data/meals.json"
OUT = os.path.join(ROOT, "data", "recipes.json")

# 做法 → 基准分钟。菜谱数据没有时长字段，只给了 steps，
# 所以用「做法决定基线 + 步数决定增量」估：一道 5 步的红烧肉该按 40 分钟算，
# 不是按 5 步算 20 分钟。这个数只用于「这道菜今晚来不来得及」这一项打分。
METHOD_MIN = {
    "凉拌": 8, "涮": 10, "炒": 12, "煎": 15, "煮": 15, "腌": 15,
    "蒸": 20, "烩": 20, "火锅": 20, "炸": 25, "干锅": 25, "焖": 35,
    "红烧": 40, "烤": 40, "烘焙": 45, "熬粥": 45, "炖": 50,
    "卤": 60, "熬": 60, "煲汤": 70,
}


def _minutes(rec):
    ms = [METHOD_MIN[m] for m in (rec.get("methods") or []) if m in METHOD_MIN]
    base = min(ms) if ms else 20
    # 步骤多于 3 步时，每多一步加 2 分钟（洗切、腌制、静置这些隐性耗时）
    extra = 2 * max(0, len(rec.get("steps") or []) - 3)
    return int(min(150, base + extra))


def _one(rec):
    mains, seas = [], []
    for line in rec.get("ingredients") or []:
        for p in lexicon.parse_line(line):
            if p["seasoning"]:
                seas.append(p["name"])
                continue
            key = lexicon.resolve_key(p["name"]) if p["role"] else None
            mains.append({
                "raw": line,
                "name": p["name"],
                "norm": lexicon.normalize(p["name"]),
                "grams": p["grams"],
                "role": p["role"],
                "key": key,
            })
    roles = collections.Counter(m["role"] for m in mains if m["role"])
    return {
        "id": rec["id"],
        "name": rec["name"],
        "category": rec["category"],
        "cuisine": rec.get("cuisine") or "generic",
        "emoji": rec.get("emoji") or "🍽",
        "mealType": rec.get("mealType") or [],
        "methods": rec.get("methods") or [],
        "flavors": rec.get("flavors") or [],
        "desc": rec.get("desc") or "",
        "steps": rec.get("steps") or [],
        "kcal": rec.get("kcal") or 0,
        "nutrition": rec.get("nutrition") or {},
        "per100g": rec.get("per100g") or {},
        "serving": rec.get("servingSize") or {"amount": 0, "unit": "g"},
        "minutes": _minutes(rec),
        "mains": mains,
        "seasonings": seas,
        "role_mix": dict(roles),
        # 平替引擎的两条前置条件：要有主料，且主料角色能被识别。
        # 全不满足的菜谱进库也没用，直接在导入期标出来。
        "usable": bool(mains) and any(m["role"] for m in mains),
    }


def report(recipes, src_count):
    print(f"源 {src_count} 道 → 入库 {len(recipes)} 道\n")

    n_main = sum(len(r["mains"]) for r in recipes)
    n_role = sum(1 for r in recipes for m in r["mains"] if m["role"])
    n_key = sum(1 for r in recipes for m in r["mains"] if m["key"])
    print("=== 食材解析 ===")
    print(f"  主料条目 {n_main}")
    print(f"  角色可判 {n_role}（{n_role / max(1, n_main) * 100:.1f}%）")
    print(f"  对到成分表 {n_key}（{n_key / max(1, n_main) * 100:.1f}%）"
          f"  ← 对不上的不报错，平替走角色匹配")
    print(f"  调味条目 {sum(len(r['seasonings']) for r in recipes)}（不参与库存匹配）")

    print("\n=== 角色分布 ===")
    c = collections.Counter(m["role"] or "未识别" for r in recipes for m in r["mains"])
    for k, v in c.most_common():
        print(f"  {k:<6}{v}")

    print("\n=== 每道菜的主料角色组合 ===")
    combo = collections.Counter()
    for r in recipes:
        rs = [k for k in ("蛋白", "蔬菜", "主食") if r["role_mix"].get(k)]
        combo["+".join(rs) or "无蛋白无蔬菜"] += 1
    for k, v in combo.most_common():
        print(f"  {k:<16}{v}")

    print("\n=== 分类 × 可用性 ===")
    for cat in sorted({r["category"] for r in recipes}):
        rs = [r for r in recipes if r["category"] == cat]
        ok = sum(1 for r in rs if r["usable"])
        print(f"  {cat:<12}{len(rs):>4} 道，可用 {ok}")

    unknowable = collections.Counter(
        m["name"] for r in recipes for m in r["mains"] if not m["role"])
    if unknowable:
        print("\n=== 仍无法判角色的主料（前 20）===")
        for k, v in unknowable.most_common(20):
            print(f"  {v:>3}  {k}")

    print("\n=== 时长分布（估）===")
    buckets = collections.Counter()
    for r in recipes:
        m = r["minutes"]
        buckets["≤15" if m <= 15 else "16-25" if m <= 25
                else "26-40" if m <= 40 else "41-60" if m <= 60 else ">60"] += 1
    for k in ("≤15", "16-25", "26-40", "41-60", ">60"):
        print(f"  {k:<8}{buckets.get(k, 0)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=SRC_DEFAULT)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--report", action="store_true", help="只打报告不写文件")
    args = ap.parse_args()

    if not os.path.exists(args.src):
        print(f"找不到菜谱源：{args.src}")
        print("改为从源项目导出：cd E:/myProject/EveryDay_Food && npm run export:data")
        return 2

    with open(args.src, encoding="utf-8") as f:
        raw = json.load(f)

    recipes = [_one(r) for r in raw]
    report(recipes, len(raw))

    if args.report:
        print("\n（--report 模式，未写文件）")
        return 0

    payload = {
        "source": os.path.abspath(args.src),
        "count": len(recipes),
        "recipes": recipes,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print(f"\n已写入 {args.out}  {os.path.getsize(args.out) / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
