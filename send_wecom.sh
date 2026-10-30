#!/bin/bash
# 每日饮食 · 把当天三餐视频发到企业微信
#
# 给定时任务一个确定性的发送入口。四步按顺序做，任一步失败就停：
#   1. 取会话列表并精确匹配目标（聊天名），拿 chat_id
#   2. 压一个 720x1280 的发送版（视频消息有大小限制，实测 25MB 被拒，
#      报「无效的视频大小」；母版 1080x1920 原画质保留不动）
#   3. 上传视频（必须 --type video，否则拿到的是 file 类型，不能当视频消息发）
#   4. 发视频消息 + 发文字清单
#
# 先匹配后上传，是有意的：目标不在会话列表里时不该白传一次 5MB。
#
# 用法：
#   ./send_wecom.sh                # 发给默认对象「安格罗秀」
#   ./send_wecom.sh 张三            # 发给别人（必须在机器人最近会话列表里）

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT" || exit 1
# shellcheck source=lib/ffmpeg.sh
. "$ROOT/lib/ffmpeg.sh"
diet_use_ffmpeg

TARGET="${1:-安格罗秀}"
DATE="$(date +%F)"
DIR="$ROOT/runs/${DATE}_三餐"
SRC="$DIR/final_三餐.mp4"
SEND="$DIR/final_三餐_发送版.mp4"
CHATFILE="/tmp/wecom_chat_id.txt"

if [ ! -f "$SRC" ]; then
  echo "ERROR: 找不到今天的成片 $SRC" >&2
  exit 30
fi

# ---- 1. 匹配目标会话 ----
wecom-cli message aibot sessions list 2>/dev/null > /tmp/wecom_sessions.json

python3 - "$TARGET" "$CHATFILE" <<'PYEOF'
import json, sys

target, out_path = sys.argv[1], sys.argv[2]
d = json.load(open("/tmp/wecom_sessions.json", encoding="utf-8"))

if d.get("errcode") in (850002, 850003):
    # 权限缺失：原样转达 help_message，不重试、不绕过
    print("PERMISSION:" + (d.get("help_message") or d.get("errmsg") or ""))
    sys.exit(40)
if d.get("errcode"):
    print(f"FAIL: 会话列表接口报错 {d.get('errcode')} {d.get('errmsg','')}")
    sys.exit(43)

sess = [s for s in (d.get("sessions") or [])
        if (s.get("chat_name") or "").strip() == target]
if len(sess) != 1:
    names = "、".join((s.get("chat_name") or "?") for s in (d.get("sessions") or []))
    print(f"NOTFOUND: 会话列表中未唯一匹配「{target}」。当前可发送：{names or '（空）'}")
    sys.exit(41)

open(out_path, "w").write(sess[0]["chat_id"])
print(f"OK 已匹配「{target}」({sess[0].get('chat_type')})")
PYEOF

MATCH=$?
if [ "$MATCH" -ne 0 ]; then
  case $MATCH in
    40) echo "ERROR: 机器人缺「消息」权限，需要创建者授权" >&2 ;;
    41) echo "ERROR: 目标不在机器人最近的会话列表里，无法发送" >&2 ;;
    *)  echo "ERROR: 匹配目标失败 (exit ${MATCH})" >&2 ;;
  esac
  exit "$MATCH"
fi

# ---- 2. 压缩成发送版 ----
NEED_COMPRESS=1
if [ -f "$SEND" ] && [ "$SEND" -nt "$SRC" ]; then
  SZ=$(stat -f%z "$SEND")
  [ "$SZ" -le 12000000 ] && NEED_COMPRESS=0
fi
if [ "$NEED_COMPRESS" -eq 1 ]; then
  echo "==> 压缩发送版（720x1280）"
  ffmpeg -y -loglevel error -i "$SRC" -vf scale=720:1280 \
    -c:v libx264 -preset medium -crf 27 -pix_fmt yuv420p \
    -c:a aac -b:a 96k -movflags +faststart "$SEND" || {
      echo "ERROR: 压缩失败" >&2; exit 31; }
fi
echo "==> 发送版 $(du -h "$SEND" | cut -f1)"

# ---- 3. 上传 ----
MEDIA=$(wecom-cli media upload --type video --file-path "$SEND" 2>/dev/null \
  | python3 -c "import json,sys;print(json.load(sys.stdin).get('media_id',''))")
if [ -z "$MEDIA" ]; then
  echo "ERROR: 视频上传失败（可能超大小限制或授权失效）" >&2
  exit 32
fi
echo "==> 上传完成"

# ---- 4. 发送 ----
python3 - "$MEDIA" "$DATE" "$ROOT" "$CHATFILE" <<'PYEOF'
import json, os, re, subprocess, sys

media, date, root, chatfile = sys.argv[1:5]
chat_id = open(chatfile).read().strip()

def clean(j):
    return {k: v for k, v in j.items()
            if k not in ("security_notice", "extra_identity_context")}

def send(payload):
    r = subprocess.run(["wecom-cli", "message", "aibot", "send", "--json",
                        json.dumps(payload, ensure_ascii=False)],
                       capture_output=True, text=True)
    try:
        return clean(json.loads(r.stdout))
    except Exception:
        return {"success": False, "raw": r.stdout[:300]}

md_path = f"{root}/runs/{date}_三餐/三餐清单.md"
md = open(md_path, encoding="utf-8").read() if os.path.exists(md_path) else ""

dishes = [f"{m.group(1)[0]}：{m.group(2).strip()}"
          for m in re.finditer(r"##\s*(早餐|午餐|晚餐)：(.*?)\n", md)]

video_payload = {"chat_id": chat_id, "msg_type": "video",
                 "video": {"media_id": media, "title": f"今日三餐（{date}）"}}
if dishes:
    video_payload["video"]["description"] = " ｜ ".join(dishes)

j = send(video_payload)
if not j.get("success"):
    print("SENDFAIL: 视频消息发送失败：" + json.dumps(j, ensure_ascii=False))
    sys.exit(42)
print("OK video")

rows = []
for m in re.finditer(r"##\s*(早餐|午餐|晚餐)：(.*?)\n+\s*(.*?)\n", md):
    label, name, stats = m.group(1), m.group(2).strip(), m.group(3).strip()
    stats = stats.replace("｜", " · ").replace("成本约 ¥", "¥")
    rows.append(f"**{label}** {name}　{stats}")

if rows:
    content = f"### 今日三餐（{date}）\n\n" + "\n\n".join(rows) + "\n\n视频见上一条 👆"
    j2 = send({"chat_id": chat_id, "msg_type": "markdown",
               "markdown": {"content": content}})
    print("OK markdown" if j2.get("success") else "WARN 文字清单发送失败")
PYEOF

STATUS=$?
if [ "$STATUS" -eq 0 ]; then
  echo "==> 已发送给 ${TARGET}"
  exit 0
fi
echo "ERROR: 发送失败 (exit ${STATUS})" >&2
exit "$STATUS"
