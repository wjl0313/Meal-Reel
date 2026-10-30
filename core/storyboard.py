#!/usr/bin/env python3
"""分镜：把「4 句文案 + 3 张图」排成时间轴。

分镜是整条流水线的原子单位 —— 一张图 + 一句文案 + 一个时间区间。
改一句只重跑那一段，重生成一张图不动音频，这就是能断点续跑的前提。

时间轴受转场影响，容易算错：xfade 会让后一个镜头提前 T 秒开始，
所以第 i 个镜头在成片里的起点是
    start_i = (d_1 + ... + d_{i-1}) - (i-1) * T
总时长是 sum(d) - (n-1) * T。字幕和配音都必须按这个式子对齐，
否则每条字幕会累积性地早 T 秒 —— 4 个镜头就是 1.5 秒的错位。
"""
from . import narrator

TRANSITION = 0.3          # 交叉溶解时长。0.5s 时两张不同景别的图会明显叠影
MIN_DUR, MAX_DUR = 2.6, 8.0     # 静图单镜超过 8 秒观感就拖
VOICE_PAD = 0.85          # 真实语音下每镜的额外停留（含句尾空气）
TAIL_AIR = 0.10           # 句尾至少留的静音，否则听感上像被切断

# (用哪张图, 运镜, 额外停留秒数)
PLAN = [("hero", "zoom_in", 0.7),       # 特写开场，缓慢推近
        ("raw", "pan_down", 0.1),
        ("process", "zoom_in", 0.1),
        ("final", "pan_up", 0.9)]       # 全盘收尾


def build(menu, lines=None, source="template", durs=None, transition=None):
    """durs: 真实语音的每句时长（秒）。给了就按音频定时间轴，否则按字数估算。
    transition: 转场时长。不传才用模块默认值 —— 后台能调这个参数，
    它必须真的影响时间轴（字幕独占区间是按转场中点算的，改了不改这里就会错位）。

    真实语音下不能简单「音频 + 固定值」，必须反推：镜头时长要同时满足
      · 容纳本句音频（否则句尾被下一镜的转场吃掉）
      · 给句尾留 TAIL_AIR 的静音
    最紧的是第 2..n 镜（独占区间 = dur - TRANSITION - 0.08），所以按它算下界。
    """
    lines = lines or narrator.template(menu)
    assert len(lines) == len(PLAN), "文案条数与分镜数不一致"
    T = TRANSITION if transition is None else float(transition)

    shots, t = [], 0.0
    for i, ((img, motion, extra), text) in enumerate(zip(PLAN, lines)):
        if durs:
            # 下界：音频 + 转场占用 + 句尾空气。不再 clamp 上限 —— 音频长度是事实，
            # 强行压缩只会切掉字尾；句长应由文案层控制，不该在这里偷偷改。
            floor = durs[i] + TAIL_AIR + T + 0.08
            dur = round(max(MIN_DUR, durs[i] + VOICE_PAD, floor), 2)
        else:
            dur = min(MAX_DUR, max(MIN_DUR, narrator.est_duration(text) + extra))
            dur = round(dur, 2)
        start = round(t, 3)
        shots.append({"i": i + 1, "image": img, "motion": motion,
                      "text": text, "dur": dur, "start": start,
                      "end": round(start + dur, 3),
                      "audio_sec": None if not durs else round(durs[i], 3)})
        t += dur - T

    total = round(sum(s["dur"] for s in shots) - T * (len(shots) - 1), 3)

    # 字幕/配音的独占区间。镜头因 xfade 提前 T 秒出现，若字幕也按镜头区间排，
    # 相邻两条会重叠 T 秒 —— 画面上就是两行字叠在一起。
    # 以"转场中点"（画面真正换掉的那一刻）为开口点，就让每条独占一段：
    #   vstart_i = start_i + T/2  (i > 0)
    #   vend_i   = vstart_{i+1} - 0.08
    for i, s in enumerate(shots):
        s["vstart"] = round(s["start"] + (T / 2 if i else 0.0), 3)
    for i, s in enumerate(shots):          # 第二遍才能拿到后一条的 vstart
        s["vend"] = round(
            shots[i + 1]["vstart"] - 0.08 if i + 1 < len(shots) else total - 0.12, 3)

    # 不变量：每句音频必须完整落在自己的独占区间内。这里宁可报错也不静默截断 ——
    # 截断只表现为"最后一个字听不清"，画面和字幕都正常，很难发现。
    if durs:
        for s in shots:
            room = s["vend"] - s["vstart"]
            if room + 1e-6 < s["audio_sec"]:
                raise ValueError(
                    f"第 {s['i']} 镜音频 {s['audio_sec']:.2f}s 超出区间 {room:.2f}s，"
                    f"句尾会被截断；检查 TRANSITION / VOICE_PAD 设置")

    return {"shots": shots, "total": total, "transition": T,
            "source": source}
