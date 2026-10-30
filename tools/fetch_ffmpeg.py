#!/usr/bin/env python3
"""把「带 libass 的 ffmpeg」装到本仓库的 vendor/ffmpeg/ 下。

为什么需要这一步：
    ffmpeg 的 libass 是编译期选项，部分发行版/包管理器的构建把 ass 滤镜裁掉了。
    缺它的时候字幕**不会报错**——成片照常输出、退出码 0，只是画面里没有字。
    所以部署完必须确认真的是带 ass 的那个 ffmpeg，而不是"系统里有个 ffmpeg"。

来源优先级：
    1. --src 指定（或环境变量 DIET_FFMPEG_SRC）
    2. 已有的 ~/.workbuddy/binaries/ffmpeg-libass/ffmpeg
    3. pip 装 imageio-ffmpeg，取它自带的静态构建（macOS/Linux/Windows 都有）

用法：
    python3 tools/fetch_ffmpeg.py           # 缺了才装，已可用则跳过
    python3 tools/fetch_ffmpeg.py --force   # 重新装
    python3 tools/fetch_ffmpeg.py --check   # 只检查，不下载
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR = os.path.join(ROOT, "vendor", "ffmpeg")
IS_WIN = os.name == "nt"
BIN = "ffmpeg.exe" if IS_WIN else "ffmpeg"


def has_ass(path):
    """这个 ffmpeg 二进制有没有 ass 滤镜。没有 grep -q，避免 SIGPIPE 干扰判断。"""
    try:
        p = subprocess.run([path, "-hide_banner", "-filters"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    for line in (p.stdout or "").splitlines():
        # 形如 "  T.. ass             V->V  Render ASS subtitles"
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "ass":
            return True
    return False


def system_ffmpeg():
    return shutil.which("ffmpeg")


def from_src(src):
    """用户指定的来源：可以是目录，也可以是二进制本身。"""
    if os.path.isdir(src):
        for name in (BIN, "ffmpeg"):
            p = os.path.join(src, name)
            if os.path.isfile(p):
                return p
        return None
    return src if os.path.isfile(src) else None


def from_pip():
    """imageio-ffmpeg 的 wheel 里带着跨平台静态构建，直接取出来用。"""
    tmp = tempfile.mkdtemp(prefix="diet-ffmpeg-")
    print(f"  pip 安装 imageio-ffmpeg 到临时目录 …")
    for extra in ([], ["--index-url", "https://pypi.tuna.tsinghua.edu.cn/simple"]):
        p = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet",
                            "--target", tmp, "imageio-ffmpeg"] + extra,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        if p.returncode == 0:
            break
    else:
        print("  pip 安装失败：", (p.stderr or "")[-400:])
        return None

    pats = ["imageio_ffmpeg/binaries/ffmpeg-*", "imageio_ffmpeg/binaries/ffmpeg*"]
    found = []
    for pat in pats:
        found += glob.glob(os.path.join(tmp, pat))
    found = [f for f in found if os.path.isfile(f)]
    if not found:
        print("  wheel 里没找到 ffmpeg 二进制")
        return None
    # 有些 wheel 同时带多架构，优先当前平台能执行且带 ass 的那个
    for f in sorted(found, key=lambda x: -os.path.getsize(x)):
        os.chmod(f, 0o755)
        if has_ass(f):
            return f
    return found[0]


def install(src):
    os.makedirs(VENDOR, exist_ok=True)
    dst = os.path.join(VENDOR, BIN)
    shutil.copy2(src, dst)
    os.chmod(dst, 0o755)

    # ffprobe：imageio 的构建不带它。系统里有就软链过来，让 doctor 与外部命令都能用；
    # 没有也不影响出片 —— core/render.py 探时长用的是 ffmpeg -i，不依赖 ffprobe。
    probe_src = shutil.which("ffprobe")
    probe_dst = os.path.join(VENDOR, "ffprobe.exe" if IS_WIN else "ffprobe")
    if probe_src and not os.path.exists(probe_dst):
        try:
            shutil.copy2(probe_src, probe_dst)
            os.chmod(probe_dst, 0o755)
        except OSError:
            pass
    return dst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.environ.get("DIET_FFMPEG_SRC"),
                    help="已有的 ffmpeg 二进制或所在目录")
    ap.add_argument("--force", action="store_true", help="即使已可用也重新安装")
    ap.add_argument("--check", action="store_true", help="只检查，不下载")
    args = ap.parse_args()

    vendored = os.path.join(VENDOR, BIN)
    if os.path.isfile(vendored) and not args.force:
        if has_ass(vendored):
            print(f"[ OK ] vendor/ffmpeg/{BIN} 已就绪，带 ass 滤镜")
            return 0
        print(f"[!] vendor/ffmpeg/{BIN} 存在但缺 ass 滤镜，重新安装")

    sys_ff = system_ffmpeg()
    if sys_ff and has_ass(sys_ff) and not args.force and not args.check:
        print(f"[ OK ] 系统的 ffmpeg 本来就带 ass 滤镜：{sys_ff}")
        print("       不必再下载。脚本会优先用 vendor/，其次用它。")
        return 0

    if args.check:
        state = f"系统 ffmpeg={'有' if sys_ff else '无'}"
        if sys_ff:
            state += f"，ass 滤镜={'有' if has_ass(sys_ff) else '无'}"
        print(f"[--] vendor 未就绪；{state}")
        print("     运行 python3 tools/fetch_ffmpeg.py 来安装")
        return 1

    print("==> 找一个带 libass 的 ffmpeg")
    cand = None
    if args.src:
        cand = from_src(args.src)
        print(f"  --src：{args.src} → {cand or '没找到'}")
    if not cand:
        legacy = os.path.expanduser("~/.workbuddy/binaries/ffmpeg-libass/" + BIN)
        if os.path.isfile(legacy):
            print(f"  复用已有构建：{legacy}")
            cand = legacy
    if not cand:
        cand = from_pip()

    if not cand:
        print("\n[FAIL] 没拿到可用的 ffmpeg。两个办法：")
        print("  1) 手动下载带 libass 的构建，然后：")
        print("     python3 tools/fetch_ffmpeg.py --src /下载目录")
        print("  2) 装一个自带 libass 的系统 ffmpeg（apt/choco/brew 完整构建）")
        return 1

    if not has_ass(cand):
        print(f"\n[FAIL] {cand} 也没有 ass 滤镜，换一个来源再试")
        return 1

    dst = install(cand)
    print(f"\n[ OK ] 已安装 {dst}（带 ass 滤镜）")
    print("       接下来：python3 tools/doctor.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
