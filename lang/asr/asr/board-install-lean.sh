#!/bin/sh
# 板端精简安装：只装 FunASR ONNX 运行时里"有 cp38/aarch64 轮子"的那几颗。
# funasr-onnx 用 --no-deps 装，librosa 由 /root/funasr/asr/shims/librosa.py 顶掉，
# sentencepiece 不装（SenseVoice 的 funasr-onnx 档因此走不通，改用 sherpa-onnx 测 SenseVoice）。
PY=/root/funasr/venv/bin/python
IDX=https://mirrors.aliyun.com/pypi/simple/
echo "=== start $(date -u '+%F %T UTC')"
$PY -m pip install --no-input -i $IDX --no-deps funasr-onnx==0.4.3
echo "rc-funasr-onnx=$?"
$PY -m pip install --no-input -i $IDX kaldi-native-fbank scipy PyYAML jieba
echo "rc-deps=$?"
$PY -m pip install --no-input -i $IDX sherpa-onnx==1.13.8
echo "rc-sherpa=$?"
$PY -m pip list --format=freeze
echo "=== done $(date -u '+%F %T UTC')"
