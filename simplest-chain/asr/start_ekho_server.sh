#!/bin/bash
# start_ekho_server.sh —— 起 ekho-server（默认 2046）。
#
# 依赖现场已有的 ekho kit；默认按 /root/ekho-bench 布局找，可用 EKHO_ROOT 覆盖：
#   EKHO_ROOT=/root/ekho-bench bash start_ekho_server.sh
# 也兼容 `install_ekho.sh` 装出来的 $HOME/.local/ekho（bin/ + share/ 布局）。
#
# 这个 kit 是交叉编译出来的，espeak-ng-data 里可能烤进 Windows 路径
# （D:/yzd/ekho-x/kit-espeak/share/...）；在 kitcwd 下补一个同名软链即可。
set -u
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
EKHO_ROOT=${EKHO_ROOT:-/root/ekho-bench}
EKHO_SERVER=${EKHO_SERVER:-$EKHO_ROOT/bin/ekho-server}
EKHO_PORT=${EKHO_PORT:-2046}
EKHO_CWD=${EKHO_CWD:-$EKHO_ROOT/kitcwd}
LOG=${LOG:-$HERE/../logs/ekho_server.log}

if [ -z "${EKHO_DATA_PATH:-}" ]; then
  for cand in "$EKHO_ROOT/share/ekho-data" "$EKHO_ROOT/p44" "$EKHO_ROOT/ekho-data"; do
    if [ -d "$cand" ]; then EKHO_DATA_PATH=$cand; break; fi
  done
fi
if [ -z "${ESPEAK_DATA_PATH:-}" ]; then
  for cand in "$EKHO_ROOT/share/espeak-ng-data" "$EKHO_ROOT/espeak-ng-data" "/usr/share/espeak-ng-data"; do
    if [ -d "$cand" ]; then ESPEAK_DATA_PATH=$cand; break; fi
  done
fi

if [ ! -x "$EKHO_SERVER" ]; then
  echo "没找到可执行的 ekho-server：$EKHO_SERVER（设 EKHO_SERVER 或 EKHO_ROOT）" >&2
  exit 4
fi
if [ -z "${EKHO_DATA_PATH:-}" ] || [ ! -d "$EKHO_DATA_PATH" ]; then
  echo "没找到 ekho 语音数据目录（期望 \$EKHO_ROOT/share/ekho-data 或 \$EKHO_ROOT/p44）" >&2
  exit 5
fi
mkdir -p "$EKHO_CWD/D:/yzd/ekho-x/kit-espeak/share" "$(dirname "$LOG")"
if [ -n "${ESPEAK_DATA_PATH:-}" ] && [ -d "$ESPEAK_DATA_PATH" ]; then
  ln -sfn "$ESPEAK_DATA_PATH" "$EKHO_CWD/D:/yzd/ekho-x/kit-espeak/share/espeak-ng-data"
fi

if timeout 2 bash -c "echo > /dev/tcp/127.0.0.1/$EKHO_PORT" 2>/dev/null; then
  echo "ekho-server 已在 $EKHO_PORT 上跑着"
  exit 0
fi

cd "$EKHO_CWD"
EKHO_DATA_PATH="$EKHO_DATA_PATH" ESPEAK_DATA_PATH="${ESPEAK_DATA_PATH:-}" \
  setsid "$EKHO_SERVER" --port "$EKHO_PORT" --no-save \
  < /dev/null >> "$LOG" 2>&1 &
echo "ekho-server 启动中 pid=$! log=$LOG"
for i in $(seq 1 20); do
  sleep 0.5
  if timeout 1 bash -c "echo > /dev/tcp/127.0.0.1/$EKHO_PORT" 2>/dev/null; then
    echo "ekho-server 就绪：http://127.0.0.1:$EKHO_PORT（GET /health 看详情）"
    exit 0
  fi
done
echo "ekho-server 10 s 内没起来，看 $LOG" >&2
exit 1
