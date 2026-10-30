#!/bin/bash
# 每日饮食 · 发单餐成片到企业微信
#
# 给「只跑一餐」的场景用（./daily.sh --slot lunch，或整天只补一餐）。
# 三餐合并发送仍走 send_wecom.sh。
#
# 用法：
#   ./send_slot.sh breakfast
#   ./send_slot.sh lunch 张三
#
# 退出码：30 找不到成片 / 32 上传失败 / 40 缺消息权限 / 41 目标不在最近会话 / 42 发送被拒

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT" || exit 1
# shellcheck source=lib/notify.sh
. "$ROOT/lib/notify.sh"

SLOT="${1:-dinner}"
DIET_WECOM_TARGET="${2:-${DIET_WECOM_TARGET:-安格罗秀}}"
DIET_TARGET="$DIET_WECOM_TARGET"
# shellcheck source=lib/ffmpeg.sh
. "$ROOT/lib/ffmpeg.sh"
diet_use_ffmpeg

case "$SLOT" in
  breakfast) LABEL="早餐" ;;
  lunch)     LABEL="午餐" ;;
  dinner)    LABEL="晚餐" ;;
  *) echo "ERROR: 未知餐次 $SLOT（应为 breakfast/lunch/dinner）" >&2; exit 2 ;;
esac

DATE="$(date +%F)"
DIR="$ROOT/runs/${DATE}_${SLOT}"
SRC="$DIR/final.mp4"
SEND="$DIR/final_发送版.mp4"

if [ ! -f "$SRC" ]; then
  echo "ERROR: 找不到今天的${LABEL}成片 $SRC" >&2
  exit 30
fi

CID="$(diet_chat_id)"
MRC=$?
if [ "$MRC" -ne 0 ]; then
  echo "ERROR: ${CID}" >&2
  exit "$MRC"
fi

diet_compress "$SRC" "$SEND" || exit $?

# 文案取本次 manifest，拿不到就退回占位
DESC="$(python3 - "$DIR/manifest.json" <<'PYEOF'
import json, sys, os
p = sys.argv[1]
if not os.path.exists(p):
    print(""); sys.exit()
m = json.load(open(p, encoding="utf-8"))
menu, cost = m.get("menu", {}), m.get("cost", {})
f = menu.get("facts") or {}
bits = [f"用时 {menu.get('minutes','?')} 分钟", f"成本约 ¥{cost.get('total','?')}"]
if f.get("kcal"):    bits.append(f"{int(f['kcal'])} 千卡")
if f.get("protein"): bits.append(f"蛋白 {f['protein']}g")
print(" ｜ ".join(bits))
PYEOF
)"

NAME="$(python3 - "$DIR/manifest.json" <<'PYEOF'
import json, sys, os
p = sys.argv[1]
print(json.load(open(p, encoding="utf-8")).get("menu", {}).get("name", "今日一餐") if os.path.exists(p) else "今日一餐")
PYEOF
)"

diet_send_video "$CID" "$SEND" "今日${LABEL}（${DATE}）" "${NAME}${DESC:+ ｜ $DESC}" || exit $?
echo "OK video"

diet_send_md "$CID" "### 今日${LABEL}（${DATE}）

**${NAME}**${DESC:+　$DESC}

视频见上一条 👆" && echo "OK markdown"

echo "==> 已发送给 ${DIET_TARGET}"
exit 0
