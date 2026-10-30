#!/usr/bin/env python3
"""菜谱使用历史 —— 让每次出片都不是同一道菜。

为什么单独存一个文件，而不是塞进 config.json：
    config.json 是「参数」，人会在后台页面里改、会被整体覆盖；
    历史是「运行时数据」，只增不改。两者混在一起，后台点一次保存就可能把
    历史冲掉，于是第二天又开始重样 —— 这种 bug 很难复现，干脆物理隔离。

记录格式（data/recipe_history.json）：
    {"version": 1, "records": [
        {"id": "lxj-home-2", "name": "蛋饼·挂面版",
         "date": "2026-10-30", "slot": "breakfast", "run_id": "2026-10-30_breakfast"}
    ]}

只保留最近 MAX_RECORDS 条，避免文件无限长。
"""
import datetime as dt
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "data", "recipe_history.json")

MAX_RECORDS = 3000


def load(path=None):
    p = path or PATH
    if not os.path.exists(p):
        return {"version": 1, "records": []}
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, json.JSONDecodeError):
        # 历史文件坏了不能让出片起不来，退回空历史（顶多重样一次）
        return {"version": 1, "records": []}
    if isinstance(d, list):
        return {"version": 1, "records": d}
    if not isinstance(d, dict):
        return {"version": 1, "records": []}
    d.setdefault("version", 1)
    d.setdefault("records", [])
    return d


def save(db, path=None):
    p = path or PATH
    recs = db.get("records") or []
    if len(recs) > MAX_RECORDS:
        db["records"] = recs[-MAX_RECORDS:]
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def add(recipe_id, name=None, date=None, slot=None, run_id=None, path=None):
    """记一条「今天用过这道菜」。同名同日的重复记录会被覆盖，不会越攒越多。"""
    if not recipe_id:
        return None
    date = date or dt.date.today().isoformat()
    if hasattr(date, "isoformat"):
        date = date.isoformat()
    db = load(path)
    recs = db["records"]
    key = (str(recipe_id), date)
    recs = [r for r in recs if (str(r.get("id")), str(r.get("date"))) != key]
    recs.append({"id": str(recipe_id), "name": name or "",
                 "date": date, "slot": slot or "", "run_id": run_id or ""})
    db["records"] = recs
    save(db, path)
    return db


def _as_date(x):
    try:
        return dt.date.fromisoformat(str(x))
    except (ValueError, TypeError):
        return None


def last_used(path=None):
    """recipe_id → 最近一次使用的日期。"""
    out = {}
    for r in load(path)["records"]:
        rid, d = str(r.get("id") or ""), _as_date(r.get("date"))
        if not rid or not d:
            continue
        if rid not in out or d > out[rid]:
            out[rid] = d
    return out


def age_days(path=None, today=None):
    """recipe_id → 距上次吃过的天数。越小 = 越近。没有记录的菜不在此表里。"""
    today = today or dt.date.today()
    if hasattr(today, "date") and not isinstance(today, dt.date):
        today = today.date()
    return {rid: (today - d).days for rid, d in last_used(path).items()}


def exclude_ids(cooldown_days, today=None, path=None):
    """返回冷却期内要排除的菜谱 id 集合。

    cooldown_days <= 0 表示不限制（只在「三餐之间不重复」上生效）。
    """
    if not cooldown_days or int(cooldown_days) <= 0:
        return set()
    ages = age_days(path, today)
    n = int(cooldown_days)
    return {rid for rid, a in ages.items() if a < n}


def summary(path=None):
    """给日志和后台用的一行统计。"""
    recs = load(path)["records"]
    return {"total": len(recs), "distinct": len({str(r.get("id")) for r in recs}),
            "last": recs[-1] if recs else None}
