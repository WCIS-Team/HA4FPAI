#!/bin/bash
# hifigan_v2 with PRelu: parse -> optimize -> quantize(fp16) -> adapt  (container part)
set -u
B=/mnt/s/work/WCIS/workenv/hifigan_prelu
V=$B/icraft
mkdir -p "$V/parse" "$V/opt" "$V/quant" "$V/adapt"

ONNX="$B/hifigan_v2_prelu_c2d.onnx"
[ -f "$ONNX" ] || { echo "MISSING $ONNX"; exit 1; }

echo "=== 1/4 parse ==="
icraft-parse \
  --framework onnx \
  --net_name hifigan_v2_prelu \
  --network "$ONNX" \
  --inputs "1,1,600,80" \
  --inputs_layout NHWC \
  --inputs_dtype FP32 \
  --pre_method nop \
  --pre_scale nop \
  --pre_mean nop \
  --channel_swap nop \
  --target zhuge \
  --jr_path "$V/parse" \
  --log_path "$V/parse" 2>&1 | tr '\r' '\n' | grep -viE '2K|^\s*$|%' | tail -6
echo "parse rc=$?"
ls "$V/parse"/*.json 2>/dev/null | head -2

echo "=== 2/4 optimize ==="
icraft-optimize \
  --json "$V/parse/hifigan_v2_prelu_parsed.json" \
  --raw  "$V/parse/hifigan_v2_prelu_parsed.raw" \
  --jr_path "$V/opt" --log_path "$V/opt" 2>&1 | tr '\r' '\n' | grep -viE '2K|^\s*$|%' | tail -6
echo "optimize rc=$?"

echo "=== 3/4 quantize fp16 ==="
icraft-quantize \
  --json "$V/opt/hifigan_v2_prelu_optimized.json" \
  --raw  "$V/opt/hifigan_v2_prelu_optimized.raw" \
  --jr_path "$V/quant" --log_path "$V/quant" \
  --target zhuge --qdtype fp16 2>&1 | tr '\r' '\n' | grep -viE '2K|^\s*$|%|Quantiz|Calibrat' | tail -6
echo "quantize rc=$?"

echo "=== 4/4 adapt ==="
icraft-adapt \
  --json "$V/quant/hifigan_v2_prelu_quantized.json" \
  --raw  "$V/quant/hifigan_v2_prelu_quantized.raw" \
  --jr_path "$V/adapt" --log_path "$V/adapt" 2>&1 | tr '\r' '\n' | grep -viE '2K|^\s*$|%' | tail -8
echo "adapt rc=$?"

echo "=== produced ==="
ls -l "$V"/*/*.json 2>/dev/null | awk '{printf "%8d  %s\n", $5, $9}'