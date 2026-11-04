#!/bin/bash
# hifigan_v2 on ZG330. Output is a waveform [N,L] directly (no iSTFT needed).
set -u
cd /root/vocos_run || exit 1
IN=io/NPU_in_1_1_600_80.bin
BE='ZG330,axi://zg330aiu?npu=0x40000000&dma=0x80000000;Host'

for k in 1 2 3; do
  rm -rf "hi_$k"
  icraft-run --json hifigan/hifigan_v2_ZG.json --raw hifigan/hifigan_v2_ZG.raw \
    --network hifigan_v2 --input "$IN" --backends "$BE" \
    --dump_format HQB --dump_output --log_time --log_path "./hi_$k" \
    > "hi_$k.log" 2>&1
  RC=$?
  HARD=$(tr '\r' '\n' < "hi_$k.log" | grep -a "total_hard_time" | tail -1 | sed 's/.*total_hard_time: \([0-9.]*\) ms.*/\1/')
  echo "run$k rc=$RC hard=${HARD}ms"
  if [ "$k" = "1" ]; then
    echo "--- dumps ---"
    ls "hi_$k"/*/ftmpHQB/ 2>/dev/null | head
    echo "--- log tail ---"
    tr '\r' '\n' < "hi_$k.log" | grep -viE '2K|^\s*$|%|Running' | tail -6
  fi
done
