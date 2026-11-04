#!/bin/bash
# Reliable per-sentence run using the vendor CLI (no Python buffer reuse issue).
set -u
cd /root/vocos_run || exit 1
BE='ZG330,axi://zg330aiu?npu=0x40000000&dma=0x80000000;Host'
mkdir -p samples_cli

python3 - <<'PY'
import json, numpy as np, os
META = json.load(open("/root/vocos_run/samples/meta.json", encoding="utf-8"))
for m in META:
    mel = np.fromfile("/root/vocos_run/samples/%s_mel.bin" % m["tag"], np.float32).reshape(80, -1)
    T = min(mel.shape[1], 600)
    pad = np.zeros((600, 80), np.float32)
    pad[:T, :] = mel.T[:T, :]
    pad.reshape(1, 1, 600, 80).tofile("/root/vocos_run/samples_cli/%s_in.bin" % m["tag"])
    print("wrote", m["tag"], "in.bin")
PY

for TAG in 01_问候 02_天气 03_数字 04_长句; do
  for MODEL in vocos hifigan; do
    if [ "$MODEL" = "vocos" ]; then
      J=fp16/vocos_ZG.json; R=fp16/vocos_ZG.raw; NET=vocos
    else
      J=hifigan/hifigan_v2_ZG.json; R=hifigan/hifigan_v2_ZG.raw; NET=hifigan_v2
    fi
    OUT="samples_cli/${TAG}_${MODEL}"
    rm -rf "$OUT"
    icraft-run --json "$J" --raw "$R" --network "$NET" \
      --input "samples_cli/${TAG}_in.bin" --backends "$BE" \
      --dump_format HQB --dump_output --log_path "./$OUT" \
      > "${OUT}.log" 2>&1
    echo "$TAG $MODEL rc=$?"
  done
done
