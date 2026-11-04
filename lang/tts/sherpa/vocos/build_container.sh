#!/bin/bash
# vocos-22khz-univ: parse -> optimize -> quantize(fp16|int8) -> adapt  (container part)
#
# NOTE: this script is reconstructed from the verified pipeline (the original run
# was driven by inline commands); the arguments below are the ones that actually
# produced the working vocos_ZG.json/.raw on the board. The 5th stage
# (icraft-generate) must run in the local Windows CLI -- see gen_local.bat.
#
# Usage:  build_container.sh [fp16|int8]
set -u
QDTYPE="${1:-fp16}"
B=/mnt/s/work/WCIS/workenv/vocos_build_$QDTYPE
V=$B/icraft
mkdir -p "$V/parse" "$V/opt" "$V/quant" "$V/adapt"

ONNX="$B/vocos-22khz-univ.onnx"
[ -f "$ONNX" ] || { echo "MISSING $ONNX -- copy vocos-22khz-univ.onnx here first"; exit 1; }

# 80 zeros for pre_mean; channel_swap 0..79 (mel is 80-band)
MEAN=$(python3 -c "print(' '.join(['0']*80))")
SWAP=$(python3 -c "print(' '.join(str(i) for i in range(80)))")

echo "=== 1/4 parse ==="
icraft-parse \
  --framework onnx \
  --net_name vocos \
  --network "$ONNX" \
  --inputs "1,1,600,80" \
  --inputs_layout NHWC \
  --inputs_dtype FP32 \
  --pre_method nop \
  --pre_scale nop \
  --pre_mean "$MEAN" \
  --channel_swap "$SWAP" \
  --target zhuge \
  --jr_path "$V/parse" --log_path "$V/parse" 2>&1 | tr '\r' '\n' | grep -viE '2K|^\s*$|%' | tail -6
echo "parse rc=$?"

echo "=== 2/4 optimize ==="
icraft-optimize \
  --json "$V/parse/vocos_parsed.json" --raw "$V/parse/vocos_parsed.raw" \
  --jr_path "$V/opt" --log_path "$V/opt" 2>&1 | tr '\r' '\n' | grep -viE '2K|^\s*$|%' | tail -6
echo "optimize rc=$?"

echo "=== 3/4 quantize $QDTYPE ==="
if [ "$QDTYPE" = "int8" ]; then
  # int8 NEEDS a calibration set (mel_*.bin, each 48000 B = 600x80)
  icraft-quantize \
    --json "$V/opt/vocos_optimized.json" --raw "$V/opt/vocos_optimized.raw" \
    --jr_path "$V/quant" --log_path "$V/quant" \
    --target zhuge --qdtype int8 --calib_dir "$B/qtset" 2>&1 \
    | tr '\r' '\n' | grep -viE '2K|^\s*$|%|Quantiz|Calibrat' | tail -8
else
  # fp16 needs NO calibration set
  icraft-quantize \
    --json "$V/opt/vocos_optimized.json" --raw "$V/opt/vocos_optimized.raw" \
    --jr_path "$V/quant" --log_path "$V/quant" \
    --target zhuge --qdtype fp16 2>&1 \
    | tr '\r' '\n' | grep -viE '2K|^\s*$|%|Quantiz|Calibrat' | tail -6
fi
echo "quantize rc=$?"

echo "=== 4/4 adapt ==="
icraft-adapt \
  --json "$V/quant/vocos_quantized.json" --raw "$V/quant/vocos_quantized.raw" \
  --jr_path "$V/adapt" --log_path "$V/adapt" 2>&1 | tr '\r' '\n' | grep -viE '2K|^\s*$|%' | tail -8
echo "adapt rc=$?"
echo "=== done; now run gen_local.bat in the Windows CLI ==="