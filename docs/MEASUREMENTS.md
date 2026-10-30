# 实测账（2026-10-30，板 192.168.1.113 / aarch64 A55×4 / 993 MB）

> 本页只放**config 级实测证据**。凡标「官方表」的都不是板上读数。

## 1. KWS 身份与性能

**板上用的就是 `sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20`**（此前我只答文件名有误，以 md5 为准）：

| 文件 | 大小 | md5（板上 = 官方包，逐字节一致） |
|---|---|---|
| `encoder-epoch-13-avg-2-chunk-8-left-64.int8.onnx` | 4,600,657 | `aa58d38b4b7c1736b74c8d2d44c5a5d0` |
| `tokens.txt` | 1,928 | `b60d5ed9664a7e73a310d80f1a4d0313` |
| `decoder-epoch-13-avg-2-chunk-8-left-64.onnx` | 759,829 | `e03554cd2a58c923d4e3c5f38ec80925` |
| `joiner-epoch-13-avg-2-chunk-8-left-64.int8.onnx` | 86,629 | `f6ba5acd8ed292284459520a594c1ea3` |
| `en.phone` | 3,330,061 | `221f57ea5a86fdaedc935bca846e46c0` |

- **RSS 81.7 MB**（`klar-voice-wake` 常驻）
- **RTF 0.499**（9 条 wav 均值，单线程；区间 0.477–0.525）
- 该包同时带 chunk-8 与 chunk-16 两档，int8 与 fp32 都有；当前用 chunk-8 int8。

## 2. TTS 候选对比（板上实测，非官方表）

| 模型 | 架构入口 | 模型文件 | **RTF** | 峰值 RSS | 冷加载 |
|---|---|---|---|---|---|
| piper `zh_CN-xiao_ya-medium`（现用） | `OfflineTtsVitsModelConfig` | 18.6 MB | **2.90–3.52** | 148.6 MB(服务常驻) | 26 s |
| **matcha `icefall-zh-baker`** | `OfflineTtsMatchaModelConfig` | 75.6 MB + vocoder 53.9 MB | **1.29–1.82** | **212 MB** | 25.6–27.7 s |
| vits-zh-aishell3 int8 | `OfflineTtsVitsModelConfig` | 39.9 MB | **7.71–9.46** ❌ | 162 MB | 11.8 s |

matcha 线程扫描（2/3/4 线程）：RTF 1.52–1.82 / 1.40–1.62 / 1.29–1.60 —— **3 线程后收益递减**。

**关键结论**
1. **matcha 能共用现有 env**：`sherpa_onnx.OfflineTtsMatchaModelConfig` 在已装的 sherpa-onnx **2.0.1** 里就存在，不需要换包、不需要额外 onnxruntime。
2. **但它必须配 vocoder**：把 `model-steps-3.onnx` 同时当 acoustic+vocoder 会 `Segmentation fault`（C++ 报 `Unsupported model_type: matcha-tts` / `Unknown model type in vocoder!`）。官方用法是另下 `vocos-22khz-univ.onnx`（53.9 MB），也可以选 hifigan v1/v2/v3。
3. **matcha 比 piper 快约 2 倍**，但**多占 64 MB 内存**（vocoder 的代价）。板上总内存 993 MB，四条服务现在用 ~374 MB，加 matcha 到 ~438 MB，仍可行但更紧。
4. **aishell3 是坑**：官方 RTF 表写 0.365@1线程，我板上实测 7.7–9.5，差 20 倍。**官方表那个 aishell3 条目不是这个 30M/329MB 的模型**，绝对值不可搬用。

## 3. HA 体积构成（PyPI 实测，`homeassistant 2026.10.4`）

| 项 | 值 |
|---|---|
| wheel 压缩体积 | **62.7 MB** |
| **解压后** | **238.0 MB / 55159 文件** |
| 其中 `homeassistant/components/` | **228.7 MB（96%）/ 1523 个集成目录** |
| 非组件部分（核心 + helpers + 工具） | 9.3 MB |

**大在哪**：1523 个集成模块，Top30 就吃掉约 55 MB（`mqtt` 4.47 MB、`home_connect` 4.30 MB、`zha` 3.84 MB、`template` 3.54 MB、`knx` 3.10 MB …）。你没用的几百个集成全在盘上。

## 4. 板与发动机现状

| 服务 | RSS |
|---|---|
| klar-voice-wake | 81.7 MB |
| klar-voice-asr | 139.5 MB |
| klar-voice-tts | 148.6 MB |
| klar-nlu-engine | 4.1 MB |
| **合计** | **~374 MB**（993 MB 中） |

## 5. 已知缺陷：klar 播报混德语

无真实 HA 实体表时，engine 用内置**默认德语公寓**，中文词映射到德语实体 id：

- `打开客厅的灯` → `light.wohnzimmer` → 播报「客厅的灯开了。」（正常）
- `打开风扇` → 播报 **「Lüfter开了。」**
- `打开窗帘` → 播报 **「Rollo Wohnzimmer开了。」**
- `打开所有灯` → 播报 **「Wohnzimmer或Esszimmer或…?」**

**接上真实 HA（`--config-dir` 指向真 `.storage/core.entity_registry`）后此现象消失**，因为房间/设备名换成你的真实命名。所以短语缓存必须等接上 HA 再做。

## 6. 测试样本的真实含义（修正）

`KWS/test_wavs/zh_0..2.wav` **不是唤醒词样本**，而是 `en_0/en_1.wav` 英文段的**中文译读**（`en_trans.txt` 里有原文）。它们不命中任何关键词属**正常**，但**不能称为「正确拒识」**——那是我上一轮的说法，用词错误。真正证明「拒识能力」需要专门的负样本集，目前没有。
