#!/usr/bin/env python3
"""配置的唯一真相源：config.json。

为什么不让前端直接改 .env：
  .env 里混着密钥（ARK_API_KEY / 语音 Token），前端一旦能写它，
  就有把密钥读出来或者写坏的机会。所以两件事分开：
    .env       只放密钥，只读，只有人和 run.py 看
    config.json 只放参数，可读写，后台页面随便改

读取顺序：内置默认 → config.json → 环境变量（仅允许的少数几项）。
环境变量放最后，是为了让 CI/命令行能临时覆盖而不改动文件。

**所有数值都要夹在合理区间**：后台是给人手填的，填 0 或 9999 都不该让出片炸掉，
应该退回到一个能跑的值并在返回里说明被夹过。
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "config.json")

# 音色候选。冒号后面是中文说明，选的时候看得懂才选得对。
# 只列实测存在的一批；想用别的直接在后台「自定义音色」里填 ID。
SPEAKERS = [
    ("zh_female_xiaohe_uranus_bigtts", "小何 · 女声，偏年轻自然（默认）"),
    ("zh_female_vv_uranus_bigtts", "VV · 女声，语气更软"),
    ("zh_female_shuangkuaisisi_emo_v2_mars_bigtts", "爽快思思 · 女声，语速偏快"),
    ("zh_male_yangguangqingnian_mars_bigtts", "阳光青年 · 男声，明亮"),
    ("zh_male_jingqiangkanye_mars_bigtts", "京腔侃爷 · 男声，评书感"),
    ("zh_male_ahu_conversation_wvae_bigtts", "阿虎 · 男声，对话感"),
    ("zh_female_wanwanxiaohe_moon_bigtts", "湾湾小何 · 女声，口音软"),
]

DEFAULTS = {
    "mode": "recipe",                     # recipe=真实菜谱 + 平替 / combo=从库存组合
    "categories": ["home"],               # 菜谱类型（recipe 模式生效）
    # 评分用哪套营养目标。空 = 跟着每道菜自己的分类走。
    # 混选「减脂 + 家常」时填 fat-loss，否则家常菜会按家常的 450-800 千卡拿满分。
    "target_category": "",
    "meal_types": ["dinner"],
    "cuisines": [],                       # 空 = 不限
    "methods": [],
    "flavors": [],
    "max_minutes": 40,
    "budget": 25,
    # 不重样：同一道菜多少天内不再出现。0 = 不限制（只保证早/午/晚不撞）。
    # 实际能撑多久取决于池子大小：减脂早餐只有个位数候选，别指望一个月不重样。
    "repeat_cooldown_days": 60,
    "pick": "random",                     # best=永远最高分（可复现）/ random=头部候选随机
    "pick_pool": 0,                       # 随机池大小，0=自动（至少 5 道、最多 40 道）
    "exclude": [],                        # 忌口/过敏原
    "user": "U001",
    "slot": "dinner",
    "inventory": "",                       # 空 = data/01_食材清单_<user>.csv
    "tts": {
        "engine": "auto",                  # auto / doubao / mock
        "speaker": "zh_female_xiaohe_uranus_bigtts",
        "speed": 1.0,
        "resource_id": "seed-tts-2.0",
    },
    "video": {
        "reuse_images": False,
        "transition": 0.3,
    },
    "subs": {
        "main_size": 70,
        "title_size": 46,
        "stat_size": 54,
        "margin_v": 300,
        "box_alpha": "66",
        "wrap": 13,
    },
    "chat": False,
    "chat_model": "doubao-seed-2-1-pro-260915",
}

# 环境变量覆盖白名单：只允许这几个，避免任意 env 影响出片结果
ENV_MAP = {
    "DIET_BUDGET_CNY": ("budget", float),
    "DIET_TTS_SPEAKER": ("tts.speaker", str),
    "DIET_TTS_RESOURCE_ID": ("tts.resource_id", str),
}

# (键, 下限, 上限)
CLAMPS = [
    ("budget", 3, 500),
    ("max_minutes", 5, 180),
    ("repeat_cooldown_days", 0, 365),
    ("pick_pool", 0, 200),
    ("tts.speed", 0.5, 2.0),
    ("video.transition", 0.0, 1.0),
    ("subs.main_size", 30, 120),
    ("subs.title_size", 24, 90),
    ("subs.stat_size", 24, 90),
    ("subs.margin_v", 60, 900),
    ("subs.wrap", 6.0, 22.0),
]

ALLOWED_CATEGORIES = ["fat-loss", "muscle-gain", "maintain", "nutrition",
                      "home", "therapy", "bakery", "snack"]
ALLOWED_SLOTS = ["breakfast", "lunch", "dinner"]


def _dig(d, path):
    cur = d
    for p in path.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


def _put(d, path, val):
    parts = path.split(".")
    cur = d
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = val


def _merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if k not in base:
            continue                      # 不认识的键直接丢，防止前端塞脏数据
        if isinstance(base[k], dict) and isinstance(v, dict):
            out[k] = _merge(base[k], v)
        else:
            out[k] = v
    return out


def _clamp(cfg):
    notes = []
    for path, lo, hi in CLAMPS:
        v = _dig(cfg, path)
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            _put(cfg, path, _dig(DEFAULTS, path))
            notes.append(f"{path} 不是数字，已用默认值")
            continue
        c = max(lo, min(hi, f))
        if abs(c - f) > 1e-9:
            notes.append(f"{path} 超出 {lo}–{hi}，已夹到 {c}")
        _put(cfg, path, int(c) if isinstance(_dig(DEFAULTS, path), int) else c)

    if cfg.get("mode") not in ("recipe", "combo"):
        cfg["mode"] = "recipe"
    if cfg.get("pick") not in ("best", "random"):
        cfg["pick"] = "random"
    cats = [c for c in (cfg.get("categories") or []) if c in ALLOWED_CATEGORIES]
    cfg["categories"] = cats or ["home"]
    tc = cfg.get("target_category") or ""
    cfg["target_category"] = tc if tc in ALLOWED_CATEGORIES else ""
    slots = [s for s in (cfg.get("meal_types") or []) if s in ALLOWED_SLOTS]
    cfg["meal_types"] = slots or ["dinner"]
    if cfg.get("slot") not in ALLOWED_SLOTS:
        cfg["slot"] = "dinner"
    if cfg.get("tts", {}).get("engine") not in ("auto", "doubao", "mock"):
        cfg.setdefault("tts", {})["engine"] = "auto"
    if not cfg.get("user"):
        cfg["user"] = "U001"
    # 忌口去重去空
    cfg["exclude"] = sorted({str(x).strip() for x in (cfg.get("exclude") or [])
                             if str(x).strip()})
    return cfg, notes


def load(path=None, use_env=True):
    path = path or PATH
    raw = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            # 配置坏了不能让整个工具起不来，退回默认并在 notes 里说明
            raw = {}
            _BROKEN[path] = f"{type(e).__name__}: {e}"
    cfg = _merge(DEFAULTS, raw)
    if use_env:
        for env, (key, cast) in ENV_MAP.items():
            v = os.environ.get(env)
            if v:
                try:
                    _put(cfg, key, cast(v))
                except ValueError:
                    pass
    cfg, _ = _clamp(cfg)
    return cfg


_BROKEN = {}


def broken_note(path=None):
    return _BROKEN.get(path or PATH)


def save(cfg, path=None):
    """写盘。先写临时文件再替换 —— 后台点保存时 run.py 可能正在读。"""
    path = path or PATH
    cfg, notes = _clamp(_merge(DEFAULTS, cfg))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return cfg, notes


def inventory_path(cfg):
    p = (cfg.get("inventory") or "").strip()
    if p:
        return p if os.path.isabs(p) else os.path.join(ROOT, p)
    return os.path.join(ROOT, "data", f"01_食材清单_{cfg.get('user', 'U001')}.csv")


def run_args(cfg, date=None, run_id=None):
    """把配置翻译成 run.py 的命令行参数。放这里是为了后台和命令行同一套逻辑。"""
    args = ["--user", cfg.get("user") or "U001",
            "--slot", cfg.get("slot") or "dinner",
            "--mode", cfg.get("mode") or "recipe",
            "--budget", str(cfg.get("budget") or 25),
            "--tts", (cfg.get("tts") or {}).get("engine") or "auto"]
    # 后台的餐次是多选（meal_types），而出片只有一餐（slot）。
    # 多选时必须把整个集合显式传下去，否则 run.py 只能拿 --slot 单餐去筛，
    # 「早餐+午餐」会被悄悄窄化成只剩一个餐次。
    mts = [m for m in (cfg.get("meal_types") or []) if m]
    if len(mts) > 1:
        args += ["--meal-types", ",".join(mts)]
    if date:
        args += ["--date", date]
    if run_id:
        args += ["--run-id", run_id]
    inv = inventory_path(cfg)
    if inv and os.path.exists(inv):
        args += ["--inventory", inv]
    if (cfg.get("video") or {}).get("reuse_images"):
        args.append("--reuse-images")
    if cfg.get("chat"):
        args.append("--chat")
    return args
