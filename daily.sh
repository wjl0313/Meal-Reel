#!/bin/bash
# 每日饮食 · 一键出片
# 用途：给定时任务/手动调用一个稳定入口。负责三件事——
#   1) 把带 libass 的 ffmpeg 放到 PATH 最前（系统自带的 ffmpeg 缺 ass 滤镜，字幕会烧不进去）
#   2) 调 run.py 出片，日志落到 runs/_logs/
#   3) 成功后最后一行打印 FINAL=<成片绝对路径>，供后续发送步骤读取
#
# 用法：
#   ./daily.sh                      # 默认 --user U001 --slot dinner
#   ./daily.sh --slot breakfast     # 参数原样透传给 run.py
#   ./daily.sh --reuse-images       # 复用图片缓存（不花钱，用于验证链路）

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT" || exit 1

# 自动发送：出片成功后直接把成片发到企业微信，失败则发告警。
# 默认开启——手动跑这一条也会送达，不需要另外再敲一次发送命令。
# 被 daily3.sh 调用时会带上 DIET_NOTIFY=0，改由三餐那层统一发一条，避免每餐刷屏。
# shellcheck source=lib/notify.sh
. "$ROOT/lib/notify.sh"

# 字幕烧制依赖 libass。查找顺序（vendor/ffmpeg → ~/.workbuddy/… → 系统 PATH）
# 见 lib/ffmpeg.sh；缺 ass 滤镜时必须在这里拦住，否则会静默出一版没字幕的片子。
# shellcheck source=lib/ffmpeg.sh
. "$ROOT/lib/ffmpeg.sh"
diet_use_ffmpeg

if ! diet_ass_ok; then
  echo "ERROR: 当前 ffmpeg 不支持 ass 滤镜，字幕无法烧入画面" >&2
  diet_ffmpeg_hint
  exit 10
fi

PY="$(command -v python3 || command -v python)"
if [ -z "$PY" ]; then
  echo "ERROR: 找不到 python3" >&2
  exit 11
fi

# 默认参数；用户传了就整套以用户为准
if [ "$#" -eq 0 ]; then
  set -- --user U001 --slot dinner
fi

DATE="$(date +%F)"
LOG_DIR="$ROOT/runs/_logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/$DATE.log"

echo "==> 开始出片 $DATE $(date +%H:%M:%S)"
echo "==> 日志 $LOG"

"$PY" run.py "$@" 2>&1 | tee -a "$LOG"
STATUS="${PIPESTATUS[0]}"

if [ "$STATUS" -ne 0 ]; then
  echo "ERROR: 出片失败，退出码 ${STATUS}（完整日志见 ${LOG}）" >&2
  if diet_notify_on; then
    diet_alert "出片（run.py）" "$STATUS" "$LOG" || true
  fi
  exit "$STATUS"
fi

# 从本次日志反查产物目录，避免靠猜目录名
OUT_DIR="$("$PY" - "$LOG" <<'PYEOF'
import re, sys
log = open(sys.argv[1], encoding="utf-8", errors="replace").read()
m = re.findall(r"产出目录\s+(\S+)", log)
print(m[-1] if m else "")
PYEOF
)"

if [ -z "$OUT_DIR" ] || [ ! -f "$OUT_DIR/final.mp4" ]; then
  echo "ERROR: 未找到成片 final.mp4（产物目录：${OUT_DIR:-未知}）" >&2
  exit 12
fi

echo "FINAL=$OUT_DIR/final.mp4"

# ---- 自动发送 ----
# 注意：失败只告警、不改退出码——成片已经生成，不能因为发送失败把一次成功的出片
# 判成失败，否则上层（daily3.sh）会误以为这一餐没出片。
if diet_notify_on; then
  SLOT="dinner"
  _prev=""
  for _a in "$@"; do
    [ "$_prev" = "--slot" ] && SLOT="$_a"
    _prev="$_a"
  done
  case "$SLOT" in
    breakfast|lunch|dinner) ;;
    *) SLOT="dinner" ;;
  esac
  echo ""
  echo "==> 自动发送${SLOT}成片"
  if ! ./send_slot.sh "$SLOT"; then
    echo "WARN: 自动发送失败，成片仍在 ${OUT_DIR}/final.mp4" >&2
  fi
fi

exit 0
