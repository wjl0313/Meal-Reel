#!/usr/bin/env python3
"""日常饮食 · 一键出片

    python run.py --user U001 --date 2026-10-30 --slot dinner

流程：库存 → 菜单 → 文案 → 分镜 → 配图 → 字幕 → 配音 → 合成
图片走真实 Seedream（9:16 原生），配音当前走 mock（语音 Key 待换）。
每一步的中间产物都落在 runs/<run_id>/ 下，可单步重跑。
"""
import argparse
import datetime as dt
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clients import ark_image, speech_doubao, speech_mock   # noqa: E402
from core import (history, narrator, planner, prompts,      # noqa: E402
                  recipes as recipe_mod,
                  render, settings, storyboard, subs)

ROOT = os.path.dirname(os.path.abspath(__file__))

MEAL_WORD = {"breakfast": "早上七点", "lunch": "中午十二点", "dinner": "晚上七点"}


def load_env(path=None):
    path = path or os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def log(msg):
    print(msg, flush=True)


def sub_style_from_env(cfg=None):
    """字幕/文字样式的配置入口。默认值面向老年观众（见 core/subs.py）。

    core 层不读环境变量，配置一律在这里注入 —— 领域逻辑保持纯函数。
    非法值直接忽略并保留默认，不因为一个配置项打错就中断出片。
    优先级：config.json 的 subs 块 > 环境变量 > 内置默认。
    """
    st = {}
    for key, env in (("main_size", "DIET_SUB_SIZE"),
                     ("title_size", "DIET_SUB_TITLE_SIZE"),
                     ("stat_size", "DIET_SUB_STAT_SIZE"),
                     ("margin_v", "DIET_SUB_MARGIN_V")):
        v = os.environ.get(env, "").strip()
        if v.isdigit():
            st[key] = int(v)
    a = os.environ.get("DIET_SUB_ALPHA", "").strip()
    if a:
        try:
            int(a, 16)
            st["box_alpha"] = a.upper().zfill(2)[-2:]
        except ValueError:
            pass
    for key, val in ((cfg or {}).get("subs") or {}).items():
        if key in ("main_size", "title_size", "stat_size", "margin_v"):
            if isinstance(val, int):
                st[key] = val
        elif key == "wrap":
            try:
                st["wrap"] = float(val)
            except (TypeError, ValueError):
                pass
        else:
            st[key] = val
    # 字体名（移植必须项）。系统里没有这个字体时 libass **不会报错**，
    # 而是静默回退到 Arial，中文全变方块 —— 成片照样输出、退出码 0。
    # 见 core/subs.py 的 DEFAULT_FONT 注释与 tools/doctor.py。
    f = os.environ.get("DIET_SUB_FONT", "").strip()
    if f:
        st["font"] = f
    return st


def font_profile():
    """当前平台建议的字体名，供 doctor 体检用。"""
    return subs.DEFAULT_FONT, subs.FONT_HINTS.get(sys.platform,
                                                  subs.FONT_HINTS["linux"])


def list_foods():
    """打印可上传的食材清单。填表前先看这个，比事后猜「为什么少了两条」省事。"""
    from core import foods as F

    print("可上传的食材（「食材名称」列照这个填，也可以写后面的俗称）\n")
    print(f"  {'名称':<10}{'角色':<5}{'价格档':<8}{'千卡':>5}{'蛋白':>6}{'单件换算':<13}深色")
    print("  " + "-" * 66)
    order = {"蛋白": 0, "蔬菜": 1, "主食": 2, "乳品": 3, "坚果": 4, "调味": 5}
    for k, it in sorted(F.FOODS.items(),
                        key=lambda x: (order.get(x[1]["role"], 9), x[1]["name"])):
        name = F.disp(k)
        w = F.UNIT_WEIGHT.get(F._strip(it["name"])) or F.UNIT_WEIGHT.get(it["name"])
        per = f"1 件 ≈ {w}g" if w else "只按 g/kg"
        print(f"  {name:<10}{it['role']:<5}{it['tier_sym']+' '+it['tier']:<8}"
              f"{it['kcal']:>5.0f}{it['protein']:>6.1f}{per:<13}"
              f"{'是' if it['dark'] else ''}")

    colloquial = {a: k for a, k in F.COLLOQUIAL.items()}
    print(f"\n常见俗称（可直接填）：{'、'.join(list(colloquial)[:24])} …")
    print("\n「数量」「单位」两列：单位可填 g / kg / 斤 / ml，或件（颗/根/个/块/条/头/瓶）。")
    print("写成 “300g” 直接填在数量列也能识别；青菜写「1颗」也行。")
    print("「保质期至」必须是 2026-11-02 这种格式。")
    print("价格档：¥ 经济(≤10元/kg)  ¥¥ 常规(10-25)  ¥¥¥ 偏高(25-60)  ¥¥¥¥ 昂贵(>60)")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default=None)
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--slot", default=None)
    ap.add_argument("--meal-types", default=None,
                    help="菜谱餐次筛选，逗号分隔（breakfast,lunch,dinner）。"
                         "不给则由 --slot 推导；后台多选餐次时由它显式传入")
    ap.add_argument("--inventory", default=None)
    ap.add_argument("--chat", action="store_true", help="用方舟改写文案（需已开通文案模型）")
    ap.add_argument("--tts", default="auto", choices=["auto", "mock", "doubao"],
                    help="auto=能用真语音就用，失败降级占位并明确标注")
    ap.add_argument("--reuse-images", action="store_true", help="只用缓存，不发起新的生图调用")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--budget", type=float, default=None,
                    help="单餐食材预算（元），默认读 DIET_BUDGET_CNY 或 25")
    ap.add_argument("--mode", default=None, choices=["recipe", "combo"],
                    help="recipe=真实菜谱+平替（默认）/ combo=从库存组合出菜")
    ap.add_argument("--recipe-cats", default=None,
                    help="菜谱类型，逗号分隔：fat-loss,muscle-gain,maintain,"
                         "nutrition,home,therapy,bakery,snack")
    ap.add_argument("--exclude-recipes", default=None,
                    help="排除指定菜谱 id，逗号分隔。三餐出片时用它避免早/午/晚重样")
    ap.add_argument("--cooldown", type=int, default=None,
                    help="同一道菜多少天内不再出现（默认读 config.json 的 "
                         "repeat_cooldown_days，0=不限制）")
    ap.add_argument("--pick", default=None, choices=["best", "random"],
                    help="best=永远最高分（可复现）/ random=在优质候选里随机（默认）")
    ap.add_argument("--list-foods", action="store_true",
                    help="列出可上传的食材名、单位与别名")
    args = ap.parse_args()

    load_env()
    if args.list_foods:
        return list_foods()

    cfg = settings.load()
    # 命令行没给就用配置里的；两处都能改，但以命令行优先，方便临时试
    args.user = args.user or cfg.get("user") or "U001"
    args.slot = args.slot or cfg.get("slot") or "dinner"
    mode = args.mode or cfg.get("mode") or "recipe"
    if args.recipe_cats:
        cfg["categories"] = [x.strip() for x in args.recipe_cats.split(",") if x.strip()]
    if args.exclude_recipes:
        cfg["exclude_recipes"] = [x.strip() for x in args.exclude_recipes.split(",")
                                  if x.strip()]
    if args.slot:
        cfg["slot"] = args.slot
    # 菜谱的餐次筛选。三档优先级，从具体到宽泛：
    #   1. --meal-types 显式给了 → 照用（后台多选餐次走这一条）
    #   2. 否则只挂一个餐次 → 它就是要出的那一餐，筛选跟着它走
    #   3. 配置里明确选了多个餐次 → 尊重配置，不在单一 --slot 上收窄
    # 原来的写法是 `if not args.mode: cfg["meal_types"]=[args.slot]`，只在没写
    # --mode 时才同步。而 `--mode recipe` 恰恰是文档和后台最常见的写法，
    # 于是 meal_types 恒等于 config.json 里的 ["dinner"]：`--slot breakfast`
    # 筛出的是晚餐菜，文案却说「早上七点」，同一份成片自相矛盾。
    if args.meal_types:
        cfg["meal_types"] = [x.strip() for x in args.meal_types.split(",")
                             if x.strip()]
    elif len(cfg.get("meal_types") or []) <= 1:
        cfg["meal_types"] = [args.slot]

    day = dt.date.fromisoformat(args.date)
    run_id = args.run_id or f"{args.date}_{args.slot}"
    out = os.path.join(ROOT, "runs", run_id)
    img_dir = os.path.join(out, "img")
    for d in (out, img_dir):
        os.makedirs(d, exist_ok=True)

    t_all = time.time()

    # ---------------------------------------------------------- 1 库存 → 菜单
    inv_path = args.inventory or settings.inventory_path(cfg)
    inv, bad = planner.load_inventory(inv_path, today=day)
    log(f"[1/8] 库存 {len(inv)} 条（坏行 {len(bad)}）")
    for i, why in bad:
        log(f"      行{i} 跳过：{why}")

    budget = args.budget or float(cfg.get("budget") or 25)
    cfg["budget"] = budget
    if mode == "recipe":
        # 跨天不重样：把「冷却期内吃过的菜」排掉，再在头部优质候选里随机挑一道。
        # 冷却期外的菜会重新回到池子，所以池子大小决定了多久轮回一次：
        # 池子 5 道 + 冷却 30 天 = 5 天一轮；池子 438 道 = 一年多不重样。
        cool = (args.cooldown if args.cooldown is not None
                else int(cfg.get("repeat_cooldown_days") or 0))
        hist_excl = history.exclude_ids(cool, today=day)
        cli_excl = {str(x) for x in (cfg.get("exclude_recipes") or []) if x}
        merged = cli_excl | hist_excl
        if merged:
            cfg["exclude_recipes"] = sorted(merged)
        # 值越小 = 越近吃过；没吃过的菜不在表里，排序时按 -1 排最前（池子耗尽时用它）
        cfg["lru"] = history.age_days(today=day)
        cfg["pick"] = args.pick or str(cfg.get("pick") or "random")
        hs = history.summary()
        log(f"      不重样：历史 {hs['distinct']} 道｜冷却 {cool} 天｜"
            f"排除 {len(merged)} 道（历史 {len(hist_excl)} + 本轮互斥 {len(cli_excl)}）"
            f"｜挑选 {cfg['pick']}")
        plan = recipe_mod.plan(inv, cfg)
        if "error" in plan:
            log("菜谱模式：" + plan["error"])
            for name, n in plan.get("funnel") or []:
                log(f"      筛剩 {name} {n}")
            if plan.get("hint"):
                log("      " + plan["hint"])
            return 2
        # 漏斗：每一关剩多少，出问题时能一眼看出是哪条筛死了
        funnel = " → ".join(f"{n} {c}" for n, c in plan["funnel"])
        log(f"      菜谱模式｜漏斗 {funnel}｜可用候选 {plan['n_combos']} 道"
            f"（超预算淘汰 {plan['over_budget']}）")
    else:
        plan = planner.plan(inv, allergens=set(cfg.get("exclude") or []), budget=budget)
        if "error" in plan:
            log("库存不足：" + plan["error"])
            return 2
        log(f"      组合模式｜预算 ¥{budget:.0f}/餐｜候选 {plan['n_combos']} 个"
            f"（超预算淘汰 {plan['over_budget']}）")

    menu = plan["best"]
    menu["meal_word"] = MEAL_WORD.get(args.slot, "晚上七点")
    log(f"      选中「{menu['name']}」（score {menu['score']:.3f}，"
        f"成本 ¥{menu['cost']:.1f}，占预算 {menu['cost_ratio']*100:.0f}%）")
    if plan.get("relaxed"):
        log("      注意：可选范围内都吃过了，本轮挑的是最久没吃的那道"
            "（想彻底不重样就放宽菜谱分类或加大库存）")
    if mode == "recipe":
        log(f"      平替保真 {menu['fidelity']}"
            f"｜用时 {menu['minutes']} 分钟｜{menu['category_label']}")
        for s in menu.get("swaps") or []:
            log(f"      平替：{s['from']} → {s['to']}（{s['reason']}）")
    if menu.get("flags"):
        log("      注意：" + "；".join(menu["flags"]))
    for d, why in plan["dropped"]:
        log(f"      过滤：{d} — {why}")
    for n, c in enumerate(plan["top3"], 1):
        log(f"      Top{n} {c['score']:.3f}  {c['name']}")

    # ---------------------------------------------------------- 2 文案
    if args.chat:
        try:
            lines, src = narrator.ark_chat(menu, os.environ.get("ARK_API_KEY"),
                                           os.environ.get("DIET_CHAT_MODEL",
                                                          "doubao-seed-2-1-pro-260915"))
            log("[2/8] 文案：ark_chat")
        except Exception as e:
            lines, src = narrator.template(menu), "template"
            log(f"[2/8] 文案：回落到 template（{type(e).__name__}: {str(e)[:80]}）")
    else:
        lines, src = narrator.template(menu), "template"
        log("[2/8] 文案：template（确定性，不调用模型）")
    for i, l in enumerate(lines, 1):
        log(f"      {i}. {l}")

    # ---------------------------------------------------------- 3 配音（先出音频）
    # 顺序很关键：真实语音的时长决定分镜时长，所以配音必须在分镜之前。
    voice, clips, durs, tts_note = None, None, None, None
    cred = speech_doubao.cred_from_env()
    tts_cfg = cfg.get("tts") or {}
    speaker = (os.environ.get("DIET_TTS_SPEAKER")
               or tts_cfg.get("speaker")
               or "zh_female_vv_uranus_bigtts")
    resource_id = (os.environ.get("DIET_TTS_RESOURCE_ID")
                   or tts_cfg.get("resource_id") or "seed-tts-2.0")
    # 语速原来有配置项、有夹紧范围、也有写进 manifest 的打算，但从来没被传下去：
    # 后台把滑杆拖到 1.5× 出的还是 1.0× 的片子。这里补上最后一段线。
    try:
        speed = float(tts_cfg.get("speed") or 1.0)
    except (TypeError, ValueError):
        speed = 1.0
    if args.tts in ("doubao", "auto"):
        try:
            if not cred:
                raise speech_doubao.SpeechError(
                    401, "45000010",
                    "未配置语音凭据：需要 VOLC_SPEECH_APP_ID + "
                    "VOLC_SPEECH_ACCESS_TOKEN，或 VOLC_SPEECH_API_KEY")
            clips = speech_doubao.tts_lines(
                lines, os.path.join(out, "voice"), cred, speaker,
                resource_id,
                speed=speed,
                cache_dir=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "cache", "tts"))
            durs = [c[1] for c in clips]
            log(f"[3/8] 配音：doubao 真人声 {len(clips)} 句，"
                f"语音总长 {sum(durs):.1f}s，音色 {speaker}，语速 {speed:g}×"
                f"（鉴权：{'APP ID + Token' if cred['mode'] == 'app' else 'API Key'}）")
        except Exception as e:
            tts_note = f"{type(e).__name__}: {e}"
            if args.tts == "doubao":
                log(f"[3/8] 配音失败：{e}")
                return 4
            log(f"[3/8] 配音：doubao 不可用 → 降级占位音轨")
            log(f"      {tts_note}")
            hint = getattr(e, "hint", "")
            if hint:
                log(f"      原因：{hint}")
    if durs is None:
        log("[3/8] 配音：mock 占位（静音+分镜提示音，非人声）")

    # ---------------------------------------------------------- 4 分镜
    # 转场时长从配置读，必须真的用上：字幕独占区间是按转场中点算的，
    # 只改 config 不改时间轴会出现字幕与画面错位。
    try:
        transition = float((cfg.get("video") or {}).get("transition", 0.3))
    except (TypeError, ValueError):
        transition = 0.3
    transition = max(0.0, min(1.0, transition))
    pack = storyboard.build(menu, lines, source=src, durs=durs,
                            transition=transition)
    log(f"[4/8] 分镜 {len(pack['shots'])} 个，总时长 {pack['total']}s"
        f"，转场 {pack['transition']}s"
        f"（时间轴依据：{'真实音频' if durs else '字数估算'}）")
    for s in pack["shots"]:
        log(f"      {s['start']:>5.2f}→{s['end']:>5.2f}  {s['image']:<8}"
            f"{s['motion']:<9} {s['dur']:.2f}s")

    # ---------------------------------------------------------- 4 配图（真实调用）
    keys = os.environ.get("ARK_API_KEY")
    if not keys:
        log("[5/8] 缺少 ARK_API_KEY，无法生图")
        return 2
    cli = ark_image.ArkImage(keys, cache_dir=os.path.join(ROOT, "runs", "_cache"))
    built = prompts.build(menu)
    refs = {"raw": [], "process": ["raw"], "final": ["raw", "process"],
            "hero": ["final"]}
    images, t_img = {}, time.time()
    log("[5/8] 配图 9:16（参考链：原料 → 过程 → 成品 → 特写）")
    for role, prompt in built:
        dst = os.path.join(img_dir, f"{role}.png")
        chain = [images[r]["path"] for r in refs[role] if r in images]
        # fidelity 也是缓存键的一部分，离线复用时必须传同样的值，否则算出的 key 不同
        fid = {"raw": None, "process": "medium", "hero": "medium"}.get(role, "high")
        if args.reuse_images:
            key = cli.cache_key(prompt, ark_image.SIZES_9X16[0], chain, fidelity=fid)
            img_path, meta_path = cli._cache_paths(key)
            if not os.path.exists(img_path):
                log(f"      {role}: 缓存缺失，跳过")
                return 3
            meta = json.load(open(meta_path, encoding="utf-8"))
            meta.update(cached=True, path=dst)      # 缓存 json 里不存 path，这里补上
            import shutil
            shutil.copy(img_path, dst)
        else:
            meta = cli.generate_vertical(prompt, out_path=dst, refs=chain, fidelity=fid)
        images[role] = meta
        log(f"      {role:<8} {meta['w']}x{meta['h']} ratio {meta['ratio']} "
            f"{meta['bytes'] // 1024}KB {meta['ms'] / 1000:.1f}s"
            f"{'  [cache]' if meta.get('cached') else ''}"
            f"{'  [降级 ' + str(meta.get('degrade')) + ']' if meta.get('degrade') else ''}")
    log(f"      生图耗时 {time.time() - t_img:.1f}s")

    # ---------------------------------------------------------- 5 字幕
    ass = subs.build_ass(pack, menu, os.path.join(out, "subtitle.ass"),
                         style=sub_style_from_env(cfg), slot=args.slot)
    subs.build_srt(pack, os.path.join(out, "subtitle.srt"))
    subs.narration_script(pack, menu, os.path.join(out, "旁白稿.md"))
    log(f"[6/8] 字幕：subtitle.ass / subtitle.srt / 旁白稿.md")

    # ---------------------------------------------------------- 7 音频落轨
    if clips:
        wav = os.path.join(out, "narration.wav")
        speech_doubao.assemble(pack, clips, wav)
        voice = {"path": wav, "engine": "doubao", "duration": pack["total"],
                 "voice_type": speaker, "sentences": len(clips),
                 "audio_sec": [round(d, 2) for d in durs]}
        log(f"[7/8] 音频：doubao  {voice['duration']}s  已按句子落轨")
    else:
        voice = speech_mock.synthesize(pack, menu, out, mode=args.tts)
        voice["degraded"] = True
        voice["degrade_reason"] = tts_note
        log(f"[7/8] 音频：占位音轨 {voice['duration']}s"
            f"（非人声，替换语音 Key 后自动切换）")

    # ---------------------------------------------------------- 7 合成
    clips, t_vid = [], time.time()
    for s in pack["shots"]:
        # 变量名不能叫 src：src 在 [2/8] 存的是「文案来源」（template/ark_chat），
        # 这里再赋一次图路径会把它覆盖掉，manifest 里的 narration.source
        # 就变成一张 png 的绝对路径 —— 排查「文案到底走没走模型」时会被误导。
        img_src = images[s["image"]]["path"]
        c = render.clip(img_src, s["dur"], s["motion"],
                        os.path.join(out, f"clip_{s['i']}.mp4"))
        clips.append(c)
    final = os.path.join(out, "final.mp4")
    render.compose(clips, [s["dur"] for s in pack["shots"]], "subtitle.ass",
                   voice["path"], final, workdir=out,
                   transition=pack["transition"])
    dur = render.probe(final)
    render.cover(final, 1.2, os.path.join(out, "cover.jpg"))
    size = os.path.getsize(final) / 1024 / 1024
    log(f"[8/8] 成片 final.mp4  {dur}s  {size:.1f}MB  (剪辑 {time.time() - t_vid:.1f}s)")

    for c in clips:
        os.remove(c)

    manifest = {
        "run_id": run_id, "user": args.user, "date": args.date, "slot": args.slot,
        "mode": mode,
        "menu": {k: menu[k] for k in ("name", "items", "facts", "minutes",
                                      "score", "parts")},
        # 成本单列一块：用户最关心的两个问题（这顿多少钱、为什么没推贵的）
        # 都应该能在 manifest 里直接查到，而不是回头读代码。
        "cost": {"total": menu["cost"], "budget": budget,
                 "ratio": menu["cost_ratio"],
                 "tier_mix": menu["tier_mix"],
                 "expensive_items": menu["expensive_items"],
                 "over_budget_dropped": plan["over_budget"],
                 # 菜谱模式下 protein_key 可能不是库存里的蛋白（整道菜没肉），
                 # premium_alternatives 需要它，缺了直接跳过而不是崩
                 "premium_options": (planner.premium_alternatives(inv, menu, budget)
                                     if menu.get("protein_key") else [])},
        "flags": menu.get("flags", []),
        "top3": [{"name": c["name"], "score": c["score"], "cost": c["cost"]}
                 for c in plan["top3"]],
        "narration": {"source": src, "lines": lines},
        "storyboard": pack,
        "images": {k: {kk: v[kk] for kk in ("model", "size_arg", "w", "h", "ratio",
                                            "ms", "bytes") if kk in v}
                   for k, v in images.items()},
        "video": {"path": "final.mp4", "duration": dur, "size_mb": round(size, 1),
                  "w": render.CW, "h": render.CH, "fps": render.FPS,
                  "ratio": "9:16"},
        "audio": voice,
        "elapsed_sec": round(time.time() - t_all, 1),
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    if mode == "recipe":
        # 菜谱模式的额外信息：这道菜本来是什么、换了哪些、步数
        manifest["recipe"] = {
            "id": menu.get("recipe_id"), "category": menu.get("category"),
            "category_label": menu.get("category_label"),
            "cuisine": menu.get("cuisine"), "methods": menu.get("methods"),
            "flavors": menu.get("flavors"), "desc": menu.get("desc"),
            "steps": menu.get("steps"), "serving": menu.get("serving"),
            "recipe_kcal": menu.get("recipe_kcal"),
            "fidelity": menu.get("fidelity"),
            "swaps": menu.get("swaps") or [],
            "missing": menu.get("missing") or [],
            "uses_stock": menu.get("uses_stock"),
            "funnel": plan.get("funnel"),
        }
        manifest["config"] = {"categories": cfg.get("categories"),
                              "target_category": cfg.get("target_category") or "",
                              "max_minutes": cfg.get("max_minutes"),
                              "repeat_cooldown_days": cfg.get("repeat_cooldown_days"),
                              "pick": cfg.get("pick"),
                              "exclude": cfg.get("exclude")}
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    # 成片真的落盘了才记历史 —— 放在这里而不是选完菜就记，
    # 是为了「生成失败算没吃过」，下一次不会因为一次失败白占掉一道菜。
    if mode == "recipe" and menu.get("recipe_id"):
        history.add(menu["recipe_id"], name=menu.get("name"), date=day,
                    slot=args.slot, run_id=run_id)
        log(f"      已记入历史：{menu['name']}（{len(history.load()['records'])} 条记录）")

    log(f"\n产出目录 {out}")
    log(f"总耗时 {time.time() - t_all:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
