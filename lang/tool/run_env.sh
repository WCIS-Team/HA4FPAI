#!/bin/bash
# 板端逐档环境探针批跑：一档一个进程，跑完把 JSON 收进 /root/funasr/out/env_probe.jsonl。
#
# 为什么一进程一档：VmHWM 是进程级峰值，同进程连测会把后面档的读数抬高（0.5 G 门禁就没法逐档判）。
# 起跑闸门照 §十二 comp-bench 的口径：/proc/loadavg 1 分钟 < 1.2 且 MemAvailable 高于下限才起，
# 下限默认 300000 kB（≈293 MB）；标点档 270 MB 权重，起之前把下限抬到 420000 kB。
#
# 用法（板端）：
#   bash /root/funasr/asr/run_env.sh                      # 跑默认清单（不含 contextual）
#   bash /root/funasr/asr/run_env.sh vad-online sensevoice
#   BIG=1 bash /root/funasr/asr/run_env.sh contextual     # 明确允许碰 830 MB 那档
set -u
V=/root/funasr/venv/bin/python
P=/root/funasr/asr/env_probe.py
OUT=/root/funasr/out/env_probe.jsonl
LOG=/root/funasr/logs/env_probe.log
mkdir -p /root/funasr/out /root/funasr/logs
AVAIL_MIN=${AVAIL_MIN:-300000}

gate() {
  local l1 avail
  while :; do
    l1=$(cut -d' ' -f1 /proc/loadavg)
    avail=$(awk '/MemAvailable/{print $2}' /proc/meminfo)
    if awk -v a="$avail" -v m="$AVAIL_MIN" 'BEGIN{exit !(a>=m)}' \
       && awk -v l="$l1" 'BEGIN{exit !(l<1.2)}'; then
      return 0
    fi
    echo "$(date -u '+%T') wait gate load1=$l1 avail=$avail(<$AVAIL_MIN kB)" >> "$LOG"
    sleep 20
  done
}

KEYS=${*:-"paraformer paraformer-online sensevoice vad-offline vad-online punc punc-rt seaco"}
# PROBE_WAV 给了就把它喂给探针：VAD 档光装载成功不算数，要真出段才算环境到位。
WAVARG=""
if [ -n "${PROBE_WAV:-}" ]; then
  WAVARG="--wav $PROBE_WAV --seconds ${PROBE_SECS:-20}"
fi
echo "=== env_probe start $(date -u '+%F %T UTC') keys=$KEYS wav=${WAVARG:-synthetic}" | tee -a "$LOG"
for k in $KEYS; do
  gate
  EXTRA=""
  [ "${BIG:-0}" = "1" ] && EXTRA="--allow-big"
  line=$($V "$P" "$k" $EXTRA $WAVARG 2>>"$LOG" | tail -1)
  echo "$line" >> "$OUT"
  echo "--- $k  $line" | tee -a "$LOG"
done
echo "=== env_probe done $(date -u '+%F %T UTC')" | tee -a "$LOG"
