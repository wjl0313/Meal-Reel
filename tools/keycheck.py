#!/usr/bin/env python3
"""火山语音凭据体检 —— 零依赖，不需要改 .env。

火山语音有**两套并存**的鉴权体系，端点相同、header 不同：

  A) 旧版控制台「语音技术 → 应用管理」：APP ID + Access Token
        X-Api-App-Id + X-Api-Access-Key
  B) 新版控制台「豆包语音 → API Key 管理」：UUID 形式的 Key
        X-Api-Key

用错体系、或应用/项目下没开通语音合成，**两者都报 45000010** ——
同一个错误码有两种成因，只看码会诊断偏。

用法：
    python tools/keycheck.py                            # 测 .env 里的凭据
    python tools/keycheck.py <APP_ID>:xxxxx           # 测 APP ID + Token
    python tools/keycheck.py xxxxxxxx-xxxx-...          # 测 UUID API Key
    python tools/keycheck.py a:b c:d uuid --all         # 一次测多组

报告把错误码翻译成人话，并指明下一步去哪个控制台。
"""
import json
import os
import sys
import uuid
import urllib.error
import urllib.request

HOST = "https://openspeech.bytedance.com"
PROBE = HOST + "/api/v3/tts/unidirectional"

HINTS = {
    "45000010": "凭据未被接受。三个成因按概率排序：\n"
                "            ① 用错了体系 —— 拿旧版 APP ID 配 X-Api-Key，或拿 UUID 配 X-Api-App-Id；\n"
                "            ② 应用/项目下没开通语音合成大模型；\n"
                "            ③ 凭据属于另一个火山账号。",
    "45000030": "缺 X-Api-Resource-Id 头（seed-tts-2.0 / seed-tts-1.0），不是凭据的问题。",
    "45000000": "请求体格式不对，鉴权大概率已通过 —— 换正式请求体再试。",
    "3001":     "旧版 v1 接口，body 里需要 appid + cluster，header 是 Authorization: Bearer;token。",
}


def load_env(path=".env"):
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for p in (path, os.path.join(here, ".env")):
        if p and os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            return p
    return None


def post(body, headers, timeout=60):
    req = urllib.request.Request(PROBE, data=json.dumps(body).encode(),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}".encode()


def code_of(raw):
    try:
        j = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return None, raw[:120].decode("utf-8", "replace")
    node = j.get("header") if isinstance(j.get("header"), dict) else j
    return node.get("code"), node.get("message", "")


def classify(spec):
    """把命令行参数/环境变量归一成凭据 dict。"""
    spec = spec.strip().strip('"').strip("'")
    if ":" in spec:
        app_id, token = spec.split(":", 1)
        return {"mode": "app", "app_id": app_id.strip(), "access_key": token.strip()}
    return {"mode": "key", "api_key": spec}


def headers_for(c):
    h = {"Content-Type": "application/json", "X-Api-Request-Id": str(uuid.uuid4()),
         "X-Api-Resource-Id": "seed-tts-2.0"}
    if c["mode"] == "app":
        h["X-Api-App-Id"] = c["app_id"]
        h["X-Api-Access-Key"] = c["access_key"]
    else:
        h["X-Api-Key"] = c["api_key"]
    return h


def label(c):
    if c["mode"] == "app":
        tok = c["access_key"]
        return f"APP ID + Token   app_id={c['app_id']}  token=***{tok[-4:] if len(tok) > 4 else ''}"
    k = c["api_key"]
    shape = "UUID" if len(k) == 36 and k.count("-") == 4 else "非 UUID"
    return f"API Key          ***{k[-4:] if len(k) > 4 else ''}  len={len(k)} ({shape})"


def check(c, speaker):
    body = {"user": {"uid": "keycheck"},
            "req_params": {"text": "你好", "speaker": speaker,
                           "audio_params": {"format": "mp3"}}}
    st, raw = post(body, headers_for(c))
    code, msg = code_of(raw)
    ok = st == 200
    # 200 也可能是流式里的业务错误，抓第一行的 code
    if ok:
        first = raw.decode("utf-8", "replace").strip().splitlines()
        if first:
            try:
                j = json.loads(first[0])
                code = j.get("code")
                ok = code in (0, 20000000)
                msg = j.get("message", "")
            except Exception:
                pass
    return ok, st, code, msg


def main():
    raw_args = [a for a in sys.argv[1:] if not a.startswith("--")]
    env_path = load_env()
    candidates = []

    if raw_args:
        candidates = [classify(a) for a in raw_args]
    else:
        app_id = (os.environ.get("VOLC_SPEECH_APP_ID") or "").strip()
        token = (os.environ.get("VOLC_SPEECH_ACCESS_TOKEN") or "").strip()
        key = (os.environ.get("VOLC_SPEECH_API_KEY") or "").strip()
        if app_id and token:
            candidates.append({"mode": "app", "app_id": app_id, "access_key": token})
        if key:
            candidates.append({"mode": "key", "api_key": key})
        if not candidates:
            print("没找到语音凭据。两种写法任选：")
            print("  python tools/keycheck.py <APP_ID>:<access_token>")
            print("  python tools/keycheck.py <uuid-api-key>")
            return 2

    speaker = os.environ.get("DIET_TTS_SPEAKER", "zh_female_vv_uranus_bigtts")
    print(f"配置来源：{env_path or '仅环境变量'}｜音色：{speaker}\n")

    best = None
    for i, c in enumerate(candidates, 1):
        ok, st, code, msg = check(c, speaker)
        print(f"[{i}] {label(c)}")
        print(f"     合成探测  HTTP {st}  code={code}  {str(msg)[:70]}")
        if ok:
            print("     结论：✅ 可用")
            best = c
        else:
            print("     结论：❌ 不可用")
            hint = HINTS.get(str(code))
            if hint:
                print(f"     可能原因：{hint}")
        print()

    if best:
        if best["mode"] == "app":
            print("写进 .env：")
            print(f"  VOLC_SPEECH_APP_ID={best['app_id']}")
            print("  VOLC_SPEECH_ACCESS_TOKEN=<token>")
        else:
            print("写进 .env：")
            print("  VOLC_SPEECH_API_KEY=<key>")
        print("\n然后跑：python run.py --user U001 --slot dinner --reuse-images --tts doubao")
    return 0 if best else 1


if __name__ == "__main__":
    sys.exit(main())
