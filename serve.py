#!/usr/bin/env python3
"""配置后台：零依赖 HTTP 服务 + 纯静态前端。

    python serve.py            # http://127.0.0.1:8765
    python serve.py --port 9000 --open

为什么只用标准库：这个项目从第一天起就是零第三方依赖（urllib 代 requests，
模板自己拼）。为了一个配置页去装 Flask 会打破这个约定，也会让「clone 下来就能跑」
变成「先 pip install」。http.server 够用，线程池 + 一个 JSON 路由表就行。

安全边界（本地工具也该有）：
  · 静态文件只允许 web/ 与 runs/ 两个目录，且必须 resolve 到目录内部
  · 路径里的 .. 一律拒绝，不用字符串前缀判断（那样 /web/../secret 会漏）
  · 只监听 127.0.0.1，不做 0.0.0.0
  · 密钥永不通过 API 返回，只回「配没配」
"""
import argparse
import json
import mimetypes
import os
import queue
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from core import planner, recipes as recipe_mod, settings  # noqa: E402

WEB = os.path.join(HERE, "web")
RUNS = os.path.join(HERE, "runs")

CATEGORY_LABEL = recipe_mod.CATEGORY_LABEL
SLOT_LABEL = {"breakfast": "早餐", "lunch": "午餐", "dinner": "晚餐"}
# 菜系用拼音做 key，界面上得给人看得懂的字
CUISINE_LABEL = {
    "generic": "家常", "chuan": "川菜", "xiang": "湘菜", "yue": "粤菜",
    "e": "鄂菜", "lu": "鲁菜", "suzhe": "苏浙", "bei": "北方", "hui": "徽菜",
    "min": "闽菜", "dongbei": "东北", "dian": "滇菜", "shan": "陕菜",
    "xinjiang": "新疆",
}


# ---------------------------------------------------------------- 运行状态
class Runner:
    """一次只跑一个出片任务。并发跑两个会抢 runs/_cache 和 ffmpeg 临时文件。"""

    def __init__(self):
        self.proc = None
        self.lines = []
        self.run_id = None
        self.started = None
        self.rc = None
        self.lock = threading.Lock()

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, argv, run_id):
        if self.running():
            return False, "已经有一个任务在跑了"
        with self.lock:
            self.lines = []
            self.run_id = run_id
            self.started = time.time()
            self.rc = None
            # -u：不缓冲。否则日志会攒到最后一次性吐出来，
            # 后台页面看起来像卡死，而实际在跑。
            self.proc = subprocess.Popen(
                [sys.executable, "-u"] + argv, cwd=HERE,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1)
        threading.Thread(target=self._pump, daemon=True).start()
        return True, None

    def _pump(self):
        p = self.proc
        for line in p.stdout:
            self.lines.append(line.rstrip("\n"))
        self.rc = p.wait()

    def stop(self):
        if self.running():
            self.proc.terminate()
            try:
                self.proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            return True
        return False


RUNNER = Runner()


# ---------------------------------------------------------------- 数据装配
def inventory_summary(path):
    if not os.path.exists(path):
        return {"exists": False, "path": path, "count": 0, "bad": [],
                "items": [], "cost_note": "库存文件不存在"}
    inv, bad = planner.load_inventory(path)
    from core import foods
    items = []
    for it in inv:
        items.append({
            "name": it["name"], "disp": foods.disp(it["key"]),
            "role": foods.FOODS[it["key"]]["role"],
            "edible_g": round(it["edible_g"]), "purchased_g": round(it["purchased_g"]),
            "days_left": it["days_left"], "tier": it["tier"],
            "tier_sym": foods.tier_sym(it["key"]), "price_kg": it["price_kg"],
            "place": it["place"], "note": it["note"],
        })
    items.sort(key=lambda x: (x["days_left"], x["role"]))
    return {"exists": True, "path": path, "count": len(inv),
            "bad": [{"line": i, "why": w} for i, w in bad], "items": items}


def _mask(v):
    """把密钥压成能辨认、但拼不回去的形状：ark-1f2e****4548（46 位）。
    首尾各留 4 位，足够核对「是不是把 ARK key 粘成了 ark-ark-…」，
    又不足以还原。长度也回，因为历史上踩过「粘重复前缀导致 401」的坑。"""
    v = (v or "").strip()
    if not v:
        return None
    if len(v) <= 10:
        return "****（%d 位）" % len(v)
    return "%s****%s（%d 位）" % (v[:4], v[-4:], len(v))


def env_status():
    """回「配没配」+ 脱敏预览，**不回完整值**。

    为什么不给完整值：这个服务只监听 127.0.0.1，但没有 CSRF 防护 ——
    本机任意网页都能向它发 POST。一旦提供「返回完整密钥」的接口，
    就等于给每个打开的网页开了一条读你 API Key 的路（DNS rebinding 同理）。
    脱敏预览能回答「配的是哪把钥匙、有没有粘错」，这已经够用；
    要看完整值请直接打开 .env（人和 run.py 才看它，见 core/settings.py 的注释）。
    """
    def has(*names):
        return all(bool((os.environ.get(n) or "").strip()) for n in names)

    cred = []
    for env_name, label in (
            ("ARK_API_KEY", "方舟生图 + 文案"),
            ("VOLC_SPEECH_APP_ID", "语音 App ID（旧版体系）"),
            ("VOLC_SPEECH_ACCESS_TOKEN", "语音 Access Token（旧版体系）"),
            ("VOLC_SPEECH_API_KEY", "语音 API Key（新版体系，UUID）"),
            ("DIET_CHAT_MODEL", "文案模型"),
            ("DIET_TTS_SPEAKER", "默认音色")):
        v = (os.environ.get(env_name) or "").strip()
        # 音色名和模型名不是密钥，原样显示才看得出选的是哪个
        plain = env_name in ("DIET_CHAT_MODEL", "DIET_TTS_SPEAKER")
        cred.append({"name": env_name, "label": label, "set": bool(v),
                     "value": (v if plain else _mask(v)) if v else None})

    from clients import speech_doubao
    mode = None
    try:
        c = speech_doubao.cred_from_env()
        mode = c["mode"] if c else None
    except Exception:
        pass
    return {
        "ark": has("ARK_API_KEY"),
        "tts_app": has("VOLC_SPEECH_APP_ID", "VOLC_SPEECH_ACCESS_TOKEN"),
        "tts_key": has("VOLC_SPEECH_API_KEY"),
        "tts_mode": mode,          # app=A 旧版 / key=B 新版 / None=未配
        "keys": cred,
        "env_file": os.path.join(HERE, ".env"),
    }


def bootstrap():
    cfg = settings.load()
    inv = settings.inventory_path(cfg)
    allr = recipe_mod.load()
    fac = recipe_mod.facets(allr)
    return {
        "config": cfg,
        "defaults": settings.DEFAULTS,
        "speakers": [{"id": i, "label": l} for i, l in settings.SPEAKERS],
        "categories": [{"id": c, "label": CATEGORY_LABEL.get(c, c),
                        "count": fac["category"].get(c, 0)}
                       for c in settings.ALLOWED_CATEGORIES],
        "slots": [{"id": s, "label": SLOT_LABEL[s]} for s in settings.ALLOWED_SLOTS],
        "cuisines": [{"id": k, "label": CUISINE_LABEL.get(k, k), "count": v}
                     for k, v in sorted(fac["cuisine"].items(), key=lambda x: -x[1])],
        "methods": [{"id": k, "label": k, "count": v}
                    for k, v in sorted(fac["method"].items(), key=lambda x: -x[1])],
        "flavors": [{"id": k, "label": k, "count": v}
                    for k, v in sorted(fac["flavor"].items(), key=lambda x: -x[1])],
        "recipe_total": len(allr),
        "inventory": inventory_summary(inv),
        "env": env_status(),
        "broken": settings.broken_note(),
        "runner": {"running": RUNNER.running(), "run_id": RUNNER.run_id,
                   "rc": RUNNER.rc},
    }


def preview(cfg=None):
    """不出图、不花钱的预演：跑一遍筛选与打分，把 Top3 和漏斗返回。"""
    cfg = cfg or settings.load()
    inv_path = settings.inventory_path(cfg)
    if not os.path.exists(inv_path):
        return {"error": f"库存文件不存在：{inv_path}"}
    inv, bad = planner.load_inventory(inv_path)
    if not inv:
        return {"error": "库存为空", "bad": [{"line": i, "why": w} for i, w in bad]}

    t0 = time.time()
    if cfg.get("mode") == "recipe":
        plan = recipe_mod.plan(inv, cfg)
    else:
        plan = planner.plan(inv, allergens=set(cfg.get("exclude") or []),
                            budget=float(cfg.get("budget") or 25))
    ms = round((time.time() - t0) * 1000)

    if "error" in plan:
        return {"error": plan["error"], "funnel": plan.get("funnel"),
                "hint": plan.get("hint"), "ms": ms}
    def slim(m):
        return {
            "name": m["name"], "short": m.get("short"), "score": m["score"],
            "parts": m["parts"], "cost": m["cost"], "facts": m["facts"],
            "minutes": m["minutes"], "tier_mix": m["tier_mix"],
            "flags": m.get("flags") or [],
            "items": [{"disp": i["disp"], "edible_g": i["edible_g"],
                       "role": i["role"], "cost": i["cost"],
                       "tier_sym": i["tier_sym"], "from": i.get("from"),
                       "swapped": i.get("swapped"),
                       "adjust": i.get("adjust")} for i in m["items"]],
            "swaps": m.get("swaps") or [], "missing": m.get("missing") or [],
            "fidelity": m.get("fidelity"), "uses_stock": m.get("uses_stock"),
            "category_label": m.get("category_label"),
            "cuisine": m.get("cuisine"), "methods": m.get("methods"),
            "flavors": m.get("flavors"), "desc": m.get("desc"),
            "steps": m.get("steps") or [],
            "recipe_kcal": m.get("recipe_kcal"),
            "emoji": m.get("emoji"),
        }
    return {"top3": [slim(m) for m in plan["top3"]], "best": slim(plan["best"]),
            "n": plan["n_combos"], "funnel": plan.get("funnel"),
            "over_budget": plan.get("over_budget"),
            "over_tier": plan.get("over_tier"),
            "budget": plan.get("budget"), "mode": plan.get("mode", cfg.get("mode")),
            "inventory_used": plan.get("inventory_used"), "ms": ms,
            "bad": [{"line": i, "why": w} for i, w in bad]}


def list_runs(limit=12):
    if not os.path.isdir(RUNS):
        return []
    out = []
    for name in sorted(os.listdir(RUNS), reverse=True):
        d = os.path.join(RUNS, name)
        if name.startswith("_") or not os.path.isdir(d):
            continue
        mf = os.path.join(d, "manifest.json")
        if not os.path.exists(mf):
            continue
        try:
            with open(mf, encoding="utf-8") as f:
                m = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        out.append({
            "run_id": name, "dish": (m.get("menu") or {}).get("name"),
            "date": m.get("date"), "slot": m.get("slot"),
            "mode": m.get("mode") or "combo",
            "cost": (m.get("cost") or {}).get("total"),
            "kcal": ((m.get("menu") or {}).get("facts") or {}).get("kcal"),
            "video": os.path.exists(os.path.join(d, "final.mp4")),
            "cover": os.path.exists(os.path.join(d, "cover.jpg")),
            "elapsed": m.get("elapsed_sec"),
            "generated_at": m.get("generated_at"),
            "degraded": bool((m.get("audio") or {}).get("degraded")),
        })
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "daily-diet/1.0"

    def log_message(self, fmt, *a):
        pass                                   # 静音，否则每个请求刷一行

    # ---- 工具 ----
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _file(self, root, rel):
        """把一个相对路径安全地映射到 root 下。用 realpath 判包含关系，
        而不是 startswith —— 前者能正确挡住 ../ 与符号链接。"""
        rel = rel.lstrip("/").replace("\\", "/")
        if not rel or ".." in rel.split("/"):
            return self._json({"error": "bad path"}, 400)
        full = os.path.realpath(os.path.join(root, rel))
        base = os.path.realpath(root)
        if not (full == base or full.startswith(base + os.sep)):
            return self._json({"error": "bad path"}, 400)
        if not os.path.isfile(full):
            return self._json({"error": "not found"}, 404)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        size = os.path.getsize(full)
        rng = self.headers.get("Range")
        # 视频要支持 Range，否则 <video> 拖不动进度条
        if rng and rng.startswith("bytes="):
            try:
                a, _, b = rng[6:].partition("-")
                start = int(a or 0)
                end = int(b) if b else size - 1
            except ValueError:
                start, end = 0, size - 1
            end = min(end, size - 1)
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(end - start + 1))
            self.end_headers()
            with open(full, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(65536, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        with open(full, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)

    # ---- 路由 ----
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        p = u.path

        if p in ("/", "/index.html"):
            return self._file(WEB, "index.html")
        if p.startswith("/static/"):
            return self._file(WEB, p[len("/static/"):])
        if p.startswith("/runs/"):
            return self._file(RUNS, p[len("/runs/"):])

        if p == "/api/bootstrap":
            return self._json(bootstrap())
        if p == "/api/preview":
            return self._json(preview())
        if p == "/api/runs":
            return self._json({"runs": list_runs()})
        if p == "/api/run":
            since = int((q.get("since") or ["0"])[0] or 0)
            with RUNNER.lock:
                chunk = RUNNER.lines[since:]
                total = len(RUNNER.lines)
            return self._json({
                "running": RUNNER.running(), "run_id": RUNNER.run_id,
                "rc": RUNNER.rc, "since": since, "total": total, "lines": chunk,
                "elapsed": round(time.time() - RUNNER.started, 1) if RUNNER.started else 0,
            })
        if p == "/api/recipe":
            rid = (q.get("id") or [""])[0]
            r = next((x for x in recipe_mod.load() if x["id"] == rid), None)
            return self._json(r or {"error": "not found"}, 200 if r else 404)
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/api/config":
            body = self._read_json()
            cfg, notes = settings.save(body.get("config") or body)
            return self._json({"ok": True, "config": cfg, "notes": notes})
        if u.path == "/api/preview":
            body = self._read_json()
            return self._json(preview(body.get("config") or body))
        if u.path == "/api/run":
            body = self._read_json()
            cfg = body.get("config")
            if cfg:
                cfg, _ = settings.save(cfg)
            else:
                cfg = settings.load()
            import datetime as dt
            run_id = body.get("run_id") or f"{dt.date.today().isoformat()}_{cfg['slot']}"
            argv = ["run.py"] + settings.run_args(cfg, run_id=run_id)
            ok, err = RUNNER.start(argv, run_id)
            if not ok:
                return self._json({"ok": False, "error": err}, 409)
            return self._json({"ok": True, "run_id": run_id, "argv": argv})
        if u.path == "/api/run/stop":
            return self._json({"ok": RUNNER.stop()})
        return self._json({"error": "not found"}, 404)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    # .env 里的密钥要在 env_status 之前进环境，否则后台会显示"未配置"
    try:
        from run import load_env
        load_env()
    except Exception:
        pass

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.daemon_threads = True
    url = f"http://{args.host}:{args.port}/"
    print(f"配置后台已启动：{url}")
    print("按 Ctrl+C 停止")
    if args.open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
