# -*- coding: utf-8 -*-
"""
把待生效的库存清单（*.new）覆盖到正式清单文件。

为什么需要这个脚本：正式清单常被百度网盘/OneDrive 等同步客户端独占打开，
普通写入会报 PermissionError。关掉同步客户端后跑一次即可：

    python tools/apply_inventory.py

也可以指定目标：
    python tools/apply_inventory.py --src data/xx.csv.new --dst data/01_食材清单_U001.csv

默认会重试 --tries 次、每次间隔 --interval 秒，直到成功或放弃。
覆盖前会先备份原文件为 *.bak（若已有同名备份则加时间戳，不覆盖旧备份）。
"""
import argparse
import os
import shutil
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")


def log(msg):
    print(msg, flush=True)


def readable(path):
    try:
        with open(path, "rb") as f:
            f.read(1)
        return True
    except OSError as e:
        log(f"  读不了 {os.path.basename(path)}：{e.strerror}")
        return False


def writable(path):
    """真正试开一次写权限，比 os.access 可靠（Windows 上只读标记与占用都拦得住）。"""
    if not os.path.exists(path):
        return True  # 不存在就直接建，不拦
    try:
        with open(path, "a", encoding="utf-8-sig"):
            pass
        return True
    except PermissionError:
        return False
    except OSError:
        return False


def backup(dst):
    if not os.path.exists(dst):
        return None
    base = dst + ".bak"
    target = base
    if os.path.exists(base):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = f"{dst}.bak.{stamp}"
    try:
        shutil.copy2(dst, target)
        log(f"  已备份原文件 → {os.path.basename(target)}")
        return target
    except OSError as e:
        log(f"  备份失败（继续尝试覆盖）：{e}")
        return None


def apply_once(src, dst):
    data = open(src, encoding="utf-8-sig").read()
    with open(dst, "w", encoding="utf-8-sig", newline="") as f:
        f.write(data)
    return len(data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.path.join(DATA, "01_食材清单_U001.csv.new"))
    ap.add_argument("--dst", default=os.path.join(DATA, "01_食材清单_U001.csv"))
    ap.add_argument("--tries", type=int, default=15)
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--no-backup", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(args.src):
        log(f"找不到待覆盖文件：{args.src}")
        return 1
    if not readable(args.src):
        return 1

    n = sum(1 for _ in open(args.src, encoding="utf-8-sig")) - 1
    log(f"待覆盖：{os.path.basename(args.src)}（{n} 条数据行）")
    log(f"目标：{os.path.basename(args.dst)}")

    ok = False
    for i in range(1, args.tries + 1):
        if not writable(args.dst):
            log(f"第 {i}/{args.tries} 次：目标文件被占用")
            if i == 1:
                log("  提示：多由百度网盘 / OneDrive 同步客户端持有。"
                    "右键托盘图标退出，或在同步设置里暂停该目录，然后重跑本脚本。")
            if i < args.tries:
                time.sleep(args.interval)
            continue
        try:
            if not args.no_backup:
                backup(args.dst)
            size = apply_once(args.src, args.dst)
            log(f"第 {i}/{args.tries} 次：写入成功（{size} 字节）")
            ok = True
            break
        except PermissionError:
            log(f"第 {i}/{args.tries} 次：写入被拒，重试")
            time.sleep(args.interval)

    if not ok:
        log("")
        log("仍然失败。文件正被别的程序独占，请按顺序试：")
        log("  1) 退出百度网盘（托盘图标 → 退出），再跑一次本脚本；")
        log("  2) 或在资源管理器里把 .new 手动改名去掉后缀覆盖。")
        return 2

    # 覆盖成功后清掉 .new；失败不影响结果
    try:
        os.remove(args.src)
        log(f"  已清理 {os.path.basename(args.src)}")
    except OSError:
        log(f"  注意：{os.path.basename(args.src)} 未能删除，可手动删掉")

    # 复核
    body = open(args.dst, encoding="utf-8-sig").read().strip().splitlines()
    log(f"复核：{os.path.basename(args.dst)} 现有 {len(body) - 1} 条数据行")
    log(f"  表头：{body[0]}")
    log(f"  首行：{body[1] if len(body) > 1 else '(空)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
