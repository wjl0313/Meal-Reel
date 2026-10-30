#!/usr/bin/env python3
"""Phase 0 冒烟自检 —— 零第三方依赖（仅标准库）。

验证方案 v1.2 §18 的四个假设：
  1. ARK_API_KEY 能否同时调 images/generations 与 chat/completions
  2. size 参数是否接受 "1024x1536"（2:3），还是只能 1K/1.5K/2K
  3. 豆包语音字幕 JSON 的实际字段结构（句级/词级）
  4. 两把钥匙是否各自有效（ARK vs 豆包语音 UUID）

用法：
  python scripts/smoke.py doctor              # 只查配置与环境，不花钱
  python scripts/smoke.py chat                # 测文案（极少量 token）
  python scripts/smoke.py image --yes         # 测生图（会产生费用）
  python scripts/smoke.py tts                 # 测配音 + 字幕结构

.env 放在 daily-diet/.env，或由环境变量提供。
"""
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

ARK_HOST = "https://ark.cn-beijing.volces.com/api/v3"
DEF_IMAGE_MODEL = "doubao-seedream-5-0-pro-260628"
DEF_CHAT_MODEL = "doubao-seed-2-1-pro-260915"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "runs", "_smoke")


# ---------------------------------------------------------------- 环境
def load_env():
    """极简 .env 解析：不覆盖已存在的环境变量。"""
    path = os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v


def env(key, default=None):
    return os.environ.get(key) or default


def post_json(url, payload, headers, timeout=180):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"raw": body[:600]}
    except Exception as e:  # 超时 / DNS / 连接
        return 0, {"exception": f"{type(e).__name__}: {e}"}


def ark_headers():
    return {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + env("ARK_API_KEY", ""),
    }


# ---------------------------------------------------------------- 图片元信息（零依赖）
def image_dims(raw):
    """从 PNG IHDR 或 JPEG SOF 读宽高，避免引入 Pillow。"""
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        w = int.from_bytes(raw[16:20], "big")
        h = int.from_bytes(raw[20:24], "big")
        return ("png", w, h)
    if raw[:2] == b"\xff\xd8":
        i = 2
        while i < len(raw) - 9:
            if raw[i] != 0xFF:
                i += 1
                continue
            m = raw[i + 1]
            if m in (0xC0, 0xC1, 0xC2, 0xC3):
                h = int.from_bytes(raw[i + 5:i + 7], "big")
                w = int.from_bytes(raw[i + 7:i + 9], "big")
                return ("jpeg", w, h)
            if m in (0xD8, 0xD9) or 0xD0 <= m <= 0xD7:
                i += 2
                continue
            seg = int.from_bytes(raw[i + 2:i + 4], "big")
            i += 2 + seg
        return ("jpeg", 0, 0)
    return ("unknown", 0, 0)


# ---------------------------------------------------------------- 测试项
def test_image(size, prompt):
    url = ARK_HOST + "/images/generations"
    payload = {
        "model": env("DIET_IMAGE_MODEL", DEF_IMAGE_MODEL),
        "prompt": prompt,
        "size": size,
        "output_format": "png",
        "watermark": True,  # 合规：保留标识，勿关
    }
    t0 = time.time()
    status, body = post_json(url, payload, ark_headers(), timeout=240)
    ms = int((time.time() - t0) * 1000)
    if status != 200:
        err = body.get("error") or body
        return {"ok": False, "status": status, "size_arg": size,
                "error": json.dumps(err, ensure_ascii=False)[:400], "ms": ms}
    items = body.get("data") or []
    if not items:
        return {"ok": False, "status": status, "error": "no data[]", "ms": ms}
    item = items[0]
    raw = None
    if item.get("b64_json"):
        raw = base64.b64decode(item["b64_json"])
        src = "b64_json"
    elif item.get("url"):
        with urllib.request.urlopen(item["url"], timeout=120) as r:
            raw = r.read()
        src = "url"
    if not raw:
        return {"ok": False, "error": "unrecognized item keys: " + ",".join(item.keys())}
    os.makedirs(OUT, exist_ok=True)
    fmt, w, h = image_dims(raw)
    path = os.path.join(OUT, f"smoke_from_{size}.{fmt}")
    with open(path, "wb") as f:
        f.write(raw)
    return {"ok": True, "size_arg": size, "returned": f"{w}x{h}",
            "ratio": round(w / h, 4) if h else None,
            "format": fmt, "src": src, "bytes": len(raw),
            "path": path, "ms": ms,
            "usage": body.get("usage"), "model": body.get("model")}


# 冒烟用的固定提示词（原料图），供缓存键对齐使用
RAW_PROMPT = ("自然光美食摄影，俯拍，浅木纹桌面铺亚麻餐布，白瓷哑光盘上摆放"
              "生鸡胸肉160g、西兰花200g切小朵，食材保持原始形态，浅景深，无文字")


def cmd_image(argv):
    if "--yes" not in argv:
        print("生图会产生费用。确认请加 --yes。\n"
              "提示：第一步先用 1 张验证，别一上来就跑 3 张。")
        return 2
    prompt = RAW_PROMPT
    print("① 试探显式尺寸 1024x1536（2:3，裁到 9:16 损失最小）")
    r1 = test_image("1024x1536", prompt)
    results = [r1]
    if not r1["ok"]:
        print("   失败：" + str(r1.get("error"))[:160])
        print("② 回退到文档确认的档位 1.5K")
        r2 = test_image("1.5K", prompt)
        results.append(r2)
        if r2["ok"]:
            print(f"   成功，实际输出 {r2['returned']}")
    else:
        print(f"   成功，实际输出 {r1['returned']}（比例 {r1['ratio']}）")
    return report("image", results)


def cmd_chat():
    url = ARK_HOST + "/chat/completions"
    payload = {
        "model": env("DIET_CHAT_MODEL", DEF_CHAT_MODEL),
        "messages": [
            {"role": "system", "content": "你是营养餐解说文案。只输出 JSON。"},
            {"role": "user", "content": '用一句话说明"鸡胸补蛋白"这件事，返回 {"line":"..."}'},
        ],
        "temperature": 0.7,
    }
    status, body = post_json(url, payload, ark_headers(), timeout=90)
    if status != 200:
        return report("chat", [{"ok": False, "status": status,
                                "error": json.dumps(body, ensure_ascii=False)[:400]}])
    txt = (body.get("choices") or [{}])[0].get("message", {}).get("content", "")
    usage = body.get("usage", {})
    print("回复：" + txt.strip()[:120])
    print("tokens：" + json.dumps(usage, ensure_ascii=False))
    return report("chat", [{"ok": True, "status": status, "model": body.get("model"),
                            "usage": usage, "chars": len(txt)}])


def cmd_tts(argv):
    """调用 doubao-voice 脚本，重点是把 --subtitle 的 JSON 结构打出来。"""
    script = env("DOUBAO_VOICE_DIR",
                 os.path.expanduser("~/.workbuddy/skills/doubao-voice")) + "/scripts/speech_create.py"
    if not os.path.exists(script):
        print("未找到 doubao-voice 脚本：" + script)
        print("可设置 DOUBAO_VOICE_DIR 指向技能目录。")
        return 3
    key = env("VOLC_SPEECH_API_KEY") or env("VOLCENGINE_SPEECH_API_KEY")
    if not key:
        print("缺少 VOLC_SPEECH_API_KEY（豆包语音控制台的 UUID 格式 Key）")
        return 3
    os.makedirs(OUT, exist_ok=True)
    mp3 = os.path.join(OUT, "smoke_tts.mp3")
    sub_env = dict(os.environ, VOLCENGINE_SPEECH_API_KEY=key)
    cmd = [sys.executable, script,
           "-t", "鸡胸肉一百六十克，西兰花两百克，今晚这一盘，蛋白质刚好补上。",
           "-o", mp3, "--subtitle"]
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True, env=sub_env, timeout=300)
    ms = int((time.time() - t0) * 1000)
    if p.returncode != 0:
        print((p.stderr or p.stdout)[-800:])
        return report("tts", [{"ok": False, "ms": ms, "stderr": (p.stderr or "")[-400:]}])
    sub_json = os.path.splitext(mp3)[0] + ".json"
    info = {"ok": True, "mp3": mp3, "bytes": os.path.getsize(mp3), "ms": ms}
    if os.path.exists(sub_json):
        with open(sub_json, "r", encoding="utf-8") as f:
            sj = json.load(f)
        info["subtitle_file"] = sub_json
        info["subtitle_top_keys"] = list(sj.keys()) if isinstance(sj, dict) else f"list[{len(sj)}]"
        # 深挖一层，找出真正承载句子/时间戳的字段
        probe = sj
        for k in ("result", "data", "subtitles", "subtitle"):
            if isinstance(probe, dict) and k in probe:
                probe = probe[k]
                info.setdefault("subtitle_path", []).append(k)
        if isinstance(probe, dict):
            info["subtitle_inner_keys"] = list(probe.keys())
            for k in ("sentences", "utterances", "items", "words"):
                if k in probe and isinstance(probe[k], list) and probe[k]:
                    sample = probe[k][0]
                    info["first_item_keys"] = list(sample.keys()) if isinstance(sample, dict) else str(type(sample))
                    info["first_item_sample"] = sample
                    break
        print("字幕 JSON 顶层：" + json.dumps(info.get("subtitle_top_keys"), ensure_ascii=False))
        print("首个句子对象：" + json.dumps(info.get("first_item_sample"), ensure_ascii=False)[:400])
    else:
        print("警告：未生成同名字幕 JSON，字幕时间戳需另行解决")
    return report("tts", [info])


def cmd_doctor():
    print("环境自检")
    print("-" * 46)
    checks = []
    arkk = env("ARK_API_KEY", "")
    speechk = env("VOLC_SPEECH_API_KEY") or env("VOLCENGINE_SPEECH_API_KEY")
    checks.append(("ARK_API_KEY", bool(arkk), arkk[:7] + "…" if arkk else "未配置（图像+文案不可用）"))
    checks.append(("VOLC_SPEECH_API_KEY", bool(speechk),
                   "UUID 格式正常" if speechk and "-" in speechk
                   else ("疑似 ark- Key，语音接口会报 45000010" if speechk else "未配置（配音跳过）")))
    import shutil
    ff = shutil.which("ffmpeg")
    checks.append(("ffmpeg", bool(ff), ff or "未安装 → 无法合成视频"))
    font = os.path.join(ROOT, "assets", "fonts", "SourceHanSansSC-Bold.otf")
    checks.append(("中文字体", os.path.exists(font), font if os.path.exists(font)
                   else "缺失 → libass 会静默输出方块"))
    try:
        import openpyxl  # noqa
        checks.append(("openpyxl", True, "可解析 xlsx"))
    except ImportError:
        checks.append(("openpyxl", False, "未装 → xlsx 模板不可用，请改用 CSV"))
    for name, ok, note in checks:
        print(f"  [{'OK ' if ok else '-- '}] {name:<22} {note}")
    print("-" * 46)
    print("结论：ARK " + ("就绪" if arkk else "缺失") +
          " ｜ 语音 " + ("就绪" if speechk else "缺失") +
          " ｜ 合成 " + ("就绪" if ff and os.path.exists(font) else "不完整"))
    return 0


def report(stage, results):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, f"smoke_{stage}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"stage": stage, "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "results": results}, f, ensure_ascii=False, indent=2)
    print("报告：" + path)
    return 0 if all(r.get("ok") for r in results) else 4


def main():
    load_env()
    argv = sys.argv[1:]
    cmd = argv[0] if argv else "doctor"
    table = {"doctor": lambda: cmd_doctor(), "chat": cmd_chat,
             "image": lambda: cmd_image(argv), "tts": lambda: cmd_tts(argv)}
    if cmd not in table:
        print(__doc__)
        return 2
    print(f"=== smoke: {cmd} ===  ({uuid.uuid4().hex[:8]})\n")
    return table[cmd]()


if __name__ == "__main__":
    sys.exit(main())
