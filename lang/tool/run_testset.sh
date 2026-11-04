#!/bin/bash
# 板端测试集跑法：cases.tsv 逐条仿实时跑一遍，一条一行读数。
#
# 默认档＝**离线 Paraformer-large int8 ＋ FSMN-VAD 按句切**（她 10-11 改的："不用 sensevoice
# 了改 Paraformer"，见 D-44）。SenseVoice 那档不删，换个变量名就回去了。
# 换档用环境变量，两个维度各一个变量，一次只动一个（控制变量口径同 §十二）：
#   SEG=fvad  按句切（默认）                    SEG=rms  现役双门限能量分段（对照组）
#   BE=funasr-paraformer（默认）                BE=funasr-sensevoice（上一轮定的档，对照组）
# VAD_END ＝句尾静音判闭合的毫秒数，**长尾的唯一旋钮**：不写就吃 asr_chain.py 的默认 400
#   （她 10-12 裁"让 VAD 检测静音更激进些"后收到的档，扫档读数见台账 `#179`）。
#   想复现旧战役的形状写 VAD_END=800。
# 用法（**脚本在 /root/funasr/asr/，不在 testset 目录里**——写相对路径会 No such file）：
#   setsid nohup bash /root/funasr/asr/run_testset.sh > /root/funasr/logs/ts.log 2>&1 < /dev/null &
#   SEG=rms bash /root/funasr/asr/run_testset.sh t01 t03      # 只跑名字里含 t01/t03 的行
# 内存上限（她 10-12 裁"要套"）：每件都过 cap.sh 的 RLIMIT_AS，默认 1536M＝台账 #177 那次
# sizing 实测整链路 VmSize 峰值 810.0 MiB 的 1.89 倍。AS=off 才裸跑。
# ⚠ `--as` 只收"整数＋后缀"：写成 1.5G 会被 cap.sh 判 `--as 解析失败` 并 exit 2，
#   链路根本不起（10-12 真踩过，冒烟第一场就撞上）。
set -u
V=${VENV_PY:-/root/funasr/venv/bin/python}
SEG=${SEG:-fvad}
BE=${BE:-funasr-paraformer}
TS=${TS:-/root/funasr/testset/cases.tsv}
TS_DIR=${TS_DIR:-/root/funasr/testset/16k}
OUT=/root/funasr/out
RUN_TAG=${RUN_TAG:-$(date -u +%m%d_%H%M%S)_${SEG}_${BE}}
AVAIL_MIN=${AVAIL_MIN:-300000}
AS=${AS:-1536M}
HBINT=${HBINT:-10}
mkdir -p "$OUT" "$OUT/segs_$RUN_TAG"

gate() {
  local l1 avail
  while :; do
    l1=$(cut -d' ' -f1 /proc/loadavg)
    avail=$(awk '/MemAvailable/{print $2}' /proc/meminfo)
    if awk -v a="$avail" -v m="$AVAIL_MIN" 'BEGIN{exit !(a>=m)}' \
       && awk -v l="$l1" 'BEGIN{exit !(l<1.2)}'; then return 0; fi
    echo "$(date -u '+%T') wait gate load1=$l1 avail=$avail"
    sleep 20
  done
}

[ -f "$TS" ] || { echo "没有 $TS，先在本机跑 push-testset.py"; exit 1; }
echo "=== run_testset $RUN_TAG seg=$SEG be=$BE start $(date -u '+%F %T UTC')"
tail -n +2 "$TS" | while IFS=$'\t' read -r name dur bytes md5 origin; do
  [ -n "${name:-}" ] || continue
  if [ "$#" -gt 0 ]; then
    hit=0
    for pat in "$@"; do case "$name" in *"$pat"*) hit=1;; esac; done
    [ "$hit" = 1 ] || continue
  fi
  gate
  jsonl="$OUT/${RUN_TAG}__${name%.wav}.jsonl"
  state="$OUT/${RUN_TAG}__${name%.wav}.state"
  echo "--- $name ($dur s) $(date -u '+%T')"
  # shellcheck disable=SC2086
  PRE=()
  if [ "$AS" != "off" ]; then
    PRE=(/usr/local/bin/cap.sh --as "$AS" --tag "${RUN_TAG}__${name%.wav}" --interval "$HBINT" --)
    echo "    cap: --as $AS  hb tag=${RUN_TAG}__${name%.wav} (每 ${HBINT}s 一条，日志 /root/hb/)"
  fi
  VARG=()
  [ -n "${VAD_END:-}" ] && VARG+=(--vad-max-end-sil "$VAD_END")
  [ -n "${VAD_LEAD:-}" ] && VARG+=(--vad-lead-ms "$VAD_LEAD")
  "${PRE[@]}" $V /root/funasr/asr_chain.py --source wav --input "$TS_DIR/$name" \
     --segmenter "$SEG" --backend "$BE" --pace realtime \
     --jsonl "$jsonl" --state "$state" --segs-dir "$OUT/segs_$RUN_TAG" \
     "${VARG[@]}" < /dev/null
  python3 - "$jsonl" "$state" <<'PY'
import json, sys
recs = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
st = json.load(open(sys.argv[2]))
d = [r["dur_s"] for r in recs]
ms = sorted(r["asr_ms"] for r in recs if r.get("asr_ms") is not None)
over = sum(1 for x in d if x >= 19.5)
print("  segs=%d asred=%d 段长中位=%.2fs 最长=%.2fs 撞20s上限=%d rtf_mean=%s lag_max=%s "
      "backlog_s=%s 空文本=%d 错误=%d hwm=%sMiB asr_ms_min/p50/p95/max=%s/%s/%s/%s queue_max=%s "
      "vmswap=%s majflt=%s swap_used_max=%s cma_min=%sKB avail_min=%sMiB"
      % (st["segments"], st.get("segments_asred", 0),
         sorted(d)[len(d)//2] if d else 0, max(d) if d else 0, over,
         st.get("rtf_mean"), st.get("lag_max"), st.get("backlog_s"),
         sum(1 for r in recs if not (r.get("text") or "").strip()),
         st.get("asr_errors", 0), st.get("max_hwm_mib"),
         st.get("asr_ms_min"), st.get("asr_ms_p50"), st.get("asr_ms_p95"),
         max(ms) if ms else None,
         max([(r.get("queue_ms") or 0) for r in recs]) if recs else None,
         st.get("vm_swap_mib"), st.get("majflt_run"), st.get("swap_used_max_mib"),
         st.get("cma_free_min_kb"), st.get("mem_avail_min_mib")))
PY
done
echo "=== run_testset done $(date -u '+%F %T UTC') 正本在 $OUT/${RUN_TAG}__*.jsonl"
