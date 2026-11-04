#!/bin/bash
# 起常驻 Piper TTS 服务（默认 5002，ONNX 档）。
#
# 为什么必须常驻：piper 每装载一次模型要 22~27 s（63 MB onnx + ORT），一次性 CLI 没法进链路。
# 服务用 /root/mf3/envs/tts（py3.11 + onnxruntime 1.31.0）跑，链路的 python 不需要装 piper。
set -u
PY=${PIPER_PY:-/root/mf3/envs/tts/bin/python}
MODEL=${PIPER_MODEL:-/root/ttsbench/models/piper/zh_CN-huayan-medium.onnx}
PORT=${PORT:-5002}
# 服务脚本位置（2026-11-04 起仓库侧正本移到 lang/server/archive/，板上仍在 /root/funasr/asr/）
SVC_DIR=${SVC_DIR:-/root/funasr/asr}
if [ ! -f "$SVC_DIR/piper_tts_server.py" ]; then
  _rel="$(cd "$(dirname "$0")/../../server/archive" 2>/dev/null && pwd)"
  [ -n "${_rel:-}" ] && [ -f "$_rel/piper_tts_server.py" ] && SVC_DIR="$_rel"
fi
if [ ! -f "$SVC_DIR/piper_tts_server.py" ]; then
  echo "找不到 piper_tts_server.py：SVC_DIR=$SVC_DIR（可用 SVC_DIR=… 指定）" >&2
  exit 6
fi
LOG=/root/funasr/logs/piper_tts.log
mkdir -p /root/funasr/logs /root/funasr/tts

if timeout 2 bash -c "echo > /dev/tcp/127.0.0.1/$PORT" 2>/dev/null; then
  echo "piper 服务已在 $PORT 上跑着"
  exit 0
fi

setsid "$PY" "$SVC_DIR/piper_tts_server.py" --port "$PORT" --model "$MODEL" \
  < /dev/null >> "$LOG" 2>&1 &
echo "piper 服务启动中 pid=$! model=$MODEL log=$LOG（装载约 23 s）"
for i in $(seq 1 90); do
  sleep 1
  if timeout 1 bash -c "echo > /dev/tcp/127.0.0.1/$PORT" 2>/dev/null; then
    echo "piper 就绪：http://127.0.0.1:$PORT（GET /health 看 load_s）"
    exit 0
  fi
done
echo "piper 90 s 内没起来，看 $LOG" >&2
exit 1
