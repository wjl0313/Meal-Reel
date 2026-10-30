#!/usr/bin/env python3
"""字幕：ASS（烧进画面）+ SRT（外挂，给平台用）。

ASS 的关键参数都按 1080x1920 竖屏调过：
  · 字号按"老年观众可读"定：字幕 70px、营养卡 54px、标题 46px。
    1080 宽下 70px 约占屏宽 6.5%，手机全屏观看相当于电视字幕观感。
    调大字号必然减少每行字数（可用宽 960px / 70 ≈ 13.7 字），
    所以文案句长要控制在 26 字内，否则会挤成 3 行压到主体。
  · 字幕与营养卡用 BorderStyle=3（半透明底），不是纯描边：
    亮背景（白瓷盘）上白字即使加描边仍有"糊"的感觉，半透明底把局部
    对比度锁死，是老年可读性最有效的一招。透明度由 box_alpha 控制。
    标题与角标用 BorderStyle=1（描边），因为它们在画面上部，
    加底框会像新闻标题条，压住画面。
    ⚠ BorderStyle=3 的底框用的是 **OutlineColour**，不是 BackColour
    （BackColour 只管 Shadow）。把透明度写到 BackColour 上等于没写，
    结果就是纯黑实心块。这条踩过，别再改回去。
  · MarginV 300 —— 避开右下角「AI 生成」水印，也落在提示词要求的留白区
  · 顶部常驻菜名 + 「AI 生成」角标 —— 合规标识做双保险，不依赖图片内水印
"""
from . import foods
import re

# 字体名写死成 "Microsoft YaHei" 是移植时的头号隐患 —— 而且它**不报错**。
# 实测（本机 ffmpeg 7.1 + libass 0.17.3）：ASS 里指定一个不存在的字体名时，
#   fontselect: (NoSuchFontXYZ, 400, 0) -> ArialMT
# libass 静默回退到 Arial，中文全部变成方块或缺字，成片照常输出、退出码 0。
# 换到 Linux/macOS 上很容易踩到（那边没有中文字体或叫别的名字）。
# 所以：默认值给 Windows，可用 DIET_SUB_FONT 覆盖，并由 tools/doctor.py 体检。
DEFAULT_FONT = "Microsoft YaHei"

# 各平台常见中文字体，给体检时提示用
FONT_HINTS = {
    "win32": ["Microsoft YaHei", "SimHei", "SimSun"],
    "darwin": ["PingFang SC", "Hiragino Sans GB", "STHeiti"],
    "linux": ["Noto Sans CJK SC", "Source Han Sans SC", "WenQuanYi Zen Hei",
              "Droid Sans Fallback"],
}

# 可被调用方覆盖（run.py 从 .env 读后传入），core 层不读环境变量
DEFAULT_STYLE = {
    "main_size": 70,     # 主字幕
    "title_size": 46,    # 顶栏菜名
    "badge_size": 32,    # 「AI 生成」角标
    "stat_size": 54,     # 营养卡
    "main_outline": 12,  # BorderStyle=3 时作为底框内边距
    "box_alpha": "66",   # 底框透明度：00 全黑 → FF 全透，66 约 40% 透
    "title_outline": 4,  # 顶栏描边；顶栏常压在浅色背景上，3px 偏薄
    "title_margin_v": 56,
    "margin_v": 300,
    "wrap": 13.0,        # 每行最大视觉宽度（汉字=1，半角=0.5）
    "font": DEFAULT_FONT,
}

# 断行点：中文标点后优先，其次是空格
_BREAK = "、，。；：！？…—,;:!? "


def _vwidth(ch):
    """视觉宽度：汉字/全角记 1，ASCII 记 0.5（数字、字母约半个汉字宽）。"""
    return 0.5 if ord(ch) < 128 else 1.0


def wrap_cjk(text, max_w):
    """给中文长句手动插入 ASS 换行符 \\\\N。

    这一条不是可选优化 —— **libass 的自动换行靠空格分词，中文整句是一个"词"，
    永远不断行，直接溢出到画面外被裁掉**。56px 时刚好没暴露，字号一放大就露馅。
    所以按标点先切成语义片段，再贪心合并到 max_w 以内。
    """
    parts = re.findall(rf"[^{re.escape(_BREAK)}]+[{re.escape(_BREAK)}]?", text)
    if not parts:
        return text
    lines, cur, w = [], "", 0.0
    for p in parts:
        pw = sum(_vwidth(c) for c in p)
        if cur and w + pw > max_w:
            lines.append(cur)
            cur, w = p, pw
        else:
            cur += p
            w += pw
    if cur:
        lines.append(cur)
    return "\\N".join(lines)


def _ts(t, sep="."):
    h = int(t // 3600); m = int(t % 3600 // 60); s = t % 60
    if sep == ".":
        return f"{h}:{m:02d}:{s:05.2f}"
    ms = int(round((t - int(t)) * 1000))
    return f"{h:02d}:{m:02d}:{int(s):02d},{ms:03d}"


def _ass_color(hexrgb, alpha=0):
    r, g, b = hexrgb[0:2], hexrgb[2:4], hexrgb[4:6]
    return f"&H{alpha:02X}{b}{g}{r}"


HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,{font},{main_size},{white},{white},{box},{box},1,0,0,0,100,100,2,0,3,{main_outline},0,2,60,60,{margin_v},1
Style: Title,{font},{title_size},{dim},{dim},{ink},{ink},1,0,0,0,100,100,1,0,1,{title_outline},1,7,44,176,{title_margin_v},1
Style: Badge,{font},{badge_size},{dim},{dim},{ink},{ink},0,0,0,0,100,100,0,0,1,2,0,9,44,44,52,1
Style: Stat,{font},{stat_size},{white},{white},{box},{box},1,0,0,0,100,100,1,0,3,8,0,8,60,60,146,1
"""


# 顶栏标题的餐次措辞。原来写死成「今晚吃」，导致早餐、午餐的片子也标着
# 「今晚吃」—— 一天三餐一起出时三条都这么写，很明显是错的。
SLOT_TITLE = {"breakfast": "今早吃", "lunch": "今天中午吃", "dinner": "今晚吃"}


def build_ass(pack, menu, path, w=1080, h=1920, style=None, slot=None):
    st = dict(DEFAULT_STYLE)
    st.update(style or {})

    white = _ass_color("FFFFFF")
    dim = _ass_color("F2F2F2")
    ink = _ass_color("0A0A0A")
    box = _ass_color("000000", int(str(st["box_alpha"]), 16))

    out = [HEADER.format(w=w, h=h, white=white, dim=dim, ink=ink, box=box, **st)]
    out.append("[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, "
               "MarginR, MarginV, Effect, Text\n")

    total = pack["total"]
    # 顶部常驻：菜名 + 合规角标。
    # 字号放大后顶栏余量只剩约 400px，超长菜名会撞右上角「AI 生成」，
    # 所以超长时退回 short 版（narrator 已生成），而不是把字号缩回去。
    name = menu.get("name", "")
    if len(name) > 14 and menu.get("short"):
        name = menu["short"]
    title_word = SLOT_TITLE.get(slot or menu.get("slot") or "dinner", "今晚吃")
    out.append(f"Dialogue: 1,{_ts(0.2)},{_ts(total)},Title,,0,0,0,,"
               f"{{\\fad(400,300)}}{title_word} · {name}")
    out.append(f"Dialogue: 1,{_ts(0)},{_ts(total)},Badge,,0,0,0,,"
               f"{{\\fad(400,0)}}AI 生成")

    for s in pack["shots"]:
        # 用 vstart/vend（独占区间），不能用镜头区间 —— 否则转场时两条字幕会叠
        txt = wrap_cjk(s["text"], st["wrap"])
        out.append(f"Dialogue: 2,{_ts(s['vstart'])},{_ts(s['vend'])},Main,,0,0,0,,"
                   f"{{\\fad(140,140)}}{txt}")

    # 营养卡：跟最后一个镜头同时进场
    f = menu["facts"]
    last = pack["shots"][-1]
    card = (f"蛋白质 {f['protein']}g　热量 {f['kcal']}kcal\\N"
            f"深色蔬菜 {f['dark_veg_g']}g　蔬菜合计 {f['veg_g']}g")
    out.append(f"Dialogue: 3,{_ts(last['start'] + pack['transition'])},{_ts(total)},"
               f"Stat,,0,0,0,,{{\\fad(260,260)}}{card}")

    with open(path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(out) + "\n")
    return path


def build_srt(pack, path):
    blocks = []
    for n, s in enumerate(pack["shots"], 1):
        blocks.append(f"{n}\n{_ts(s['vstart'], ',')} --> {_ts(s['vend'], ',')}\n"
                      f"{s['text']}\n")
    with open(path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(blocks))
    return path


def narration_script(pack, menu, path):
    """给人看/给审校用的口播稿，标出每句的预计时长与营养溯源。"""
    lines = [f"# 口播稿 · {menu['name']}",
             f"总时长 {pack['total']}s，{len(pack['shots'])} 个分镜，"
             f"转场 {pack['transition']}s，文案来源 {pack['source']}", ""]
    for s in pack["shots"]:
        lines.append(f"[{s['vstart']:>5.2f} → {s['vend']:>5.2f}]  "
                     f"{s['image']:<8} {s['dur']:>4.2f}s  {s['text']}")
    lines += ["", "## 营养溯源（可食部克数 → 成分表）"]
    for i in menu["items"]:
        cooked = f"，熟重约 {int(i['cooked_g'])}g" if i["cooked_g"] else ""
        lines.append(f"- {i['name']}：可食部 {i['edible_g']}g{cooked}")
    f = menu["facts"]
    lines += ["", f"合计：热量 {f['kcal']}kcal｜蛋白 {f['protein']}g｜脂肪 {f['fat']}g"
                  f"｜碳水 {f['carb']}g｜膳食纤维 {f['fiber']}g"
                  f"｜蔬菜 {f['veg_g']}g（深色 {f['dark_veg_g']}g）"]
    with open(path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(lines) + "\n")
    return path
