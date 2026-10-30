#!/usr/bin/env python3
"""配音通道。两种模式：

  doubao —— 走豆包语音合成，真实人声 + 句级时间戳（需 VOLC_SPEECH_API_KEY）
  mock   —— 本机没有可用 TTS 时（语音 Key 还没换成 UUID 那把）的占位实现：
            按文案预估时长生成一条**等长测试轨**，每个分镜起点有一个轻提示音。
            它唯一的用途是让你能"听出"声画是否对齐，绝不能当成交付音轨。

mock 的价值不只是占位：它把音频层从"阻塞项"变成"可跳过项"，
图/字幕/视频这条 90% 的流程今天就能验证完，等 Key 到位只需换一个函数。
"""
import array
import json
import math
import os
import wave

SR = 44100
FPS = 30


def _tone(buf, start_sec, freq, ms=70, amp=0.07):
    n0 = int(start_sec * SR)
    n = int(ms / 1000 * SR)
    fade = int(0.012 * SR)
    for k in range(n):
        if n0 + k >= len(buf):
            break
        env = min(1.0, k / max(fade, 1), (n - k) / max(fade, 1))
        v = int(amp * env * 32767 * math.sin(2 * math.pi * freq * k / SR))
        buf[n0 + k] = max(-32768, min(32767, buf[n0 + k] + v))


def mock(pack, path):
    """等长静音轨 + 分镜起点提示音。"""
    total = pack["total"]
    n = int(total * SR)
    buf = array.array("h", bytes(2 * n))
    for s in pack["shots"]:
        _tone(buf, s["vstart"], 880, ms=70, amp=0.07)     # 开口点（不是切镜点）
    _tone(buf, max(0.0, total - 1.2), 1318, ms=180, amp=0.05)  # 结尾轻收

    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(buf.tobytes())
    return {"path": path, "engine": "mock", "duration": round(total, 3),
            "sample_rate": SR, "channels": 1,
            "peak": round(max(abs(x) for x in buf[::997]) / 32767, 3),
            "note": "占位音轨：静音 + 分镜提示音，非人声"}


def voice_plan(pack, menu, path, voice_type="zh_female_shuangkuaisisi_moon_bigtts"):
    """把"如果真调豆包"要发的请求先写出来，供核对与后续切换。

    每句单独请求（而不是整篇一次），因为分镜边界要按句子归属反推：
    整篇合成只给一个总音频 + 一串句子时间戳，跨句切分容易和分镜错位。
    """
    plan = {"provider": "volcengine_speech", "voice_type": voice_type,
            "encoding": "mp3", "speed_ratio": 1.0,
            "endpoint": "https://openspeech.bytedance.com/api/v1/tts",
            "auth_header": "X-Api-Key: <UUID 格式 Key>",
            "sentences": []}
    for s in pack["shots"]:
        plan["sentences"].append({
            "shot": s["i"], "text": s["text"], "chars": len(s["text"]),
            "predicted_sec": s["dur"], "slot": [s["vstart"], s["vend"]]})
    plan["fallback"] = "mock"
    plan["menu"] = menu["name"]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    return plan


def synthesize(pack, menu, out_dir, mode="mock"):
    os.makedirs(out_dir, exist_ok=True)
    wav = os.path.join(out_dir, "narration.wav")
    info = mock(pack, wav)
    voice_plan(pack, menu, os.path.join(out_dir, "voice_request_plan.json"))
    info["mode"] = mode
    with open(os.path.join(out_dir, "narration.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)
    return info
