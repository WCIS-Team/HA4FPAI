#!/bin/bash
# Quantize hifigan_v2. HiFi-GAN has no Cos/Sin, so int8 should be safe.
set -u
B=/mnt/s/work/WCIS/workenv/.tmp_matcher/hifigan
C=/usr/bin
V=$B/icraft
mkdir -p "$V/quant" "$V/adapt" "$V/gen"

echo "=== quantize int8 ==="
icraft-quantize --json "$V/opt/hifigan_v2_optimized.json" --raw "$V/opt/hifigan_v2_optimized.raw" \
  --jr_path "$V/quant" --log_path "$V/quant" --target zhuge --qdtype int8 2>&1 \
  | tr '\r' '\n' | grep -viE '2K|^\s*$|%|Quantizing|Calibrat' | tail -12
echo "rc=$?"
ls "$V/quant" 2>/dev/null | head -4
