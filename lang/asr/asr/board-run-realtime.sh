#!/bin/bash
# 仿实时完整测试：指定"后端 × 测试音频"组合逐个跑，每次跑完把心跳摘要写进日志。
#
# 和 board-run-campaign.sh 的区别：那个用 `for SPEC in $PASSING` 拆"名 目录"对，
# 被空白全词拆分后第二轮就把目录当成了后端名（rc=2，参数错）。这里改成 while read 逐行成对。
# 后端目录一律不显式给，走链路里的 BACKEND_DIRS 默认表。
#
# 用法（板端，分离跑）：
#   setsid nohup /root/funasr/asr/board-run-realtime.sh >/root/funasr/logs/realtime.out 2>&1 &
# 改要跑的组合：编辑下面 PAIRS。
# 看进度：tail -20 /root/funasr/logs/realtime.log
set +e

PY=/root/funasr/venv/bin/python
F=/root/funasr/asr_chain.py
TD=/root/funasr/testdata
OUT=/root/funasr/out
LOG=/root/funasr/logs/realtime.log
CONT=/root/funasr/logs/realtime-contention.log
STATE_DIR=/dev/shm/asr
THREADS=${THREADS:-4}

PAIRS="funasr-paraformer b123
funasr-paraformer c247
funasr-paraformer-online b123
funasr-paraformer-online c247"

mkdir -p "$OUT" "$STATE_DIR"
: > "$LOG"
: > "$CONT"

say() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOG"; }

(
  while true; do
    echo "[$(date -u +%T)] avail=$(awk '/MemAvailable/{print $2}' /proc/meminfo)kB load=$(cut -d' ' -f1 /proc/loadavg) top=$(ps -eo rss,args --sort=-rss --no-headers | head -1 | awk '{printf "%dM %s", $1/1024, $2}')" >> "$CONT"
    sleep 5
  done
) &
SAMP=$!

say "realtime start threads=$THREADS pairs=$(echo "$PAIRS" | grep -c .)"

echo "$PAIRS" | while read -r NAME TAGW; do
  [ -z "$NAME" ] && continue
  [ -f "$TD/$TAGW.wav" ] || { say "skip $NAME $TAGW: 没有这条音频"; continue; }
  if [ -s "$OUT/${NAME}_${TAGW}.jsonl" ]; then
    say "skip $NAME $TAGW: 已有 $(grep -c . "$OUT/${NAME}_${TAGW}.jsonl") 段结果，不重跑"
    continue
  fi
  AV=$(awk '/MemAvailable/{print $2}' /proc/meminfo)
  say "run $NAME on $TAGW (avail=${AV}kB)"
  "$PY" "$F" --source wav --input "$TD/$TAGW.wav" \
      --backend "$NAME" --threads "$THREADS" --pace realtime --queue-alarm 8 \
      --segs-dir "/root/funasr/segments/${NAME}_${TAGW}" \
      --jsonl "$OUT/${NAME}_${TAGW}.jsonl" \
      --state "$STATE_DIR/state_${NAME}_${TAGW}" >> "$LOG" 2>&1
  RC=$?
  ST="$STATE_DIR/state_${NAME}_${TAGW}"
  say "run $NAME $TAGW rc=$RC segments=$(grep -c . "$OUT/${NAME}_${TAGW}.jsonl" 2>/dev/null) state=$(tr -d '\n' < "$ST" 2>/dev/null | cut -c1-300)"
done

kill "$SAMP" 2>/dev/null
say "realtime done; contention samples=$(grep -c . "$CONT")"
echo REALTIME-DONE | tee -a "$LOG"
