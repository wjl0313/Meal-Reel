#!/bin/bash
# 每日饮食 · 一天三餐一次出片
# 早上 7 点跑这一条：早/午/晚各出一道菜（强制不重样），再拼成一条竖屏视频，方便一次性转发。
#
# 产物：
#   runs/<日期>_三餐/final_三餐.mp4    ← 合成后的单条视频（转发用这个）
#   runs/<日期>_三餐/三餐清单.md        ← 三道菜名 + 营养 + 成本，转发时可当文案
#   runs/<日期>_<餐次>/                ← 三份独立成片，想单发某一餐也有
#
# 用法：
#   ./daily3.sh                # 三餐全出，真实生图（约 6-12 分钟，计费）
#   ./daily3.sh --reuse-images # 只复用图片缓存，验证链路用

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT" || exit 1

# 出片成功后自动把三餐成片发到企业微信；失败发告警。
# 默认开启，DIET_NOTIFY=0 关闭（daily3_autosend.sh 用它来接管发送）。
# shellcheck source=lib/notify.sh
. "$ROOT/lib/notify.sh"

RUNDAY="$(date +%F)"
RUNLOG="$ROOT/runs/_logs/${RUNDAY}.log"

# 出错统一走这里：打印 → 告警 → 带原退出码退出
die3() {
  echo "ERROR: $1" >&2
  if diet_notify_on; then
    diet_alert "出片（daily3.sh）" "$2" "$RUNLOG" "$1" || true
  fi
  exit "$2"
}

# shellcheck source=lib/ffmpeg.sh
. "$ROOT/lib/ffmpeg.sh"
diet_use_ffmpeg
if ! diet_ass_ok; then
  echo "ERROR: 当前 ffmpeg 不支持 ass 滤镜，字幕无法烧入画面" >&2
  diet_ffmpeg_hint
  exit 20
fi
HH="ffmpeg"

DATE="$(date +%F)"
OUT="$ROOT/runs/${DATE}_三餐"
mkdir -p "$OUT"

EXTRA_ARGS=("$@")

echo "==> 一天三餐出片 $DATE"
OK_SLOTS=()
FAIL_SLOTS=()
EXCLUDE=()      # 已经选过的菜谱 id，逐餐累积，保证三餐不重样

pick_name() {   # $1=manifest 路径，$2=字段（id / name）
  [ -f "$1" ] || { echo ""; return; }
  python3 - "$1" "$2" <<'PYEOF'
import json, sys
m = json.load(open(sys.argv[1], encoding="utf-8"))
if sys.argv[2] == "id":
    print(m.get("recipe", {}).get("id") or "")
else:
    print(m.get("menu", {}).get("name") or "")
PYEOF
}

for SLOT in breakfast lunch dinner; do
  echo ""
  echo "---------- $SLOT ----------"
  ARGS=(--slot "$SLOT")
  if [ "${#EXCLUDE[@]}" -gt 0 ]; then
    JOINED="$(IFS=,; echo "${EXCLUDE[*]}")"
    ARGS+=(--exclude-recipes "$JOINED")
    echo "==> 排除已选：$JOINED"
  fi
  # DIET_NOTIFY=0：这一层不发，等三餐拼成一条后再统一发，否则一天三条单餐视频
  if DIET_NOTIFY=0 ./daily.sh "${ARGS[@]}" ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}; then
    OK_SLOTS+=("$SLOT")
    RID="$(pick_name "$ROOT/runs/${DATE}_${SLOT}/manifest.json" id)"
    [ -n "$RID" ] && EXCLUDE+=("$RID")
  else
    echo "WARN: $SLOT 出片失败，跳过" >&2
    FAIL_SLOTS+=("$SLOT")
  fi
done

if [ "${#OK_SLOTS[@]}" -eq 0 ]; then
  die3 "三餐全部失败，未生成视频" 20
fi

# ---- 收集成片，按 早→午→晚 顺序 ----
LIST="$OUT/concat.txt"
: > "$LIST"
CLIPS=()
for SLOT in breakfast lunch dinner; do
  F="$ROOT/runs/${DATE}_${SLOT}/final.mp4"
  if [ -f "$F" ]; then
    echo "file '$F'" >> "$LIST"
    CLIPS+=("$F")
  fi
done

if [ "${#CLIPS[@]}" -eq 0 ]; then
  die3 "找不到任何成片" 21
fi

FINAL="$OUT/final_三餐.mp4"

if [ "${#CLIPS[@]}" -eq 1 ]; then
  cp "${CLIPS[0]}" "$FINAL"
else
  echo ""
  echo "==> 拼接 ${#CLIPS[@]} 段"
  # 三段由同一份代码产出，编码参数一致，优先无损直拼（快且不掉画质）
  if ! $HH -y -loglevel error -f concat -safe 0 -i "$LIST" -c copy "$FINAL" 2>/dev/null; then
    echo "     直拼失败，改用重编码拼接"
    $HH -y -loglevel error -f concat -safe 0 -i "$LIST" \
      -c:v libx264 -preset medium -crf 19 -pix_fmt yuv420p -r 30 \
      -c:a aac -b:a 160k -ar 44100 -movflags "+faststart" "$FINAL" || {
        die3 "拼接失败" 22; }
  fi
fi

# ---- 校验总时长 ≈ 各段之和 ----
SUM=0
for F in "${CLIPS[@]}"; do
  S=$($HH -i "$F" 2>&1 | sed -n 's/.*Duration: \([0-9:.]*\).*/\1/p' | head -1 | awk -F: '{print ($1*3600)+($2*60)+$3}')
  SUM=$(echo "$SUM + $S" | bc)
done
GOT=$($HH -i "$FINAL" 2>&1 | sed -n 's/.*Duration: \([0-9:.]*\).*/\1/p' | head -1 | awk -F: '{print ($1*3600)+($2*60)+$3}')
echo "==> 时长 分段合计 ${SUM}s / 合成 ${GOT}s"

# 时长对不上说明拼接有问题，宁可报错也不发一条残缺的片子
if [ "$(echo "$SUM > 0" | bc)" -eq 1 ]; then
  DIFF=$(echo "$SUM - $GOT" | bc | tr -d '-')
  if [ "$(echo "$DIFF > 2" | bc)" -eq 1 ]; then
    die3 "合成时长与分段合计差 ${DIFF}s，拼接可能不完整" 23
  fi
fi

# ---- 三餐清单（转发文案）----
MD="$OUT/三餐清单.md"
{
  echo "# $DATE 三餐推荐"
  for SLOT in breakfast lunch dinner; do
    M="$ROOT/runs/${DATE}_${SLOT}/manifest.json"
    case "$SLOT" in
      breakfast) LABEL="早餐" ;;
      lunch)     LABEL="午餐" ;;
      dinner)    LABEL="晚餐" ;;
    esac
    if [ -f "$M" ]; then
      python3 - "$M" "$LABEL" <<'PYEOF'
import json, sys
m = json.load(open(sys.argv[1], encoding="utf-8"))
menu = m.get("menu", {})
cost = m.get("cost", {})
f = menu.get("facts") or {}
print()
print(f"## {sys.argv[2]}：{menu.get('name','?')}")
print()
bits = [f"用时 {menu.get('minutes','?')} 分钟", f"成本约 ¥{cost.get('total','?')}"]
if f.get("kcal"):
    bits.append(f"{int(f['kcal'])} 千卡")
if f.get("protein"):
    bits.append(f"蛋白 {f['protein']}g")
print("｜".join(bits))
print()
for line in m.get("narration", {}).get("lines", []):
    print(f"> {line}")
PYEOF
    else
      echo ""
      echo "## ${LABEL}：（未出片）"
    fi
  done
} > "$MD"

echo ""
echo "==> 三餐清单 $MD"
[ "${#FAIL_SLOTS[@]}" -gt 0 ] && echo "WARN: 失败餐次 ${FAIL_SLOTS[*]}" >&2
echo "FINAL=$FINAL"

# ---- 自动发送 ----
# 发送失败不改退出码：片子已经拼好了，别让上层以为没出片。
if diet_notify_on; then
  echo ""
  echo "==> 自动发送三餐成片"
  if ! ./send_wecom.sh; then
    echo "WARN: 自动发送失败，成片仍在 ${FINAL}" >&2
  fi
fi

exit 0
