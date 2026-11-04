#!/bin/bash
# 链路 A（无 ONNX）：WebRTC VAD → 远程 ASR（HTTP）→ ekho 本机出声。板上不做任何 ONNX 推理。
#
# 组成：系统 python3 + webrtcvad 小扩展（vad/webrtc/pylite/，板上老位置 /root/funasr/pylite）；
#       ASR 走远端；TTS 是 ekho-server
# （纯 C++ 单元拼接，也不吃 ONNX）。三段全是独立进程，链路进程 VmHWM ≈ 23 MB。
#
# 用法（板端）：
#   ASR_REMOTE_URL=http://127.0.0.1:18090/api/v1/asr bash /root/funasr/asr/run_remote_chain.sh
#   SRC=wav WAV=/root/funasr/testdata/c247_60.wav ASR_REMOTE_URL=... bash .../run_remote_chain.sh
#   BG=1 ASR_REMOTE_URL=... bash .../run_remote_chain.sh
#
# 环境变量：
#   ASR_REMOTE_URL   必填。ASR_REMOTE_PROTO=multipart|openai|json；
#                     ASR_REMOTE_TOKEN/TIMEOUT/RETRY/EXTRA
#   SRC=mic|wav  WAV=路径  PACE=free|realtime  SECS=0
#   DEV=plughw:MCP01,0  采集卡；SRC=mic 时先查在位，查不到就退出 3（不回落默认卡）
#   SEG=webrtc|rms|fvad  WR_MODE=0..3  WR_OPEN=0.4  WR_OPEN_FRAMES=1  SIL=400  PUNC=0|1
#   TTS=none|ekho|piper（默认 ekho）  TTS_URL=（默认 http://127.0.0.1:2046）  TTS_DIR=/root/funasr/tts
#   PY=/usr/bin/python3  PYLITE=/root/funasr/pylite  JSONL/STATE/SEGS  AVAIL_MIN=120000
#   BG=1 后台；额外参数原样传给 asr_chain.py
set -u
CHAIN=/root/funasr/asr_chain.py
LOGD=/root/funasr/logs
mkdir -p "$LOGD" /dev/shm/asr /root/funasr/tts

PY=${PY:-/usr/bin/python3}
# webrtcvad（本体 + 包装）2026-11-04 起住在 vad/webrtc/pylite/；三级查找：
#   ① 显式 PYLITE；② 板上老位置 /root/funasr/pylite；③ 本脚本相对 ../../vad/webrtc/pylite
# 注：vad/webrtc/webrtcseg.py 自己也做同样的 sys.path 引导，所以本变量不再是硬前提。
if [ -z "${PYLITE:-}" ]; then
  if [ -d /root/funasr/pylite ]; then
    PYLITE=/root/funasr/pylite
  else
    PYLITE="$(cd "$(dirname "$0")/../../vad/webrtc/pylite" 2>/dev/null && pwd)"
  fi
fi
[ -n "${PYLITE:-}" ] && export PYTHONPATH="${PYLITE}${PYTHONPATH:+:$PYTHONPATH}"

SRC=${SRC:-mic}
WAV=${WAV:-}
DEV=${DEV:-plughw:MCP01,0}
PACE=${PACE:-realtime}
SEG=${SEG:-webrtc}
WR_MODE=${WR_MODE:-3}
WR_OPEN=${WR_OPEN:-0.4}         # 一帧里语音子帧占比门槛（0.4＝原来那档）
WR_OPEN_FRAMES=${WR_OPEN_FRAMES:-1}   # 要连续几帧达标才起段（1＝原来那档）
SIL=${SIL:-400}
PUNC=${PUNC:-0}
TTS=${TTS:-ekho}
TTS_DIR=${TTS_DIR:-/root/funasr/tts}
SECS=${SECS:-0}
JSONL=${JSONL:-/root/funasr/out/remote_chain.jsonl}
STATE=${STATE:-/dev/shm/asr/state}
SEGS=${SEGS:-/root/funasr/segments}
LOG=$LOGD/remote_chain.log
AVAIL_MIN=${AVAIL_MIN:-120000}

if [ -z "${ASR_REMOTE_URL:-}" ]; then
  echo "缺 ASR_REMOTE_URL：远程 ASR 的 endpoint，例如 http://127.0.0.1:18090/api/v1/asr" >&2
  exit 2
fi

# 采集卡门禁：SRC=mic 时先核 DEV 点名的卡真在位，命中不到就退出 3，绝不回落到 plughw:0,0。
# 理由：卡 index 由 probe 顺序决定，跟物理 USB 口没有固定对应；认错卡的表现是"链路照跑、全是静音"，
# 而 arecord 对非 48k 的请求不报错、rc=0、静默降级，所以退出码也靠不住——只能在起链前把卡在位这件事查实。
mic_gate() {
  local tok
  case "$DEV" in
    *:*) tok=$(echo "$DEV" | sed 's/^[^:]*://; s/,.*$//') ;;
    *)   echo "== DEV=$DEV 里没有卡标识（不含冒号），跳过在位检查" | tee -a "$LOG"; return 0 ;;
  esac
  case "$tok" in
    # 短名：卡表里右括号前有对齐空格（" 0 [MCP01          ]:"），所以两侧都要容空格；
    # 也不能用固定串 [MCP01] —— 那样 MCP01 会匹配不上，而 MCP01/MCP012 会互相认错。
    ''|*[!0-9]*) grep -qE "\[[[:space:]]*${tok}[[:space:]]*\]" /proc/asound/cards 2>/dev/null ;;
    *)           grep -qE "^[[:space:]]*${tok}[[:space:]]" /proc/asound/cards 2>/dev/null ;;  # 纯数字：按 index
  esac || {
    echo "缺采集卡：DEV=$DEV 在 /proc/asound/cards 里找不到，不回落默认卡。当前卡表：" >&2
    cat /proc/asound/cards >&2
    exit 3
  }
  echo "== 采集卡在位：DEV=$DEV" | tee -a "$LOG"
}
[ "$SRC" = "mic" ] && mic_gate

# TTS：按引擎给默认地址，端口没开就把服务拉起来
case "$TTS" in
  ekho)  TTS_URL=${TTS_URL:-http://127.0.0.1:2046}; PORT=$(echo "$TTS_URL" | sed 's|.*:\([0-9]*\).*|\1|') ;;
  piper) TTS_URL=${TTS_URL:-http://127.0.0.1:5002}; PORT=$(echo "$TTS_URL" | sed 's|.*:\([0-9]*\).*|\1|') ;;
  *)     TTS_URL=${TTS_URL:-}; PORT="" ;;
esac
if [ "$TTS" != "none" ] && [ -n "$PORT" ]; then
  if ! timeout 2 bash -c "echo > /dev/tcp/127.0.0.1/$PORT" 2>/dev/null; then
    echo "== TTS 服务($TTS) 没在 $PORT 上，先拉起来" | tee -a "$LOG"
    if [ "$TTS" = "ekho" ]; then bash /root/funasr/asr/start_ekho_server.sh
    else bash /root/funasr/asr/start_piper_server.sh; fi
  fi
fi

if [ "$PUNC" = "1" ]; then NEED=${AVAIL_MIN_PUNC:-420000}; else NEED=$AVAIL_MIN; fi
gate() {
  local l1 avail
  while :; do
    l1=$(cut -d' ' -f1 /proc/loadavg)
    avail=$(awk '/MemAvailable/{print $2}' /proc/meminfo)
    if awk -v a="$avail" -v m="$NEED" 'BEGIN{exit !(a>=m)}'; then
      return 0
    fi
    echo "$(date -u '+%T') wait gate load1=$l1 avail=$avail(<$NEED kB)" >> "$LOGD/remote_chain.gate.log"
    sleep 15
  done
}
gate

ARGS=(--source "$SRC" --pace "$PACE" --backend remote-api --device "$DEV"
      --segmenter "$SEG" --webrtc-mode "$WR_MODE" --sil-stop-ms "$SIL"
      --webrtc-open-ratio "$WR_OPEN" --webrtc-open-frames "$WR_OPEN_FRAMES"
      --jsonl "$JSONL" --state "$STATE" --segs-dir "$SEGS"
      --asr-url "$ASR_REMOTE_URL" --tts "$TTS" --tts-dir "$TTS_DIR")
[ -n "$TTS_URL" ] && ARGS+=(--tts-url "$TTS_URL")
[ "$SRC" = "wav" ] && ARGS+=(--input "$WAV")
[ "$SECS" != "0" ] && ARGS+=(--max-seconds "$SECS")
[ "$PUNC" = "1" ] && ARGS+=(--punc --punc-threads 1)
ARGS+=("$@")

echo "=== chain-A(lite) start $(date -u '+%F %T UTC') url=$ASR_REMOTE_URL proto=${ASR_REMOTE_PROTO:-multipart} seg=$SEG tts=$TTS($TTS_URL) src=$SRC dev=$DEV ${WAV:-} py=$PY" | tee -a "$LOG"

if [ "${BG:-0}" = "1" ]; then
  setsid nohup "$PY" "$CHAIN" "${ARGS[@]}" >> "$LOG" 2>&1 &
  echo "pid=$! state=$STATE jsonl=$JSONL log=$LOG"
  sleep 3
  "$PY" - "$STATE" <<'PY'
import json, sys
try:
    s = json.load(open(sys.argv[1]))
    print("state: backend=%s segmenter=%s tts=%s pid=%s frames=%s"
          % (s.get("backend"), s.get("segmenter"), s.get("tts"), s.get("pid"), s.get("frames")))
except Exception as exc:
    print("state 还没写出来：%r" % (exc,))
PY
else
  exec "$PY" "$CHAIN" "${ARGS[@]}"
fi
