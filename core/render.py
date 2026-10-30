#!/usr/bin/env python3
"""合成：图和音在时间轴上对齐成 1080x1920 / 30fps / H.264 成片。

运镜用 zoompan 的 `d=1` 逐帧累积写法，配合 `-loop 1 -framerate 30 -t dur`，
好处是时长由 -t 精确控制，不会像 d=N 那样因为取整多出几帧导致接缝错位。

转场用 xfade 串起来而不是先各自加淡化再拼接 —— 后者会黑闪一下，
在竖屏短视频里非常显眼。
"""
import os
import subprocess

CW, CH = 1080, 1920
FPS = 30
CRF = 19


def run(cmd, cwd=None):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败({p.returncode}): {cmd[0]} …\n{p.stderr[-1200:]}")
    return p


def conform(src, dst, w=CW, h=CH):
    """任何尺寸的图 → 精确 w x h。比例已是 9:16 时不裁切，只缩放。"""
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", src,
         "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,"
                f"crop={w}:{h},setsar=1",
         "-frames:v", "1", dst])
    return dst


def _zoompan(motion, dur, w=CW, h=CH):
    big_w, big_h = int(w * 1.5), int(h * 1.5)
    nf = max(2, int(round(dur * FPS)))
    pre = (f"scale={big_w}:{big_h}:force_original_aspect_ratio=increase,"
           f"crop={big_w}:{big_h},setsar=1")
    cx = "iw/2-(iw/zoom/2)"
    # 缩放速率按帧数反推，保证在镜头结束时**刚好**推到 1.16。
    # 写死固定增量会有两个问题：短镜头没推够、长镜头早早到顶然后静止不动。
    step = 0.16 / nf
    if motion == "zoom_out":
        z, x, y = f"max(1.16-{step:.6f}*on,1.0)", cx, "ih/2-(ih/zoom/2)"
    elif motion == "pan_down":
        z, x, y = "1.10", cx, f"(ih-ih/zoom)*min(1\\,on/{nf})"
    elif motion == "pan_up":
        z, x, y = "1.10", cx, f"(ih-ih/zoom)*(1-min(1\\,on/{nf}))"
    else:                                     # zoom_in
        z, x, y = f"min(1.0+{step:.6f}*on,1.16)", cx, "ih/2-(ih/zoom/2)"
    return f"{pre},zoompan=z='{z}':x='{x}':y='{y}':d=1:s={w}x{h}:fps={FPS},setsar=1"


def clip(img, dur, motion, dst):
    run(["ffmpeg", "-y", "-loglevel", "error",
         "-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", img,
         "-vf", _zoompan(motion, dur),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
         "-pix_fmt", "yuv420p", "-r", str(FPS), dst])
    return dst


def compose(clips, durs, ass_name, audio, out, workdir, transition=0.5):
    """clips/durs 顺序即时间轴顺序；ass_name 相对 workdir。"""
    # cwd 会切到 workdir，所以所有输入路径必须绝对化，否则相对路径会找不到
    clips = [os.path.abspath(c) for c in clips]
    audio, out = os.path.abspath(audio), os.path.abspath(out)
    fc, cur, acc = [], "[0:v]", 0.0
    for i in range(1, len(clips)):
        acc += durs[i - 1]
        off = round(acc - transition * i, 3)
        nxt = f"[v{i}]"
        fc.append(f"{cur}[{i}:v]xfade=transition=fade:duration={transition}:"
                  f"offset={off}{nxt}")
        cur = nxt
    fc.append(f"{cur}ass={ass_name}[vout]")

    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for c in clips:
        cmd += ["-i", c]
    cmd += ["-i", audio, "-filter_complex", ";".join(fc),
            "-map", "[vout]", "-map", f"{len(clips)}:a",
            "-c:v", "libx264", "-preset", "medium", "-crf", str(CRF),
            "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
            "-shortest", "-movflags", "+faststart", out]
    run(cmd, cwd=workdir)
    return out


def cover(video, t, dst):
    run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", video,
         "-frames:v", "1", "-q:v", "2", dst])
    return dst


def probe(path):
    """零依赖探时长：用 ffmpeg -i 的 stderr 解析 Duration。"""
    p = subprocess.run(["ffmpeg", "-i", path], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    for line in p.stderr.splitlines():
        if "Duration:" in line:
            t = line.split("Duration:")[1].split(",")[0].strip()
            h, m, s = t.split(":")
            return round(int(h) * 3600 + int(m) * 60 + float(s), 3)
    return None
