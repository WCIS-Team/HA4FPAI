#!/bin/bash
# Per-sentence CLI runs (Python API reuses buffers -> zeros after the first call).
set -u
cd /root/vocos_run || exit 1
BE='ZG330,axi://zg330aiu?npu=0x40000000&dma=0x80000000;Host'
mkdir -p exact_cli

python3 - <<'PY'
import numpy as np, glob, os
for melp in sorted(glob.glob("/root/vocos_run/exact/*_exact_mel.bin")):
    tag = os.path.basename(melp).replace("_exact_mel.bin", "")
    mel = np.fromfile(melp, np.float32).reshape(80, -1)
    T = min(mel.shape[1], 600)
    pad = np.zeros((600, 80), np.float32)
    pad[:T, :] = mel.T[:T, :]
    pad.reshape(1, 1, 600, 80).tofile("/root/vocos_run/exact_cli/%s_in.bin" % tag)
    print("in", tag, "T=", T)
PY

for TAG in 01_问候 02_天气 03_数字 04_长句; do
  for M in vocos hifigan; do
    if [ "$M" = "vocos" ]; then
      J=fp16/vocos_ZG.json; R=fp16/vocos_ZG.raw; NET=vocos
    else
      J=hifigan_fp16/hifigan_v2_ZG.json; R=hifigan_fp16/hifigan_v2_ZG.raw; NET=hifigan_v2
    fi
    OUT="exact_cli/${TAG}_${M}"
    rm -rf "$OUT"
    icraft-run --json "$J" --raw "$R" --network "$NET" \
      --input "exact_cli/${TAG}_in.bin" --backends "$BE" \
      --dump_format HQB --dump_output --log_path "./$OUT" \
      > "${OUT}.log" 2>&1
    echo "$TAG $M rc=$?"
  done
done
