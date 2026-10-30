#!/bin/bash
# 每日饮食 · 公共通知库（被 source，不要直接执行）
#
# 提供两件事：
#   diet_chat_id         匹配企业微信目标会话，输出 chat_id
#   diet_send_md         往会话发一条 markdown
#   diet_alert           发失败告警（带阶段/退出码/日志尾部）
#
# 开关：DIET_NOTIFY=0 关闭自动发送。
#   daily3.sh 会用它来抑制「每一餐都单独发一条」；
#   daily3_autosend.sh 也用它，改由自己统一发。
#
# 目标：DIET_WECOM_TARGET，默认「安格罗秀」。

DIET_TARGET="${DIET_WECOM_TARGET:-安格罗秀}"

# 是否启用自动发送
diet_notify_on() {
  [ "${DIET_NOTIFY:-1}" != "0" ]
}

# 匹配会话：成功时 stdout 只有 chat_id；失败时 stdout 是原因，返回非零
diet_chat_id() {
  local tmp="/tmp/wecom_sessions_diet_$$.json"
  wecom-cli message aibot sessions list 2>/dev/null > "$tmp"
  python3 - "$DIET_TARGET" "$tmp" <<'PYEOF'
import json, sys
target, path = sys.argv[1], sys.argv[2]
try:
    d = json.load(open(path, encoding="utf-8"))
except Exception as e:
    print(f"会话列表读取失败：{e}"); sys.exit(43)
if d.get("errcode") in (850002, 850003):
    print(d.get("help_message") or d.get("errmsg") or "机器人缺「消息」权限"); sys.exit(40)
if d.get("errcode"):
    print(f"会话列表接口报错：{d.get('errmsg','')}"); sys.exit(43)
sess = [s for s in (d.get("sessions") or [])
        if (s.get("chat_name") or "").strip() == target]
if len(sess) != 1:
    names = "、".join((s.get("chat_name") or "?") for s in (d.get("sessions") or []))
    print(f"未唯一匹配「{target}」；当前可发送：{names or '（空）'}"); sys.exit(41)
print(sess[0]["chat_id"])
PYEOF
  local rc=$?
  rm -f "$tmp"
  return $rc
}

# 发 markdown：$1=chat_id $2=内容
diet_send_md() {
  python3 - "$1" "$2" <<'PYEOF'
import json, subprocess, sys
chat_id, content = sys.argv[1], sys.argv[2]
payload = {"chat_id": chat_id, "msg_type": "markdown",
           "markdown": {"content": content}}
r = subprocess.run(["wecom-cli", "message", "aibot", "send", "--json",
                    json.dumps(payload, ensure_ascii=False)],
                   capture_output=True, text=True)
try:
    ok = bool(json.loads(r.stdout).get("success"))
except Exception:
    ok = False
sys.exit(0 if ok else 1)
PYEOF
}

# 失败告警：$1=阶段 $2=退出码 $3=日志文件 $4=补充说明（可空）
diet_alert() {
  local stage="$1" code="$2" logfile="${3:-}" extra="${4:-}"
  local cid why
  cid="$(diet_chat_id)" || {
    why="$cid"
    echo "WARN: 告警无法送达（${why}），仅打印到终端" >&2
    return 1
  }
  local tail_txt=""
  [ -n "$logfile" ] && [ -f "$logfile" ] && tail_txt="$(tail -n 12 "$logfile" 2>/dev/null)"
  local content="### ⚠️ 饮食出片失败（$(date +%F)）

**阶段**：${stage}
**退出码**：${code}${extra:+
**说明**：${extra}}"
  [ -n "$logfile" ] && content="${content}
**日志**：\`$(basename "$(dirname "$logfile")")/$(basename "$logfile")\`"
  content="${content}

\`\`\`
${tail_txt:-（无日志输出）}
\`\`\`

本次未发送视频。"
  diet_send_md "$cid" "$content" || { echo "WARN: 告警发送失败" >&2; return 1; }
  echo "==> 已发送失败告警给 ${DIET_TARGET}"
  return 0
}

# 压缩成可发送的 720x1280 版本（视频消息有大小限制）：$1=源 $2=目标
diet_compress() {
  local src="$1" dst="$2"
  if [ -f "$dst" ] && [ "$dst" -nt "$src" ] && [ "$(stat -f%z "$dst")" -le 12000000 ]; then
    echo "==> 复用已有发送版 $(du -h "$dst" | cut -f1)"
    return 0
  fi
  echo "==> 压缩发送版（720x1280）"
  ffmpeg -y -loglevel error -i "$src" -vf scale=720:1280 \
    -c:v libx264 -preset medium -crf 27 -pix_fmt yuv420p \
    -c:a aac -b:a 96k -movflags +faststart "$dst" || {
      echo "ERROR: 压缩失败" >&2; return 31; }
  echo "==> 发送版 $(du -h "$dst" | cut -f1)"
}

# 上传并发送视频消息：$1=chat_id $2=发送版路径 $3=标题 $4=描述
diet_send_video() {
  local cid="$1" file="$2" title="$3" desc="$4"
  local media
  media="$(wecom-cli media upload --type video --file-path "$file" 2>/dev/null \
    | python3 -c "import json,sys;print(json.load(sys.stdin).get('media_id',''))")"
  [ -z "$media" ] && { echo "ERROR: 视频上传失败（可能超大小限制或授权失效）" >&2; return 32; }
  echo "==> 上传完成"
  python3 - "$cid" "$media" "$title" "$desc" <<'PYEOF'
import json, subprocess, sys
chat_id, media, title, desc = sys.argv[1:5]
video = {"media_id": media, "title": title}
if desc:
    video["description"] = desc
payload = {"chat_id": chat_id, "msg_type": "video", "video": video}
r = subprocess.run(["wecom-cli", "message", "aibot", "send", "--json",
                    json.dumps(payload, ensure_ascii=False)],
                   capture_output=True, text=True)
try:
    ok = bool(json.loads(r.stdout).get("success"))
except Exception:
    ok = False
sys.exit(0 if ok else 42)
PYEOF
}
