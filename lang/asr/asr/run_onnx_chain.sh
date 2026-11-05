#!/bin/bash
# 链路 B（带 ONNX）：WebRTC VAD（纯 C）→ 远程 ASR（HTTP）→ Piper 本机出声（onnx）。
#
# 2026-11-04 改动（她裁「把 fsmn 和 funasr 删了」）：FSMN-VAD 与 funasr_onnx 依赖已从本仓删除，
# 分段改用 WebRTC VAD（纯 C 扩展，无权重、无 onnxruntime）⇒ 链路进程回到系统 python3，
# 不再需要 /root/funasr/venv。与链路 A 的区别只剩 TTS：A 用 ekho（纯 C++），B 用 Piper
# （conda env /root/mf3/envs/tts 里的 piper-tts + onnxruntime）。ASR 仍然留空走远程。
# Piper 在**独立常驻进程**里，链路主进程只发 HTTP。
#
# 想换回 FSMN：得先恢复 funasr_onnx 依赖与 Fsmn-vad 权重（已删），再 `SEG=fvad`。
#
# 用法：
#   ASR_REMOTE_URL=http://127.0.0.1:18090/api/v1/asr bash /root/funasr/asr/run_onnx_chain.sh
#   SRC=wav WAV=/root/funasr/testdata/c247_60.wav ASR_REMOTE_URL=... bash .../run_onnx_chain.sh
#
# 环境变量：
#   ASR_REMOTE_URL   必填（同上，协议见 asr/remote_asr.py）
#   SRC=mic|wav  WAV=路径  PACE=free|realtime  SECS=0
#   DEV=plughw:MCP01,0  采集卡；SRC=mic 时先查在位，查不到就退出 3（不回落默认卡）
#   SEG=webrtc|rms（默认 webrtc，纯 C）  VAD_SIL=400（句尾静音 ms）
#   WR_MODE=0..3（WebRTC 严格度，3 最严）  WR_OPEN=0.4（起段语音占比）  WR_OPEN_FRAMES=1
#   TTS=none|ekho（默认 ekho；piper 档与 piper_tts_server 已于 2026-11-04 删除）
#   TTS_URL=（默认 http://127.0.0.1:2046）  TTS_DIR
#   LENGTH_SCALE=1.0（<1 更快）
#   PUNC=0|1  BG=1  JSONL/STATE/SEGS  AVAIL_MIN=300000
set -u
CHAIN=/root/funasr/asr_chain.py
LOGD=/root/funasr/logs
mkdir -p "$LOGD" /dev/shm/asr /root/funasr/tts

PY=${PY:-/usr/bin/python3}                # WebRTC VAD 是纯 C 扩展，系统 python3 就够
# webrtcvad（本体 + 包装）2026-11-04 起住在 vad/webrtc/pylite/；三级查找同链路 A。
# 补上这一处是因为链路 B 默认 SEG=webrtc，此前它没有 PYLITE 接线（会 import 不到）。
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
SEG=${SEG:-webrtc}                        # webrtc|rms（fvad 已随 FSMN 删除）
WR_MODE=${WR_MODE:-3}
WR_OPEN=${WR_OPEN:-0.4}
WR_OPEN_FRAMES=${WR_OPEN_FRAMES:-1}
VAD_SIL=${VAD_SIL:-400}
PUNC=${PUNC:-0}
TTS=${TTS:-ekho}
TTS_DIR=${TTS_DIR:-/root/funasr/tts}
LENGTH_SCALE=${LENGTH_SCALE:-1.0}
SECS=${SECS:-0}
JSONL=${JSONL:-/root/funasr/out/onnx_chain.jsonl}
STATE=${STATE:-/dev/shm/asr/state_onnx}
SEGS=${SEGS:-/root/funasr/segments}
LOG=$LOGD/onnx_chain.log
AVAIL_MIN=${AVAIL_MIN:-300000}

if [ -z "${ASR_REMOTE_URL:-}" ]; then
  echo "缺 ASR_REMOTE_URL：远程 ASR 的 endpoint" >&2
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

case "$TTS" in
  ekho)  TTS_URL=${TTS_URL:-http://127.0.0.1:2046}; PORT=$(echo "$TTS_URL" | sed 's|.*:\([0-9]*\).*|\1|') ;;
  *)     TTS_URL=${TTS_URL:-}; PORT="" ;;
esac
if [ "$TTS" != "none" ] && [ -n "$PORT" ]; then
  if ! timeout 2 bash -c "echo > /dev/tcp/127.0.0.1/$PORT" 2>/dev/null; then
    echo "== TTS 服务($TTS) 没在 $PORT 上，先拉起来" | tee -a "$LOG"
    bash /root/funasr/asr/start_ekho_server.sh
  fi
fi

# 闸门：这条链路要同时挂着 FSMN-VAD 的 ORT 会话和 piper 的 165 MB，比链路 A 高得多
NEED=$AVAIL_MIN
[ "$PUNC" = "1" ] && NEED=${AVAIL_MIN_PUNC:-450000}
gate() {
  local l1 avail
  while :; do
    l1=$(cut -d' ' -f1 /proc/loadavg)
    avail=$(awk '/MemAvailable/{print $2}' /proc/meminfo)
    if awk -v a="$avail" -v m="$NEED" 'BEGIN{exit !(a>=m)}'; then
      return 0
    fi
    echo "$(date -u '+%T') wait gate load1=$l1 avail=$avail(<$NEED kB)" >> "$LOGD/onnx_chain.gate.log"
    sleep 15
  done
}
gate

ARGS=(--source "$SRC" --pace "$PACE" --backend remote-api --device "$DEV"
      --segmenter "$SEG" --sil-stop-ms "$VAD_SIL"
      --webrtc-mode "$WR_MODE" --webrtc-open-ratio "$WR_OPEN"
      --webrtc-open-frames "$WR_OPEN_FRAMES"
      --jsonl "$JSONL" --state "$STATE" --segs-dir "$SEGS"
      --asr-url "$ASR_REMOTE_URL" --tts "$TTS" --tts-dir "$TTS_DIR"
      --tts-length-scale "$LENGTH_SCALE")
[ -n "$TTS_URL" ] && ARGS+=(--tts-url "$TTS_URL")
[ "$SRC" = "wav" ] && ARGS+=(--input "$WAV")
[ "$SECS" != "0" ] && ARGS+=(--max-seconds "$SECS")
[ "$PUNC" = "1" ] && ARGS+=(--punc --punc-threads 1)
ARGS+=("$@")

echo "=== chain-B(onnx) start $(date -u '+%F %T UTC') url=$ASR_REMOTE_URL seg=$SEG tts=$TTS($TTS_URL) src=$SRC dev=$DEV ${WAV:-} py=$PY" | tee -a "$LOG"

if [ "${BG:-0}" = "1" ]; then
  setsid nohup "$PY" "$CHAIN" "${ARGS[@]}" >> "$LOG" 2>&1 &
  echo "pid=$! state=$STATE jsonl=$JSONL log=$LOG"
  sleep 5
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
