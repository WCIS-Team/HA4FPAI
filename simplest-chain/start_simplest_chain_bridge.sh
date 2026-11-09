#!/bin/bash
# start_simplest_chain_bridge.sh —— 启动 SiliconFlow ASR+LLM 桥（simplest_chain_bridge.py）。
#
#   bash start_simplest_chain_bridge.sh           # 前台，Ctrl-C 停
#   BG=1 bash start_simplest_chain_bridge.sh      # 后台，日志 logs/simplest_chain_bridge.log
#   PORT=18090 bash start_simplest_chain_bridge.sh
#
# 密钥（不要写进文件）：
#   export SILICONFLOW_API_KEY=...              # 优先
#   或写 .siliconflow-key（默认本目录下，chmod 600） # 其次
set -u
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DIR=${DIR:-$ROOT}
LOGD=${LOGD:-$ROOT/logs}
PY=${PY:-python3}
export PORT=${PORT:-18090}
export BIND=${BIND:-0.0.0.0}

mkdir -p "$LOGD"
KEYFILE=${KEYFILE:-$ROOT/.siliconflow-key}
if [ -z "${SILICONFLOW_API_KEY:-}" ] && [ -r "$KEYFILE" ]; then
  SILICONFLOW_API_KEY=$(tr -d ' \t\r\n' < "$KEYFILE")
fi
if [ -z "${SILICONFLOW_API_KEY:-}" ]; then
  echo "缺 SILICONFLOW_API_KEY：请 export SILICONFLOW_API_KEY=… 或写进 $KEYFILE 并 chmod 600" >&2
  exit 5
fi
export SILICONFLOW_API_KEY
export LLM_ENABLE=${LLM_ENABLE:-1}
export HISTORY_TURNS=${HISTORY_TURNS:-2}
export LLM_REASONING=${LLM_REASONING:-none}
export LOG_JSONL=${LOG_JSONL:-$LOGD/bridge.jsonl}

LOG=$LOGD/simplest_chain_bridge.log

port_open() {
  "$PY" - "$1" <<'PY' 2>/dev/null
import socket, sys
s = socket.socket()
s.settimeout(0.5)
try:
    s.connect(("127.0.0.1", int(sys.argv[1])))
except Exception:
    sys.exit(1)
finally:
    s.close()
PY
}

if [ "${BG:-0}" = "1" ]; then
  if port_open "$PORT"; then
    echo "端口 $PORT 已经有人在听（可能已在跑）；先 pkill -f simplest_chain_bridge.py 再起" >&2
    exit 3
  fi
  if command -v setsid >/dev/null 2>&1; then
    setsid nohup "$PY" "$DIR/simplest_chain_bridge.py" >> "$LOG" 2>&1 &
  else
    nohup "$PY" "$DIR/simplest_chain_bridge.py" >> "$LOG" 2>&1 &
  fi
  echo "pid=$! port=$PORT log=$LOG"
  for i in $(seq 1 40); do
    if port_open "$PORT"; then
      echo "起来了：http://127.0.0.1:$PORT/health"
      exit 0
    fi
    sleep 0.5
  done
  echo "20 秒还没起来，看日志：tail -40 $LOG" >&2
  exit 4
else
  exec "$PY" "$DIR/simplest_chain_bridge.py"
fi