#!/bin/bash
set -u
cd /root/vocos_run || exit 1
BE='ZG330,axi://zg330aiu?npu=0x40000000&dma=0x80000000;Host'
mkdir -p prelu
TAG=01_问候

echo "=== timing: old (5-op hack) vs new (PRelu) ==="
for M in old new; do
  if [ "$M" = "old" ]; then
    J=hifigan_fp16/hifigan_v2_ZG.json; R=hifigan_fp16/hifigan_v2_ZG.raw; NET=hifigan_v2
  else
    J=prelu/hifigan_v2_prelu_ZG.json; R=prelu/hifigan_v2_prelu_ZG.raw; NET=hifigan_v2_prelu
  fi
  rm -rf prelu/t_$M
  icraft-run --json "$J" --raw "$R" --network "$NET" \
    --input prof/${TAG}_in.bin --backends "$BE" --log_time \
    --log_path ./prelu/t_$M > prelu/t_$M.log 2>&1
  echo "$M rc=$?"
  tr -d '\r' < prelu/t_$M.log | grep -a 'network wall' | head -1
done

echo
echo "=== all 4 sentences, new model, with dump ==="
for T in 01_问候 02_天气 03_数字 04_长句; do
  rm -rf prelu/d_$T
  icraft-run --json prelu/hifigan_v2_prelu_ZG.json --raw prelu/hifigan_v2_prelu_ZG.raw \
    --network hifigan_v2_prelu --input prof/${T}_in.bin --backends "$BE" \
    --dump_format HQB --dump_output --log_time \
    --log_path ./prelu/d_$T > prelu/d_$T.log 2>&1
  echo "$T rc=$?"
  tr -d '\r' < prelu/d_$T.log | grep -a 'network wall' | head -1
done