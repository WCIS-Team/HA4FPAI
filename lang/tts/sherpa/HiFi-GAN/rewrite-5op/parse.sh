#!/bin/bash
# hifigan_v2 parse. mel [1,80,L] -> icraft wants 4-D, so declare [1,1,L,80] NHWC.
# L here: the mel we feed is 455 frames -> use 600 to match the vocos runs.
set -u
SRC=/mnt/s/work/WCIS/workenv/.tmp_matcher/hifigan
DST=$SRC/icraft
mkdir -p "$DST"

icraft-parse \
  --net_name hifigan_v2 \
  --network "$SRC/hifigan_v2_icraft.onnx" \
  --inputs "1,1,600,80" \
  --inputs_layout NHWC \
  --inputs_dtype FP32 \
  --pre_method nop \
  --pre_scale nop \
  --pre_mean nop \
  --channel_swap nop \
  --target ZG \
  --jr_path "$DST" \
  --log_path "$DST/log" 2>&1 | tr '\r' '\n' | grep -viE '2K|^\s*$|%' | tail -22

echo "--- produced ---"
ls -l "$DST" 2>/dev/null | head
