#!/usr/bin/env python3
"""三类配图的提示词装配 —— 固定 9:16 竖屏。

一致性靠「参考链」而不是反复描述：原料图 text2img → 过程图带原料图为参考 →
成品图带原料图+过程图两张参考。经验上这比在提示词里写十遍「同一只白盘」有效得多。

竖屏有三个和横屏不同的硬约束，必须写进每一条提示词：
  1. 主体在画面中上部，下方 25% 留干净区（字幕要落在这里）
  2. 四周留 8% 安全边距，主体不出边（竖屏边缘更容易切掉配菜）
  3. 不出现人手、不出现文字 —— 手部是最容易触发内容审核的元素，文字会和字幕打架

画面描述按**类别**取，和文案层同一套依据：库里 103 种食材，
逐个写「黑胡椒与欧芹碎」写不完，且换品种就会露出"食材在锅中受热"这种空话。
"""
from . import foods

STYLE = ("竖版 9:16 美食摄影，自然侧逆光，浅木纹桌面铺浅灰亚麻餐布，白瓷哑光盘，"
         "色彩清爽不过饱和，浅景深，食物集中在画面中上部，"
         "下方 25% 保持干净留白用于字幕，四周留 8% 安全边距、主体不出边")

NEGATIVE = "画面中不出现人手、不出现人脸、不出现任何文字或logo、不过度饱和、不塑料感"

# 主料成熟后的样子 —— 按主类
DONE = {
    "禽肉类": "煎到表面金黄微焦、边缘略深",
    "畜肉类": "快炒至刚变色、仍带光泽",
    "鱼虾蟹贝类": "刚蒸熟、肉质雪白微微颤动",
    "蛋类": "刚凝成嫩块、仍带湿润光泽",
    "干豆类": "炖到软烂、汤色浓稠",
    "乳类": "拌匀成柔滑质地",
}
PLATED = {
    "禽肉类": "煎好切片的禽肉",
    "畜肉类": "炒好的肉片",
    "鱼虾蟹贝类": "整条蒸好的鱼" ,
    "蛋类": "嫩炒蛋",
    "干豆类": "炖好的豆制品",
    "乳类": "拌好的乳制品",
}
# 蔬菜在锅边的样子 —— 按叶类
BESIDE = {
    "嫩茎叶花菜类": "锅边码着焯过水的翠绿叶菜",
    "根菜类": "锅边码着橙色根菜薄片",
    "茄果瓜菜类": "锅边放着切好的瓜茄块",
    "葱蒜类": "锅边放着切段的葱蒜",
    "薯芋类": "锅边码着泡过水的薯类细丝",
    "鲜豆类": "锅边码着翠绿的鲜豆",
    "水生蔬菜类": "锅边码着雪白的藕片",
    "菌类": "锅边码着撕成小朵的菌菇",
    "藻类": "锅边放着泡发好的海藻",
    "野生蔬菜类": "锅边码着焯过水的野菜",
}
HERO_FOCUS = {
    "禽肉类": "切片断面的纹理与表面香料",
    "畜肉类": "肉片边缘的焦色与油光",
    "鱼虾蟹贝类": "鱼皮的胶质光泽与雪白鱼肉",
    "蛋类": "嫩蛋的湿润褶皱",
    "干豆类": "豆制品吸饱汤汁的孔洞",
    "乳类": "柔滑表面的质感",
}


def _sub(key):
    return foods.FOODS[key]["category"].split("-")[0].replace("及其制品", "")


def _leaf(key):
    return foods.FOODS[key]["category"].split("-")[-1]


def _mains(menu):
    """主料+蔬菜，不含油和调味。"""
    return [i for i in menu["items"] if i["role"] not in ("调味",)]


def raw(menu):
    """生食材俯拍。这一张**故意不带菜名**：画面就是一盘还没下锅的原料，
    红烧豆腐和麻婆豆腐的生料本来就该长一样。带上菜名只会让缓存永远不命中，
    多花一份钱换一张没有差别的图。菜品身份从 process 开始才成立。
    """
    its = _mains(menu)
    desc = "、".join(f"{i['disp']} {int(round(i['edible_g']))} 克" for i in its[:3])
    scene = (f"生的食材摊放在白瓷盘上：{desc}，保持原始形态，"
             f"旁边一小碟植物油与两瓣大蒜，清爽干净，尚未加热")
    return "raw", _wrap(scene)


def process(menu):
    p = menu["protein_key"]
    v = menu["veg_keys"][0] if menu["veg_keys"] else None
    side = BESIDE.get(_leaf(v), "") if v and v in foods.FOODS else ""
    scene = (f"浅色厨房台面上，同一只锅里的{_sname(p)}"
             f"{DONE.get(_sub(p), '受热至成熟')}" +
             (f"，{side}" if side else "") +
             f"，食材纹理清晰、热气轻扬，油光克制")
    return "process", _wrap(scene, _dish_tag(menu))


def final(menu):
    p = menu["protein_key"]
    its = _mains(menu)
    # 画面里必须有主食，否则「561 千卡」里那 60 克生米在画面上找不到出处
    staple = next((i for i in menu["items"] if i["role"] == "主食"), None)
    rest = [i for i in its if i["role"] != "主食"]
    desc = "、".join(f"{i['disp']} {int(round(i['edible_g']))} 克" for i in rest[:3])
    extra = (f"，画面一侧一只小碗盛约 {int(staple['cooked_g'])} 克"
             f"{staple['disp']}" if staple and staple.get("cooked_g") else "")
    scene = (f"成品摆盘：{desc}，{PLATED.get(_sub(p), '做好的主料')}整齐码在白瓷盘中央，"
             f"{_garnish(menu)}{extra}，45 度俯拍，分量真实不堆砌")
    return "final", _wrap(scene, _dish_tag(menu))


def hero(menu):
    """开场钩子：成品特写。与 final 同盘不同机位，避免首尾同图。"""
    p = menu["protein_key"]
    scene = (f"只取盘子的局部近景，画面里看不到完整的盘子边缘，"
             f"镜头贴近到约一臂之内，聚焦{HERO_FOCUS.get(_sub(p), '菜品格纹与配料细节')}，"
             f"浅景深、背景强虚化，油光与蒸汽细节清晰，画面中上部为焦点")
    return "hero", _wrap(scene, _dish_tag(menu))


def _sname(key):
    return foods.disp(key)


# 摆盘收尾的措辞。原来四个镜头位统一写「撒少量黑胡椒与欧芹碎」——
# 那是西餐的收尾动作，套到「红烧豆腐」「番茄炒蛋」上画面会变成一个
# 明显不是这道菜的东西。按菜系取，取不到才退回「撒葱花」这种中餐通用收尾。
GARNISH = {
    "chuan": "撒一把花椒面与葱花",
    "xiang": "撒一把鲜红剁椒与葱花",
    "yue": "淋一勺清亮的豉油，配两根菜心",
    "suzhe": "淋一层薄薄的糖色芡汁",
    "lu": "浇一勺浓稠的原汤，撒葱段",
    "dongbei": "码一撮葱丝与香菜段",
    "shan": "撒一撮辣椒面与蒜末",
    "xinjiang": "撒一撮孜然与洋葱碎",
    "dian": "点几粒小米辣与香菜",
    "hui": "淋一勺火腿高汤收汁",
    "min": "淋一勺清汤，撒葱花",
    "bei": "码几段葱白，浇一勺明油",
    "e": "撒一撮白胡椒粉与葱花",
    "generic": "撒一小把葱花，淋半勺明油",
}


def _garnish(menu):
    """按菜系取摆盘收尾措辞；菜谱模式才有 cuisine，组合模式退回通用。"""
    return GARNISH.get((menu.get("cuisine") or "").strip(), GARNISH["generic"])


def _dish_tag(menu):
    """把「这是什么菜」压成一句画面前缀。

    这条是修一个很贵的缺陷：原来四个镜头位的提示词里**一个菜名都不含**，
    只有「豆腐 350 克 + 炖好的豆制品」这种食材级描述。后果是同一类食材的
    所有菜共用同一张图 —— 红烧豆腐和麻婆豆腐、家常豆腐，出来的画面完全一样，
    而且缓存键只由 prompt 决定，换个菜谱直接命中上一道的图，白拿一张错的图。
    把菜名带上，画面才会真的是这道菜，缓存键也自然分开。
    """
    name = menu.get("short") or menu.get("name") or ""
    if not name:
        return ""
    bits = [f"这道菜是「{name}」"]
    if menu.get("cuisine") and menu["cuisine"] != "generic":
        bits.append("，一道" + {"chuan": "川菜", "xiang": "湘菜",
                               "yue": "粤菜", "lu": "鲁菜", "suzhe": "苏浙菜",
                               "dongbei": "东北菜", "shan": "陕菜",
                               "xinjiang": "新疆菜", "dian": "滇菜",
                               "hui": "徽菜", "min": "闽菜", "bei": "北方菜",
                               "e": "鄂菜"}.get(menu["cuisine"], "家常菜"))
    elif menu.get("category_label"):
        bits.append("，一道" + menu["category_label"])
    if menu.get("methods"):
        bits.append("，做法是" + "／".join(menu["methods"][:2]))
    # 结尾留句号：否则会和后面的场景描述粘成「…做法是煎／红烧浅色厨房台面上…」
    return "".join(bits) + "。"


def _wrap(scene, tag=""):
    return f"{STYLE}。{tag}{scene}。{NEGATIVE}。"


def build(menu):
    """返回 [(作用, prompt)]，顺序即生成顺序（参考链依赖前一张）。"""
    return [raw(menu), process(menu), final(menu), hero(menu)]
