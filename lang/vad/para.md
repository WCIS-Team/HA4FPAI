# lang/vad/para.md —— 各 VAD 的全部可选参数

2026-11-04 她问「除 `min_silence_duration` 还有什么可选参数」。本页把**链路里实际存在**的
三个分段档（rms / webrtc / sherpa 系）的**每一个可调参数**列全，默认值全部摘自现行
`asr_chain.py` 与 sherpa-onnx v2.0.1 源码，不是凭印象。

> rms 档是什么、怎么判决的：见本目录 README §三「链路自己的后静音参数」与 `asr_chain.py:180`
> 的 `dbfs()`。本页只记参数。

---

## 一、rms 档的全部可选参数（7 个）

默认值摘自 `asr_chain.py:483-498`。

| 参数 | 默认 | 干什么 | 调法 |
|---|---|---|---|
| `--frame-ms` | **100** | 一帧多少毫秒（判决粒度） | 主循环节奏，与麦对齐；一般不动 |
| `--open-dbfs` | **-45.0** | 开口门限：响过它判语音 | 闹翻的环境调到 -40；安静环境 -50 |
| `--close-dbfs` | **-55.0** | 收口门限：轻过它判静音 | 与 open 保持 8~12 dB 间隔 |
| `--sil-stop-ms` | **600** | **后静音**：连续静音这么久才断句 | 想快断句 → 400；「想了想」停顿多 → 800+ |
| `--min-seg-ms` | **300** | 段最短长度，短于丢弃 | 防咳一声/碰一声成段 |
| `--max-seg-s` | **20.0** | 段最长长度，到了强制断 | 防背景音一直开着段不闭合 |
| `--vad-lead-ms` | **0** | 切音频时在段头多带多少毫秒 | 防吃轻声字头（webui 里默认 200） |

**共享的段缓存参数**（fvad 遗产，rms/webrtc 也受它影响）：

| 参数 | 默认 | 干什么 |
|---|---|---|
| `--vad-keep-s` | **40.0** | PCM 环形缓冲保留多少秒，决定「能往回切多远」 |
| `--vad-min-ms` | **250** | 段短于它丢弃（与 min-seg-ms 二选一用的场景） |

**断句的精确语义**（`asr_chain.py:829` 原文）：

```python
if sil_frames >= sil_stop_frames or seg_frames >= max_seg_frames:
    if seg_frames - sil_frames >= min_seg_frames:   # 扣掉尾部静音后的净语音长度
        # 成段；否则整段丢弃
```

⇒ `min_seg_ms` 卡的是**净语音**（段长减去尾部静音），不是整段。

---

## 二、webrtc 档的全部可选参数（5 个专属 + 共享 7 个）

WebRTC 库本身**只有 `mode` 一个参数**（0..3 的严格度）；其余全是**链路外层**加的。默认值摘自
`asr_chain.py:501-514`。

| 参数 | 默认 | 干什么 | 调法 |
|---|---|---|---|
| `--webrtc-mode` | **3** | 库的 aggressiveness：0 最宽松（漏检少、误判多）、3 最严 | **默认 3 的来历**：10-27 扫档实测 0/1/2 会把整段判语音（60 s 只切 4 段、两段撞 20 s 上限），3 才切出 8 段零撞限 |
| `--webrtc-sub-ms` | **20** | 子帧长：100 ms 主帧切 5 个 20 ms 子帧逐个判 | 只认 10/20/30（库限制） |
| `--webrtc-open-ratio` | **0.4** | 主帧里语音子帧占比 ≥ 0.4 → 语音帧 | 想更严给 0.6（10-27 实测两段测试音频上结果没变） |
| `--webrtc-close-ratio` | **0.2** | 占比 ≤ 0.2 → 静音帧 | 取 0 会把换气/齿音全算语音，断不开句 |
| `--webrtc-open-frames` | **1** | 连续 N 个语音帧才起段 | 防单帧毛刺起段 |

**＋共享 7 个**（`frame-ms` / `open-dbfs`* / `close-dbfs`* / `sil-stop-ms` / `min-seg-ms` /
`max-seg-s` / `vad-lead-ms`——带 * 的两个在 webrtc 档**不生效**，判决走占比不走 dBFS）。

---

## 三、sherpa 系（silero / ten）的全部参数（5 个，库内）

摘自 sherpa-onnx v2.0.1 源码头文件（`silero-vad-model-config.h` / `ten-vad-model-config.h`）——
**两者字段完全同名同义**，只差 `window_size` 合法值：

| 参数 | silero 默认 | ten 默认 | 干什么 |
|---|---|---|---|
| `threshold` | **0.5** | **0.5** | 判为语音的概率门限：预测概率 > 它才算语音 |
| `min_silence_duration` | **0.5** | **0.5** | **后静音**（秒）：静音满这么久才闭合段 |
| `min_speech_duration` | **0.25** | **0.25** | 最短语音（秒）：短于它不成段 |
| `window_size` | **512**（认 512/1024/1536） | **256**（只认 160/256） | 一次推理喂多少样本 |
| `max_speech_duration` | **20** | **20** | 超长段保护：超过后门限自动提到 0.9 逼它断，段闭合后复位 |

**与链路换算的两坑**（实现 `--segmenter sherpa` 时必踩）：

1. **单位**：`min_silence_duration` / `min_speech_duration` 是**秒**，链路 `--sil-stop-ms` 是
   **毫秒** ⇒ `min_silence_duration = --sil-stop-ms / 1000.0`
2. **窗口粒度**：链路主循环 100 ms 一帧（1600 样本 @16k）——
   silero 512 窗口 = 32 ms ⇒ 一帧切 **3 个多**子帧；
   ten 256 窗口 = 16 ms ⇒ 一帧切 **6 个多**子帧（判决更细、推理次数翻倍）

---

## 四、FSMN-VAD 的参数（已删，留档备查）

摘自已删的 `vadseg.py`（git `11248e88` 可取回）。**单位毫秒，与链路参数直接对齐**（当年 fvad 档
用着最顺手就是这个原因）：

| 参数 | 默认 | 干什么 |
|---|---|---|
| `max_end_sil` | **800**（库内出厂） | **后静音**（毫秒）：我方收到 **400**——10-12 扫档实测 800 会让段撞 20 s 上限、`asr_ms` 尾部到 14.7 s；400 把撞限段数打到 0，代价段数 ≈2.7 倍 |
| `thres`（speech_noise_thres） | **0.6**（出厂） | 判决严格度：`exp(speech) ≥ exp(noise) + thres`；调大更严。0.7/0.8/0.9 可用，**1.0 会把整段判成静音** |
| `quantize` | True | 跑 int8 版（`model_quant.onnx`） |
| threads | 1 | ORT intra-op；开 4 会和 ASR 抢核（VAD 单档实测 RTF 0.14） |

---

## 五、横向速查（谁有什么旋钮）

| 旋钮 | rms | webrtc | silero | ten | fsmn(删) |
|---|---|---|---|---|---|
| **后静音** | `--sil-stop-ms`(ms) | `--sil-stop-ms`(ms) | `min_silence_duration`(s) | 同左 | `max_end_sil`(ms) |
| 起段门限 | `--open-dbfs` | `--webrtc-open-ratio` | `threshold` | `threshold` | `thres` |
| 收段门限 | `--close-dbfs` | `--webrtc-close-ratio` | —（概率单门限） | — | （并入 thres） |
| 最短段 | `--min-seg-ms` | 同 | `min_speech_duration`(s) | 同 | （外层 `--vad-min-ms`） |
| 最长段 | `--max-seg-s` | 同 | `max_speech_duration`(s) | 同 | —（20 s 外层） |
| 严格度档位 | — | `--webrtc-mode` 0..3 | — | — | 出厂 0.6 |
| 判决粒度 | `--frame-ms` | `--webrtc-sub-ms` | `window_size` 32ms | **16 ms** | 帧级 |

**一行总结**：**所有档位最终都归到四个语义——开口门限 / 收口门限 / 后静音 / 段长上下限**；
区别只是「门限是能量、占比还是概率」＋「单位是 ms 还是 s」。换 VAD 时按这张表对号入座，链路外层
那 7 个参数（除两个 dBFS）全部照用。
