#!/bin/bash
# 每日饮食 · 三餐出片 + 自动发企业微信（唯一入口）
#
# 为什么要有这一层：
#   daily3.sh 只管出片，send_wecom.sh 只管发送。两个独立脚本靠人（或 Agent）
#   串起来，只要漏一步，当天就静默不发；daily3.sh 失败时更是没人知道。
#   这里把「出片 → 发送」焊成一条，成功发视频+清单，失败发文字告警，
#   保证每一次执行都有回执。
#
# 用法：
#   ./daily3_autosend.sh                    # 三餐全出并发送（默认对象「安格罗秀」）
#   ./daily3_autosend.sh --reuse-images     # 参数原样透传给 daily3.sh，验证链路用
#   DIET_WECOM_TARGET=张三 ./daily3_autosend.sh
#   DIET_NO_LOCK=1 ./daily3_autosend.sh     # 跳过防并发锁（调试用）
#
# 退出码：
#   0   出片成功且视频+清单均已送达
#   20+ daily3.sh 自身的错误码（20/21/22/23），已发告警
#   30-42 send_wecom.sh 的错误码，已发告警
#   60  已有任务在跑（并发保护）
#   61  出片成功但告警通道也不可用（极少见）

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT" || exit 1
# shellcheck source=lib/ffmpeg.sh
. "$ROOT/lib/ffmpeg.sh"
diet_use_ffmpeg

TARGET="${DIET_WECOM_TARGET:-安格罗秀}"
DATE="$(date +%F)"
OUT="$ROOT/runs/${DATE}_三餐"
FINAL="$OUT/final_三餐.mp4"
LOGDIR="$ROOT/runs/_logs"
NOTIFY_LOG="$LOGDIR/notify_${DATE}.log"
CHATFILE="/tmp/wecom_chat_id_autosend.txt"
mkdir -p "$LOGDIR"

# ---- 并发保护：定时任务和手动执行撞一起会互相踩产物 ----
# 锁里记 PID。上一轮被 kill -9 或异常退出时 trap 没机会清理，这里靠 PID
# 是否存活来判断锁是「真在跑」还是「陈旧」，陈旧就自动清掉重拿 —— 否则一天
# 卡住，之后每天都 60 失败。
LOCK="$ROOT/runs/.autosend.lock"

acquire_lock() {
  mkdir "$LOCK" 2>/dev/null && { echo $$ > "$LOCK/pid"; return 0; }
  local oldpid=""
  [ -f "$LOCK/pid" ] && oldpid="$(cat "$LOCK/pid" 2>/dev/null)"
  if [ -n "$oldpid" ] && kill -0 "$oldpid" 2>/dev/null; then
    echo "$oldpid"
    return 1
  fi
  echo "==> 清理陈旧锁（pid=${oldpid:-未知} 已不存在）"
  rm -rf "$LOCK"
  mkdir "$LOCK" 2>/dev/null && { echo $$ > "$LOCK/pid"; return 0; }
  return 1
}

if [ "${DIET_NO_LOCK:-0}" != "1" ]; then
  if ! OWNER=$(acquire_lock); then
    echo "ERROR: 另一个出片任务正在运行（pid ${OWNER:-未知}），本次跳过。确认没在跑可删 $LOCK 后重试。" >&2
    exit 60
  fi
  trap 'rm -rf "$LOCK" 2>/dev/null' EXIT
fi

log() { echo "$*" | tee -a "$NOTIFY_LOG"; }

# ---- 匹配目标会话，拿到 chat_id ----
# 先匹配再干活：目标不在机器人最近会话里时，没必要白跑 10 分钟
match_chat() {
  wecom-cli message aibot sessions list 2>/dev/null > /tmp/wecom_sessions_autosend.json
  python3 - "$TARGET" "$CHATFILE" <<'PYEOF'
import json, sys
target, out_path = sys.argv[1], sys.argv[2]
try:
    d = json.load(open("/tmp/wecom_sessions_autosend.json", encoding="utf-8"))
except Exception as e:
    print(f"FAIL: 会话列表读取失败 {e}"); sys.exit(43)
if d.get("errcode") in (850002, 850003):
    print("PERMISSION:" + (d.get("help_message") or d.get("errmsg") or "")); sys.exit(40)
if d.get("errcode"):
    print(f"FAIL: 会话列表接口报错 {d.get('errmsg','')}"); sys.exit(43)
sess = [s for s in (d.get("sessions") or [])
        if (s.get("chat_name") or "").strip() == target]
if len(sess) != 1:
    names = "、".join((s.get("chat_name") or "?") for s in (d.get("sessions") or []))
    print(f"NOTFOUND: 未唯一匹配「{target}」。当前可发送：{names or '（空）'}"); sys.exit(41)
open(out_path, "w").write(sess[0]["chat_id"])
print(f"OK 已匹配「{target}」")
PYEOF
}

# ---- 发一条 markdown 告警（不依赖成片，失败场景也能用）----
send_alert() {
  local content="$1"
  if [ ! -s "$CHATFILE" ]; then
    echo "WARN: 没有可用的会话，告警无法送达，仅打印到终端" >&2
    printf '%s\n' "$content" >&2
    return 1
  fi
  python3 - "$content" "$(cat "$CHATFILE")" <<'PYEOF'
import json, subprocess, sys
content, chat_id = sys.argv[1], sys.argv[2]
payload = {"chat_id": chat_id, "msg_type": "markdown",
           "markdown": {"content": content}}
r = subprocess.run(["wecom-cli", "message", "aibot", "send", "--json",
                    json.dumps(payload, ensure_ascii=False)],
                   capture_output=True, text=True)
try:
    ok = bool(json.loads(r.stdout).get("success"))
except Exception:
    ok = False
print("==> 告警已发送" if ok else "WARN: 告警发送失败")
sys.exit(0 if ok else 1)
PYEOF
}

# ---- 失败退出：告警 + 落盘 + 透传退出码 ----
die_with_alert() {   # $1=阶段说明 $2=退出码 $3=日志文件
  local stage="$1" code="$2" logfile="$3"
  local tail_txt=""
  [ -f "$logfile" ] && tail_txt="$(tail -n 12 "$logfile" 2>/dev/null | sed 's/^/    /')"
  log "RESULT=fail stage=${stage} exit=${code}"
  send_alert "### ⚠️ 今日三餐出片失败（${DATE}）

**阶段**：${stage}
**退出码**：${code}
**日志**：\`runs/_logs/${DATE}.log\`

\`\`\`
${tail_txt:-（无日志输出）}
\`\`\`

本次未发送视频。" || true
  exit "$code"
}

echo "==> [1/3] 校验发送通道 $DATE"
match_chat
MRC=$?
if [ "$MRC" -ne 0 ]; then
  log "RESULT=fail stage=校验发送通道 exit=${MRC}"
  echo "ERROR: 发送通道不可用，本次不出片（避免白跑 10 分钟）。" >&2
  exit "$MRC"
fi

echo "==> [2/3] 出片 $DATE"
log "===== $(date '+%F %T') 开始执行 target=${TARGET} args=${*:-无} ====="
START=$(date +%s)
RUN_LOG="$LOGDIR/${DATE}.log"
# DIET_NOTIFY=0：daily3.sh 内置的自动发送关掉，由这一层统一发（带前置校验 + 单独的执行记录）
DIET_NOTIFY=0 ./daily3.sh "$@"
RC=$?
DUR=$(( $(date +%s) - START ))

if [ "$RC" -ne 0 ]; then
  # 出片失败：绝不去发旧成片，只报告原因
  die_with_alert "出片（daily3.sh）" "$RC" "$RUN_LOG"
fi

if [ ! -f "$FINAL" ]; then
  die_with_alert "出片完成但找不到成片 ${FINAL}" 30 "$RUN_LOG"
fi

echo "==> [3/3] 发送到企业微信（${TARGET}）"
./send_wecom.sh "$TARGET"
SRC=$?
if [ "$SRC" -ne 0 ]; then
  die_with_alert "发送（send_wecom.sh）" "$SRC" "$RUN_LOG"
fi

SIZE=$(du -h "$FINAL" | cut -f1)
log "RESULT=ok duration=${DUR}s size=${SIZE} final=${FINAL}"
echo ""
echo "==> 完成：出片 ${DUR}s，已发送给 ${TARGET}"
echo "FINAL=$FINAL"
exit 0
