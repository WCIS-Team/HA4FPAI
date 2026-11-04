#!/bin/bash
# 板端 ASR 选型战役：等板子空出来 → RAM 门禁探针 → 逐个后端仿实时完整测试，全程记内容量。
#
# 为什么要等：板子 993 MB 无 swap。另一条会话的 TTS 基准（bench_kokoro）实测 RSS 649 MB、
# 371 % CPU，把可用内存压到 135 MB。这时候装载 230 MB 的 onnx 只会把 OOM killer 叫起来，
# 而它按占用分数挑人，先杀的很可能是对方那个更大的进程——那是她的基准，不能碰。
#
# 用法（板端，分离跑）：
#   setsid nohup /root/funasr/asr/board-run-campaign.sh >/root/funasr/logs/campaign.out 2>&1 &
# 看进度：
#   tail -40 /root/funasr/logs/campaign.log; grep -c . /root/funasr/out/probes.jsonl
# 中止（只杀自己这一棵）：
#   pkill -f 'board-run-campaign[.]sh'
#
# 门禁：连续 3 次（每 20 s 一次）同时满足
#   MemAvailable >= 560000 kB（比 512 MiB 留一层余量，保证峰值判得准）
#   除本战役自己的进程外，没有别的进程 RSS > 200 MB
# 才开跑；跑的过程中每 5 s 记一行 avail/load/top，用来事后判读数有没有被污染。
set +e

PY=/root/funasr/venv/bin/python
A=/root/funasr/asr
F=/root/funasr/asr_chain.py
M=/root/funasr/models
TD=/root/funasr/testdata
OUT=/root/funasr/out
LOG=/root/funasr/logs/campaign.log
CONT=/root/funasr/logs/contention.log
STATE_DIR=/dev/shm/asr
GATE_KB=560000
ROUNDS=3
WAVES="a067 b123"
THREADS=${THREADS:-4}

mkdir -p "$OUT" "$STATE_DIR"
: > "$LOG"
: > "$CONT"

say() { echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOG"; }

avail_kb() { awk '/MemAvailable/{print $2}' /proc/meminfo; }

other_heavy() {
  # 除本战役进程树以外，RSS>200MB 的进程数（对方基准一旦出现就非 0）
  ps -eo pid,rss,args --sort=-rss --no-headers | awk '
    { args = ""; for (i = 3; i <= NF; i++) args = args " " $i
      if ($2 > 204800 && args !~ /awk|campaign|asr_chain|load_probe|board-run|sleep|bash|ps -eo/) {
        n++; s = s " " $1 ":" int($2 / 1024) "M:" args } }
    END { printf "%d%s", n + 0, s }'
}

say "campaign start threads=$THREADS gate=${GATE_KB}kB rounds=$ROUNDS waves='$WAVES'"

N=0
while true; do
  AV=$(avail_kb)
  HEAVY=$(other_heavy)
  CNT=${HEAVY%% *}
  if [ "$AV" -ge "$GATE_KB" ] && [ "$CNT" = "0" ]; then
    N=$((N + 1))
    say "gate ok $N/$ROUNDS avail=${AV}kB"
    [ "$N" -ge "$ROUNDS" ] && break
  else
    N=0
    say "gate wait avail=${AV}kB heavy=${HEAVY}"
  fi
  sleep 20
done
say "gate passed, board free"

(
  while true; do
    echo "[$(date -u +%FT%TZ)] avail=$(avail_kb)kB load=$(cut -d' ' -f1 /proc/loadavg) top=$(ps -eo rss,args --sort=-rss --no-headers | head -1 | awk '{printf "%dM %s", $1/1024, $2}')" >> "$CONT"
    sleep 5
  done
) &
SAMP=$!

# ---- 第一步：RAM 门禁 ----
: > "$OUT/probes.jsonl"
for SPEC in "funasr-paraformer $M/Paraformer-large" \
            "funasr-paraformer-online $M/Paraformer-large-online" \
            "sensevoice-sherpa $M/SenseVoiceSmall"; do
  set -- $SPEC
  say "probe $1"
  "$PY" "$A/load_probe.py" --backend "$1" --model-dir "$2" \
      --wav "$TD/a067.wav" --seconds 20 --threads "$THREADS" --jsonl "$OUT/probes.jsonl" >> "$LOG" 2>&1
  say "probe $1 rc=$? records=$(grep -c . "$OUT/probes.jsonl")"
done

# ---- 第二步：过了门禁的后端逐个仿实时完整测试 ----
PASSING=$("$PY" - <<'PY'
import json
for line in open("/root/funasr/out/probes.jsonl"):
    try:
        r = json.loads(line)
    except Exception:
        continue
    if r.get("pass_gate"):
        print("%s %s" % (r["backend"], r["model_dir"]))
PY
)
say "passing: $(echo "$PASSING" | tr '\n' ';')"

for SPEC in $PASSING; do
  set -- $SPEC
  NAME=$1
  DIR=$2
  [ -z "$NAME" ] && continue
  for TAGW in $WAVES; do
    say "realtime $NAME on $TAGW"
    "$PY" "$F" --source wav --input "$TD/$TAGW.wav" \
        --backend "$NAME" --backend-dir "$DIR" --threads "$THREADS" --pace realtime \
        --segs-dir "/root/funasr/segments/${NAME}_${TAGW}" \
        --jsonl "$OUT/${NAME}_${TAGW}.jsonl" \
        --state "$STATE_DIR/state_${NAME}_${TAGW}" \
        --queue-alarm 8 >> "$LOG" 2>&1
    say "realtime $NAME $TAGW rc=$? state=$(tr -d '\n' < "$STATE_DIR/state_${NAME}_${TAGW}" | cut -c1-260)"
  done
done

kill "$SAMP" 2>/dev/null
say "campaign done; contention samples=$(grep -c . "$CONT")"
echo CAMPAIGN-DONE | tee -a "$LOG"
