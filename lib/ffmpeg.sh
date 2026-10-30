#!/bin/bash
# 定位带 libass 的 ffmpeg —— 字幕能不能烧进画面全靠它。
#
# 为什么要有这个文件：
#   ffmpeg 的 libass 是编译期选项。Homebrew、apt、choco 装的版本里有一部分是裁剪过的，
#   那种 ffmpeg 根本没有 ass 滤镜；没有的时候字幕**不会报错**，成片照常输出、
#   退出码依然是 0，只是画面里一个字都没有。所以必须先找对二进制，而不是"有 ffmpeg 就行"。
#
# 优先级（先到先得）：
#   1. DIET_FFMPEG_DIR            环境变量手动指定
#   2. <repo>/vendor/ffmpeg       tools/fetch_ffmpeg.py 下载到这里
#   3. ~/.workbuddy/binaries/ffmpeg-libass
#   4. 系统 PATH 里的 ffmpeg      调用方需自行检查 ass 滤镜
#
# 用法（各脚本在设定 ROOT 之后 source 本文件）：
#   . "$ROOT/lib/ffmpeg.sh"
#   diet_use_ffmpeg

diet_ffmpeg_candidates() {
  printf '%s\n' \
    "${DIET_FFMPEG_DIR:-}" \
    "${ROOT:-.}/vendor/ffmpeg" \
    "$HOME/.workbuddy/binaries/ffmpeg-libass"
}

# 输出命中的目录；找不到返回非零
diet_ffmpeg_dir() {
  local d
  while IFS= read -r d; do
    [ -n "$d" ] || continue
    if [ -x "$d/ffmpeg" ] || [ -x "$d/ffmpeg.exe" ]; then
      printf '%s\n' "$d"
      return 0
    fi
  done <<EOF
$(diet_ffmpeg_candidates)
EOF
  return 1
}

diet_use_ffmpeg() {
  local d
  if d="$(diet_ffmpeg_dir)"; then
    export PATH="$d:$PATH"
    if [ -x "$d/ffmpeg" ]; then
      export DIET_FFMPEG_BIN="$d/ffmpeg"
    else
      export DIET_FFMPEG_BIN="$d/ffmpeg.exe"
    fi
  else
    export DIET_FFMPEG_BIN="$(command -v ffmpeg 2>/dev/null || true)"
  fi
}

# 不带 grep -q 的 ass 检测：pipefail 下 `ffmpeg | grep -q` 会让 ffmpeg 收到
# SIGPIPE(141)，把一次正常检测偶发判成"不支持 ass"，进而静默跳掉某一餐。
diet_ass_ok() {
  local bin out
  bin="${DIET_FFMPEG_BIN:-ffmpeg}"
  out="$( "$bin" -hide_banner -filters 2>/dev/null || true )"
  case "$out" in
    *" ass "*) return 0 ;;
  esac
  return 1
}

diet_ffmpeg_hint() {
  cat >&2 <<'HINT'
       当前 ffmpeg 缺少 ass 滤镜，字幕烧不进画面（而且不会报错）。
       修法二选一：
         1) python3 tools/fetch_ffmpeg.py     # 自动下载带 libass 的静态构建到 vendor/ffmpeg
         2) 手动指定：DIET_FFMPEG_DIR=/路径/含ffmpeg的目录 ./脚本名
HINT
}
