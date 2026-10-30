#!/usr/bin/env python3
"""移植/换机后的环境体检 —— 移植到新设备后第一件事就跑它。

    python tools/doctor.py

检查的是「换一台机器最容易坏、坏了又不报错」的那几项：
  1. Python 版本（需要 3.8+，零第三方依赖）
  2. ffmpeg / ffprobe 存在（路径 + 版本）
  3. **中文字体**：ASS 里指定的字体系统里到底有没有。
     这一条最阴 —— libass 找不到字体时静默回退 Arial，中文变方块，
     成片照常输出、退出码 0，不体检根本发现不了。
  4. libass 滤镜是否编译进 ffmpeg（没有 ass 滤镜就烧不了字幕）
  5. 目录可写（runs/ cache/）
  6. 凭据是否配置（只报有无，不打印值）
  7. 数据文件是否齐全

任何一项失败都会给「怎么修」，而不是只说 FAIL。
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

OK, WARN, BAD = "OK", "WARN", "FAIL"
_rows = []


def check(name, status, detail="", fix=""):
    _rows.append((name, status, detail, fix))
    icon = {OK: "[ OK ]", WARN: "[WARN]", BAD: "[FAIL]"}[status]
    print(f"{icon} {name}" + (f"\n       {detail}" if detail else ""))
    if fix and status != OK:
        print(f"       → {fix}")


def python_version():
    v = sys.version_info
    check("Python 版本", OK if v >= (3, 8) else BAD,
          f"{v.major}.{v.minor}.{v.micro}  ({sys.executable})",
          "需要 Python 3.8 或更高")


def _run(cmd, **kw):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", **kw)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, ""


def ffmpeg_tools():
    for tool in ("ffmpeg", "ffprobe"):
        path = shutil.which(tool)
        if not path:
            check(tool, BAD, "不在 PATH 里",
                  "Windows: choco install ffmpeg / 或把 ffmpeg\\bin 加进 PATH；"
                  "macOS: brew install ffmpeg；Linux: apt install ffmpeg")
            continue
        _, out = _run([tool, "-version"])
        ver = next((l for l in out.splitlines() if "version" in l), "")[:60]
        check(tool, OK, f"{path}\n       {ver.strip()}")


def libass_filter():
    code, out = _run(["ffmpeg", "-hide_banner", "-filters"])
    if code == 127:
        check("libass（烧字幕）", BAD, "ffmpeg 不可用，无法检查")
        return False
    has = any(line.split()[1:2] == ["ass"] for line in out.splitlines()
              if len(line.split()) > 2)
    check("libass（烧字幕）", OK if has else BAD,
          "ffmpeg 已编译 ass 滤镜" if has else "ffmpeg 没有 ass 滤镜",
          "换一个带 --enable-libass 的 ffmpeg 构建")
    return has


def load_env():
    """把 .env 读进环境变量。

    必须在检查前调用：`DIET_SUB_FONT` 就写在 .env 里，不读的话体检会拿默认的
    Microsoft YaHei 去测，于是明明已经配好字体、却报一条假的 [FAIL]。
    这里不 import run.load_env，是为了让 doctor 保持零依赖、能单独复制到新机器上跑。
    """
    path = os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def chinese_font():
    """关键一项：ASS 里写的字体名，系统里有没有。

    做法不是「枚举系统字体」（跨平台没有统一办法），而是让 libass 自己说：
    渲染一帧极小的 ASS，看它的 fontselect 日志把请求的字体解析成了什么。
    解析结果不是原字体名 → 说明回退到了别的字体，中文可能变方块。
    """
    from core import subs

    want = (os.environ.get("DIET_SUB_FONT") or "").strip() or subs.DEFAULT_FONT
    hints = subs.FONT_HINTS.get(sys.platform, subs.FONT_HINTS["linux"])

    tmpdir = os.path.join(ROOT, "runs", "_doctor")
    os.makedirs(tmpdir, exist_ok=True)
    ass_name = "probe.ass"
    png_name = "probe.png"
    # ⚠ ass 滤镜的参数解析器把 `\` 和 `:` 当选项分隔符，Windows 绝对路径传进去会报
    #   "Error applying option 'original_size' to filter 'ass'"。
    # 所以下面必须 cwd=tmpdir 并用相对文件名，不能传绝对路径。
    with open(os.path.join(tmpdir, ass_name), "w", encoding="utf-8") as f:
        f.write(subs.HEADER.format(
            w=1080, h=1920, font=want, main_size=70, title_size=46,
            badge_size=32, stat_size=54, main_outline=12, title_outline=4,
            title_margin_v=56, margin_v=300,
            white="&H00FFFFFF", dim="&H00F2F2F2", ink="&H000A0A0A",
            box="&H66000000"))
        f.write("[Events]\nFormat: Layer, Start, End, Style, Name, "
                "MarginL, MarginR, MarginV, Effect, Text\n")
        f.write("Dialogue: 0,0:00:00.00,0:00:10.00,Main,,0,0,0,,中文体检\n")

    code, out = _run([
        "ffmpeg", "-y", "-loglevel", "info",
        "-f", "lavfi", "-i", "color=c=black:s=320x240:d=4",
        "-ss", "1", "-vf", f"ass={ass_name}", "-frames:v", "1",
        "-update", "1", png_name], cwd=tmpdir)
    if code == 127:
        check("中文字体", BAD, "ffmpeg 不可用，无法检查")
        return
    sel = [l for l in out.splitlines() if "fontselect" in l]
    if not sel:
        check("中文字体", WARN, f"没拿到 fontselect 日志（请求字体 {want}）",
              "手动确认系统里装了中文字体")
        return
    line = sel[0]
    # libass 的日志长这样：
    #   fontselect: (Microsoft YaHei, 400, 0) -> /path/to/font.ttc, 0, MicrosoftYaHei-Bold
    # 箭头后面第一个字段是**文件路径**，最后一个字段才是家族名。早期版本取第一个
    # 逗号前的内容，于是拿到的是路径，任何字体都会被判成"被回退"（假 FAIL）。
    tail = line.split("->")[-1] if "->" in line else ""
    fields = [x.strip() for x in tail.split(",") if x.strip()]
    if len(fields) >= 2:
        resolved = fields[-1]
    elif fields:
        resolved = os.path.basename(fields[0])
    else:
        resolved = "?"

    # 家族名与请求名不一致 → 说明回退了；Arial 系是 libass 回退时的典型落点
    same = resolved.replace(" ", "").lower().startswith(
        want.replace(" ", "").lower()[:6])
    if same:
        check("中文字体", OK, f"请求 {want} → 实际 {resolved}")
    elif resolved.split(",")[0].strip().lower().startswith("arial"):
        check("中文字体", BAD,
              f"请求 {want} → **被回退到 {resolved}**，中文会显示成方块",
              f"装中文字体，或设 DIET_SUB_FONT 为本机已有的字体名。"
              f"本平台常见：{' / '.join(hints)}")
    else:
        # 解析到的既不是请求名也不是 Arial：多数情况是家族别名（如 PingFang.ttc
        # 里的 PingFangSC-Regular），实际能用。给 WARN 而不是 BAD，并提示抽帧确认。
        check("中文字体", WARN,
              f"请求 {want} → 实际解析到 {resolved}（同名变体，通常可用）",
              "想确认就抽一帧看成片："
              "ffmpeg -ss 2 -i runs/<id>/final.mp4 -frames:v 1 -update 1 chk.jpg")


def writable_dirs():
    for d in (os.path.join(ROOT, "runs"), os.path.join(ROOT, "cache")):
        os.makedirs(d, exist_ok=True)
        probe = os.path.join(d, ".doctor_write_test")
        try:
            with open(probe, "w") as f:
                f.write("x")
            os.remove(probe)
            check(f"目录可写 {os.path.relpath(d, ROOT)}/", OK)
        except OSError as e:
            check(f"目录可写 {os.path.relpath(d, ROOT)}/", BAD, str(e),
                  "检查目录权限，或整个项目复制到有写权限的位置")


def credentials():
    env_path = os.path.join(ROOT, ".env")
    if not os.path.exists(env_path):
        check(".env 存在", BAD, "没有 .env 文件",
              "把 .env.example 复制成 .env 再填密钥")
    else:
        check(".env 存在", OK, env_path)

    try:
        from run import load_env
        load_env()
    except Exception as e:
        check("读取 .env", WARN, f"{type(e).__name__}: {e}")

    ark = (os.environ.get("ARK_API_KEY") or "").strip()
    if not ark:
        check("ARK_API_KEY", BAD, "未配置 —— 无法生图",
              "方舟控制台 → API Key，粘贴完整值（本身含 ark- 前缀）")
    elif ark.count("ark-") > 1 or ark.startswith("ark-ark-"):
        check("ARK_API_KEY", BAD, f"疑似重复前缀：{ark[:12]}…",
              "控制台复制的 key 已含 ark-，不要再手打一遍（会报 401）")
    else:
        check("ARK_API_KEY", OK, f"已配置（{len(ark)} 位）")

    try:
        from clients import speech_doubao
        cred = speech_doubao.cred_from_env()
    except Exception:
        cred = None
    if not cred:
        check("语音凭据", WARN, "未配置 —— 将使用占位音轨",
              "配 VOLC_SPEECH_APP_ID + VOLC_SPEECH_ACCESS_TOKEN（旧版），"
              "或 VOLC_SPEECH_API_KEY（新版 UUID）")
    else:
        check("语音凭据", OK,
              "体系 A（APP ID + Token）" if cred["mode"] == "app"
              else "体系 B（API Key）")


def data_files():
    need = [("data/ingredients.csv", "食材库"),
            ("data/recipes.json", "菜谱库"),
            ("data/price_ref.csv", "价格表")]
    for rel, label in need:
        p = os.path.join(ROOT, rel)
        if os.path.exists(p):
            kb = os.path.getsize(p) / 1024
            check(f"{label} {rel}", OK, f"{kb:.0f} KB")
        else:
            check(f"{label} {rel}", BAD, "文件缺失",
                  "从原项目一起复制 data/ 目录")
    inv = os.path.join(ROOT, "data", "01_食材清单_U001.csv")
    check("库存清单", OK if os.path.exists(inv) else WARN,
          inv if os.path.exists(inv) else "未找到 U001 的清单",
          "用 tools/make_inventory.py 生成，或自己按 README 第四节填")


def main():
    load_env()          # 必须在检查之前：DIET_SUB_FONT 来自 .env
    print("=" * 64)
    print(f"每日饮食 · 环境体检   平台 {sys.platform}")
    print(f"项目目录 {ROOT}")
    print("=" * 64)
    print()
    python_version()
    ffmpeg_tools()
    libass_filter()
    chinese_font()
    writable_dirs()
    credentials()
    data_files()

    bad = sum(1 for r in _rows if r[1] == BAD)
    warn = sum(1 for r in _rows if r[1] == WARN)
    print()
    print("=" * 64)
    if bad:
        print(f"结论：{bad} 项必须修复，{warn} 项建议关注 —— 修完再出片")
    elif warn:
        print(f"结论：可用（{warn} 项警告不影响出片，但会影响效果）")
    else:
        print("结论：环境完全就绪，可以直接 python run.py --user U001 --slot dinner")
    print("=" * 64)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
