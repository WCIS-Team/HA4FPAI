#!/bin/bash
# 板端 RAM 门禁：三个候选后端逐个装载＋一段真音频推理，结果进 jsonl 与日志。
# 用法（板端）：setsid nohup /root/funasr/asr/board-run-probes.sh >/root/funasr/logs/probes.out 2>&1 &
set +e
PY=/root/funasr/venv/bin/python
A=/root/funasr/asr
M=/root/funasr/models
W=/root/funasr/testdata/a067.wav
OUT=/root/funasr/out/probes.jsonl
LOG=/root/funasr/logs/probes.log
THREADS=${THREADS:-4}

echo "=== probes start $(date -u +%FT%TZ) threads=$THREADS wav=$W ===" | tee -a "$LOG"
grep -E 'MemTotal|MemAvailable|SwapTotal' /proc/meminfo | tee -a "$LOG"

for SPEC in "funasr-paraformer $M/Paraformer-large" \
            "funasr-paraformer-online $M/Paraformer-large-online" \
            "sensevoice-sherpa $M/SenseVoiceSmall"; do
  set -- $SPEC
  NAME=$1
  DIR=$2
  echo "--- $NAME ---" | tee -a "$LOG"
  "$PY" "$A/load_probe.py" --backend "$NAME" --model-dir "$DIR" \
      --wav "$W" --seconds 20 --threads "$THREADS" --jsonl "$OUT" 2>&1 | tee -a "$LOG"
  echo "rc=${PIPESTATUS[0]} (0=过门禁 2=超门禁)" | tee -a "$LOG"
done

echo "=== probes done $(date -u +%FT%TZ) ===" | tee -a "$LOG"
grep -E 'MemTotal|MemAvailable' /proc/meminfo | tee -a "$LOG"
free -m | tee -a "$LOG"
echo PROBES-DONE | tee -a "$LOG"
