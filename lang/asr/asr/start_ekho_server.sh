#!/bin/bash
# 起 ekho-server（默认 2046）——纯 C++ 单元拼接，不吃 ONNX。
#
# 一个坑：这套 kit 编译时把 espeak-ng-data 的路径烤成了 Windows 的 D:/yzd/ekho-x/kit-espeak/share/...，
# 在 Linux 上它是个**相对路径**，所以从 cwd 下补出这层目录、指向板上真正的 espeak-ng-data 就能跑通。
# 语音数据（zh_list / pinyin/ 那一套）在 /root/ekho-bench/p44，用 EKHO_DATA_PATH 指过去。
set -u
W=/root/ekho-bench/kitcwd
LOG=/root/funasr/logs/ekho_server.log
mkdir -p "$W/D:/yzd/ekho-x/kit-espeak/share" /root/funasr/logs
ln -sfn /root/ekho-bench/espeak-ng-data "$W/D:/yzd/ekho-x/kit-espeak/share/espeak-ng-data"

if timeout 2 bash -c "echo > /dev/tcp/127.0.0.1/${PORT:-2046}" 2>/dev/null; then
  echo "ekho-server 已在 ${PORT:-2046} 上跑着"
  exit 0
fi

cd "$W"
EKHO_DATA_PATH=${EKHO_DATA_PATH:-/root/ekho-bench/p44} \
  setsid /root/ekho-bench/bin/ekho-server --port "${PORT:-2046}" --no-save \
  < /dev/null >> "$LOG" 2>&1 &
echo "ekho-server 启动中 pid=$! log=$LOG"
for i in $(seq 1 20); do
  sleep 0.5
  if timeout 1 bash -c "echo > /dev/tcp/127.0.0.1/${PORT:-2046}" 2>/dev/null; then
    echo "ekho-server 就绪：http://127.0.0.1:${PORT:-2046}（GET /health 看详情）"
    exit 0
  fi
done
echo "ekho-server 10 s 内没起来，看 $LOG" >&2
exit 1
