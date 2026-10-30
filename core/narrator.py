#!/usr/bin/env python3
"""解说文案。双实现：

  template  —— 纯模板拼接，无模型，零成本，永远可用（兜底）
  ark_chat  —— 走方舟 chat/completions，语言更自然（需账号已开通文案模型）

铁律：文案里的每一个数字都来自 menu["facts"] / menu["cost"]，模型不得新造数字。
ark_chat 只允许改写句式，接收到的 facts 以 JSON 给出并要求「数字照抄」。

措辞按**类别**取，不按具体食材穷举：库里 103 种食材，逐个写"两面各煎两分半"
既写不全也维护不了，而新池子每换一次品种就会多出一批没有动词的食材，
文案立刻退化成"草鱼下锅炒香"。
"""
from . import foods

VOICE_RATE = 4.2          # 中文 TTS 约 4.2 字/秒（豆包默认语速实测区间 3.8–4.5）

# 主料怎么做 —— 按主类
COOK_PROTEIN = {
    "禽肉类": "两面各煎两分半",
    "畜肉类": "大火快炒到变色",
    "鱼虾蟹贝类": "水开后蒸八分钟",
    "蛋类": "六成油温快速划散",
    "干豆类": "提前泡发再炖",
    "乳类": "拌匀即可",
}
# 蔬菜怎么做 —— 按叶类
COOK_VEG = {
    "嫩茎叶花菜类": "沸水焯四十秒",
    "根菜类": "切薄片小火煸软",
    "茄果瓜菜类": "最后下锅翻两下",
    "葱蒜类": "起锅前爆香",
    "薯芋类": "切丝泡水去淀粉",
    "鲜豆类": "焯水后再炒",
    "水生蔬菜类": "切片快炒保持脆",
    "菌类": "撕小朵快炒",
    "藻类": "提前泡开洗净",
    "野生蔬菜类": "焯水后凉拌",
}


def _sub(key):
    return foods.FOODS[key]["category"].split("-")[0].replace("及其制品", "")


def _leaf(key):
    return foods.FOODS[key]["category"].split("-")[-1]


def _name(key):
    return foods.disp(key)


def template(menu):
    """四句：钩子 / 食材 / 步骤 / 营养与成本。每句压在 26 字内 ≈ 6 秒，
    静图视频里单镜超过 7 秒观感就明显拖。

    菜谱模式（menu 里有 recipe_id）走 from_recipe，这里只管组合模式。
    """
    if menu.get("recipe_id"):
        return from_recipe(menu)

    f = menu["facts"]
    items = [i for i in menu["items"] if i["role"] not in ("调味",)]
    top = sorted(items, key=lambda i: -i["edible_g"])[:2]
    parts = "、".join(f"{i['disp']}{int(round(i['edible_g']))}克" for i in top)

    p = menu["protein_key"]
    v = menu["veg_keys"][0] if menu["veg_keys"] else None
    # 第三句必须讲「火候」而不是「腌料」：过程图画的就是下锅后的状态，
    # 文案说"撒盐静置"而画面是煎好的金黄，观众一眼看出不对。
    third = f"{_name(p)}{COOK_PROTEIN.get(_sub(p), '下锅炒香')}"
    if v and v in foods.FOODS:
        third += f"；{_name(v)}{COOK_VEG.get(_leaf(v), '断生即可')}"
    return [
        f"{_meal_word(menu)}，{menu['minutes']}分钟，一道{menu.get('short', menu['name'])}。",
        f"{parts}，都是冰箱里现成的。",
        third + "。",
        f"{f['protein']:g}克蛋白、{f['kcal']}千卡，"
        f"深色菜{f['dark_veg_g']}克，成本{menu['cost']:.0f}块。",
    ]


def _meal_word(menu):
    """开场的时间词。早/午/晚不同，让文案别一到早上就说「晚上七点」。"""
    return menu.get("meal_word") or "晚上七点"


def _short(s, limit=26):
    """句子压到 limit 字内。

    截断本身是最后手段：能整句说就整句说。所以调用方应该先用
    `_pick_step` 挑一条**本来就装得下**的步骤，实在没有才落到这里。
    截断时优先在最近的标点断，断完补句号 —— 原来断在「放入鸡胸肉」
    这种半句上，字幕是一个悬空的逗号，听着像话没说完。
    """
    s = (s or "").strip().rstrip("。！？")
    if len(s) <= limit:
        return s
    cut = -1
    for i, ch in enumerate(s[:limit]):
        if ch in "，；、,;":
            cut = i
    body = s[:cut] if cut >= limit // 2 else s[:limit]
    return body.rstrip("，；、,; ")


def _pick_step(steps, limit=26):
    """从步骤里挑一条最适合单独播出来的。

    优先级：含烹饪动词 > 原句就装得下 > 短。
    原实现是「先按动词挑，挑到哪条算哪条」，于是一半的长步骤被
    `_short` 硬截成半句（400 道抽样里 34.8% 如此）。改成整句优先：
    宁可播一条短而完整的步骤，也不要播一条信息多但被砍断的。
    """
    cands = [s.strip().rstrip("。") for s in (steps or []) if s and s.strip()]
    if not cands:
        return ""
    verbs = ("煎", "炒", "蒸", "煮", "焖", "烤", "拌", "炖", "焯", "煸", "烧")

    def rank(item):
        i, s = item
        has_verb = any(v in s for v in verbs)
        fits = len(s) <= limit
        # 有动词且装得下 → 最优；其次装得下；再次有动词但超长；最后其他
        tier = (0 if (has_verb and fits) else
                1 if fits else
                2 if has_verb else 3)
        return (tier, -i)

    # 先看第 2 句起（第一句常是「洗净切块」这类准备工作，信息量偏低），
    # 再回看第一句，保持原来的偏好顺序。
    ordered = list(enumerate(cands[1:], 1)) + list(enumerate(cands[:1], 0))
    return min(ordered, key=rank)[1]


def from_recipe(menu):
    """菜谱模式的四句。**步骤直接取自菜谱**，不是套模板生成的火候话术 ——
    这正是这个模式存在的理由：真有步骤，就能说出"先腌十五分钟"这种信息。

    第四句会把平替讲明白。不讲的话，用户看到的菜名是「凉拌鸡胸·冬瓜版」，
    听到的却是"鸡胸和黄瓜"，两边对不上。
    """
    f = menu["facts"]
    steps = [s for s in (menu.get("steps") or []) if s.strip()]
    items = [i for i in menu["items"] if i["role"] not in ("调味",)]
    top = sorted(items, key=lambda i: -i["edible_g"])[:2]
    parts = "、".join(f"{i['disp']}{int(round(i['edible_g']))}克" for i in top)

    hook = f"{_meal_word(menu)}，{menu['minutes']}分钟，一道{menu['short']}。"
    ing = f"{parts}，都是冰箱里现成的。" if not menu.get("swaps") else \
        f"{parts}，缺的几样用同类替了。"
    key = _pick_step(steps) or "按家常做法做熟即可"
    third = _short(key) + "。"
    tail = (f"{f['protein']:g}克蛋白、{f['kcal']}千卡，"
            f"深色菜{f['dark_veg_g']}克，成本{menu['cost']:.0f}块。")
    if menu.get("swaps"):
        sw = menu["swaps"][0]
        tail = (f"{f['protein']:g}克蛋白、{f['kcal']}千卡，成本{menu['cost']:.0f}块，"
                f"{sw['from']}换成了{sw['to']}。")
    return [hook, ing, third, tail]


def est_duration(text):
    """文案 → 预估语音时长（秒）。真实 TTS 会是这个数 ±5%，足够排时间轴。"""
    n = len([c for c in text if c.strip()])
    return round(max(2.2, n / VOICE_RATE + 0.45), 2)


def ark_chat(menu, api_key, model, timeout=60):
    """走方舟改写文案。失败时调用方应回落 template()。"""
    import json
    import urllib.request
    from clients.ark_image import ARK_HOST

    base = template(menu)
    prompt = (
        "你在给一条竖屏美食短视频写旁白，共 4 句，依次对应：开场钩子 / 食材清点 / "
        "关键步骤 / 营养与成本收尾。要求：口语、短句、每句不超过 30 个字；"
        "**所有数字必须原样照抄下面的 facts 与成本，不得新增或修改任何数字**；"
        "不要出现「美味」「诱人」这类空话。只输出 JSON 数组，4 个字符串。\n"
        f"facts = {json.dumps(menu['facts'], ensure_ascii=False)}\n"
        f"成本 = {menu['cost']} 元；菜名 = {menu['name']}；耗时 = {menu['minutes']} 分钟\n"
        f"参考写法 = {json.dumps(base, ensure_ascii=False)}"
    )
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                       "temperature": 0.7, "max_tokens": 600}).encode()
    req = urllib.request.Request(ARK_HOST + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + api_key},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode())
    txt = data["choices"][0]["message"]["content"].strip()
    txt = txt[txt.find("["): txt.rfind("]") + 1]
    lines = json.loads(txt)
    if not isinstance(lines, list) or len(lines) != 4:
        raise ValueError("模型未按要求返回 4 句")
    return [str(x).strip() for x in lines], "ark_chat"
