#!/usr/bin/env python3
"""豆包语音合成（openspeech /api/v3/tts/unidirectional）。

与 mock 的关键差别：**时长来自真实音频**，不再是字数估算。
流程因此变成「先出音频 → 用音频时长定分镜 → 再排字幕」，
而不是「先估时长 → 事后对音频」。这是唯一能保证声画严格对齐的顺序。

鉴权有两种并存体系，端点相同、header 不同：

  A) 旧版控制台（火山引擎 → 语音技术 → 应用管理）：APP ID + Access Token
        X-Api-App-Id: <app_id>
        X-Api-Access-Key: <access_token>
  B) 新版控制台（豆包语音 → API Key 管理）：UUID 形式的 API Key
        X-Api-Key: <uuid>

两者都需要 X-Api-Resource-Id（大模型音色为 seed-tts-2.0）。
本模块按环境变量自动判定，A 优先（实测通过的生产配置）。
用错体系时报 45000010 Invalid X-Api-Key，与"key 未开通项目"的表现相同，
极易误判，所以这里显式区分。
"""
import array
import base64
import hashlib
import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
import uuid
import wave

HOST = "https://openspeech.bytedance.com"
ENDPOINT = HOST + "/api/v3/tts/unidirectional"
# 与 TTS 原生采样率一致 -> 拼轨零重采样
SR = 24000


class SpeechError(Exception):
    def __init__(self, status, code, message):
        super().__init__(f"[{status}/{code}] {message}")
        self.status, self.code, self.message = status, code, message

    @property
    def is_auth(self):
        return str(self.code) == "45000010"

    @property
    def hint(self):
        if self.is_auth:
            return ("凭据未被接受。注意有两套体系：旧版控制台用 "
                    "APP ID + Access Token（VOLC_SPEECH_APP_ID / "
                    "VOLC_SPEECH_ACCESS_TOKEN），新版控制台用 UUID API Key"
                    "（VOLC_SPEECH_API_KEY）。用错体系或该应用未开通语音合成都会报此码")
        return ""


def cred_from_env(env=None):
    """按环境变量判定凭据体系。返回 dict 或 None。"""
    env = os.environ if env is None else env
    app_id = (env.get("VOLC_SPEECH_APP_ID") or "").strip()
    token = (env.get("VOLC_SPEECH_ACCESS_TOKEN") or "").strip()
    api_key = (env.get("VOLC_SPEECH_API_KEY") or "").strip()
    if app_id and token:
        return {"mode": "app", "app_id": app_id, "access_key": token}
    if api_key:
        return {"mode": "key", "api_key": api_key}
    return None


def _headers(cred, resource_id):
    h = {"Content-Type": "application/json",
         "X-Api-Request-Id": str(uuid.uuid4())}
    if cred["mode"] == "app":
        h["X-Api-App-Id"] = cred["app_id"]
        h["X-Api-Access-Key"] = cred["access_key"]
    else:
        h["X-Api-Key"] = cred["api_key"]
    h["X-Api-Resource-Id"] = resource_id
    return h


def _probe_dur(path):
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", path],
                       capture_output=True, text=True)
    try:
        return round(float(p.stdout.strip()), 3)
    except Exception:
        return None


def tts_once(text, cred, speaker, resource_id, out_mp3,
             speed=1.0, timeout=120, retries=2):
    """合成一句。返回 (mp3 路径, 实际时长秒)。

    speed 为倍率（1.0 正常）；接口的 speech_rate 是相对基准的百分比整数，
    传错（例如把 1.0 直接传进去）会被当作 +1% 处理，等于没调速。
    """
    payload = {"user": {"uid": "daily-diet"},
               "req_params": {
                   "text": text, "speaker": speaker,
                   "audio_params": {
                       "format": "mp3",
                       "sample_rate": SR,
                       "speech_rate": int(round((speed - 1.0) * 100)),
                   }}}
    body = json.dumps(payload).encode()
    last, raw = None, b""
    for attempt in range(retries + 1):
        req = urllib.request.Request(ENDPOINT, data=body, method="POST",
                                     headers=_headers(cred, resource_id))
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
            last = None
            break
        except urllib.error.HTTPError as e:
            txt = e.read().decode("utf-8", "replace")
            code = msg = None
            try:
                j = json.loads(txt)
                node = j.get("header") if isinstance(j.get("header"), dict) else j
                code, msg = node.get("code"), node.get("message")
            except Exception:
                msg = txt[:200]
            err = SpeechError(e.code, code, msg)
            # 鉴权错重试无意义，直接抛
            if err.is_auth or e.code in (400, 401, 403):
                raise err
            last = err
        except Exception as e:
            last = SpeechError(0, None, f"{type(e).__name__}: {e}")
        if attempt == retries and last is not None:
            raise last

    # 响应是 NDJSON：每行一个 JSON，data 为 base64 音频片段，
    # code=0 为数据行，code=20000000 为结束标记
    audio = bytearray()
    for line in raw.decode("utf-8", "replace").strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            chunk = json.loads(line)
        except json.JSONDecodeError:
            continue
        if chunk.get("code") not in (0, 20000000, None):
            raise SpeechError(200, chunk.get("code"), chunk.get("message"))
        if chunk.get("data"):
            audio += base64.b64decode(chunk["data"])
        elif (chunk.get("result") or {}).get("data"):
            audio += base64.b64decode(chunk["result"]["data"])

    if not audio:
        raise SpeechError(200, None, "响应里没有音频数据")
    with open(out_mp3, "wb") as f:
        f.write(audio)
    return out_mp3, _probe_dur(out_mp3)


def tts_lines(lines, out_dir, cred, speaker="zh_female_vv_uranus_bigtts",
              resource_id="seed-tts-2.0", speed=1.0, cache_dir=None):
    """逐句合成。返回 [(mp3, 秒)]。

    逐句而不是整篇：分镜边界要按句子归属反推，整篇合成只给一个总音频，
    跨句切分容易和分镜错位。

    cache_dir 给了就按「文本+音色+语速+资源」做内容寻址缓存 —— 调字幕、
    调转场、改运镜都会重跑全片，这些改动不该重复付语音的钱。
    """
    os.makedirs(out_dir, exist_ok=True)
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
    out = []
    for i, text in enumerate(lines, 1):
        mp3 = os.path.join(out_dir, f"line_{i}.mp3")
        hit = None
        if cache_dir:
            sig = f"{speaker}|{speed}|{resource_id}|{text}"
            hit = os.path.join(
                cache_dir, hashlib.sha1(sig.encode("utf-8")).hexdigest()[:16] + ".mp3")
        if hit and os.path.exists(hit) and os.path.getsize(hit) > 512:
            dur = _probe_dur(hit)
            if dur:
                shutil.copy(hit, mp3)
                out.append((mp3, dur))
                continue
        path, dur = tts_once(text, cred, speaker, resource_id, mp3, speed)
        if not dur:
            raise SpeechError(0, None, f"第 {i} 句时长解析失败")
        if hit:
            shutil.copy(path, hit)
        out.append((path, dur))
    return out


def assemble(pack, clips, out_wav):
    """把每句音频按各自 vstart 放到时间轴上，合成一条等长音轨。

    用 PCM 直接拼而不是 ffmpeg adelay+amix：时长可控到采样点，
    不会因为滤镜自动补帧导致整体偏移几十毫秒。
    """
    total = pack["total"]
    n = int(total * SR) + SR        # 留 1 秒余量防止尾部被截
    buf = array.array("h", bytes(2 * n))

    for s, (mp3, _dur) in zip(pack["shots"], clips):
        wav = mp3 + ".wav"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp3,
                        "-ac", "1", "-ar", str(SR), "-acodec", "pcm_s16le", wav],
                       check=True, capture_output=True)
        with wave.open(wav, "rb") as w:
            if w.getnchannels() != 1 or w.getsampwidth() != 2:
                raise SpeechError(0, None, "解码结果不是 16bit 单声道")
            pcm = array.array("h", w.readframes(w.getnframes()))
        off = int(s["vstart"] * SR)
        end = min(n, off + len(pcm))
        buf[off:end] = array.array("h", pcm[:end - off])
        os.remove(wav)

    with wave.open(out_wav, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(buf[:int(total * SR)].tobytes())
    return out_wav
