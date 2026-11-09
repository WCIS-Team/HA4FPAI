# testdata

`a067.wav`：16 kHz 单声道测试样本（来自原板端 `/root/funasr/testdata/a067.wav`）。

用途：

```bash
python3 mimo_voice.py --input testdata/a067.wav --bridge-url http://127.0.0.1:18095/api/v1/asr --tts none --once
```

没有 MiMo API key 时，可先起 `asr/mock_asr_server.py`，用假 ASR 验证上传口子。
