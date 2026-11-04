# lang/vad —— VAD 分段：能力对照、代码、模型

2026-11-04 她问「webrtc 不支持设置 vad 后静音，silero 支持，fsmn 支持，是不是，ten 呢」，
本页把四个 VAD 的**后静音（句尾静音／end-silence）能力**逐条落到源码／API 证据，并把代码与模型
收在这一个目录。

## 一、结论表（先看这张）

| VAD | 后静音可设吗 | 参数名 | 单位／默认 | 证据级别 |
|---|---|---|---|---|
| **WebRTC** | ❌ **不支持**（库本身没有这个概念） | — | — | API 级（`webrtcvad.Vad` 只有 `is_speech()`） |
| **Silero** | ✅ 支持 | `min_silence_duration` | 秒，默认 **0.5** | 源码级（sherpa v2.0.1 头文件） |
| **Ten VAD** | ✅ 支持 | `min_silence_duration` | 秒，默认 **0.5** | 源码级（sherpa v2.0.1 头文件） |
| **FSMN-VAD** | ✅ 支持 | `max_end_sil` | **毫秒**，库内出厂 800 | 源码级（funasr_onnx `Fsmn_vad_online`） |

**⇒ 你的记忆是对的，只有一处要补正**：不是「silero 支持、ten 不确定」，而是
**silero 和 ten 用的都是同一个字段 `min_silence_duration`（秒，默认 0.5）**——两者在 sherpa-onnx
里的配置结构**形状几乎一样**（`threshold` / `min_silence_duration` / `min_speech_duration` /
`window_size` / `max_speech_duration`），差别只在 `window_size` 的合法取值。

**WebRTC 确实不支持**——这不是「没暴露参数」，而是**库的模型里就没有这个概念**：`webrtcvad`
只给 `Vad.is_speech(frame, rate)` 一个布尔判决，没有段、没有边界、没有静音时长。所以链路里的
「后静音」是**我们自己在外层做的**（双门限迟滞＋累计静音计数），见下面第三节。

## 二、逐个的证据

### WebRTC —— 不支持（API 级）

`webrtcvad` 的公开接口只有三样：构造 `Vad(mode)`、`Vad.is_speech(frame, sample_rate)`、
`Vad.set_mode(mode)`。**没有 segment、没有 silence duration、没有 end-of-speech 的概念。**

```python
# vad/webrtc/webrtcseg.py 实际用法（现行链路）
self.vad = webrtcvad.Vad(int(mode))          # mode 0..3 只影响判决严格度
...
if self.vad.is_speech(chunk, self.rate):     # 返回 True/False，一个子帧一个布尔
```

所以「后静音」在 WebRTC 档里是**外层自己算的**（见第三节）。这也是为什么链路 A 的
`--sil-stop-ms` 是**我们自己的参数**，不是 WebRTC 的。

### Silero —— 支持（源码级）

`sherpa-onnx/csrc/silero-vad-model-config.h`（v2.0.1 tag）：

```cpp
struct SileroVadModelConfig {
  std::string model;
  float threshold = 0.5;                  // 判为语音的概率门限
  float min_silence_duration = 0.5;       // ← 后静音，秒
  float min_speech_duration = 0.25;       // 最短语音才成段，秒
  int32_t window_size = 512;              // 512 / 1024 / 1536 samples @16k
  float max_speech_duration = 20;         // 超长时段门限自动提到 0.9 逼它断，秒
};
```

**注意 `min_silence_duration` 是秒不是毫秒**，而链路现有参数 `--sil-stop-ms` 是毫秒——
换算时别搞错（0.5 s = 500 ms）。

### Ten VAD —— 支持（源码级）

`sherpa-onnx/csrc/ten-vad-model-config.h`（v2.0.1 tag）：

```cpp
struct TenVadModelConfig {
  std::string model;
  float threshold = 0.5;
  float min_silence_duration = 0.5;       // ← 后静音，秒（与 Silero 同名同义）
  float min_speech_duration = 0.25;
  int32_t window_size = 256;              // 只认 160 或 256 samples @16k
  float max_speech_duration = 20;
};
```

**与 Silero 的差异**：`window_size` 只认 **160 / 256**（10 / 16 ms @16k），Silero 认
**512 / 1024 / 1536**（32 / 64 / 96 ms）。Ten 的窗口更小 ⇒ 边界更细，但每帧推理次数更多。

### FSMN-VAD —— 支持（源码级，已删但留证）

`funasr_onnx.vad_bin.Fsmn_vad_online(model_dir, ..., max_end_sil=…)`：

```python
# 已删的 asr/vadseg.py 原文（git 11248e88 可取回）
def __init__(self, model_dir, threads=4, max_end_sil=None, quantize=True, thres=None):
    from funasr_onnx.vad_bin import Fsmn_vad_online
    self.v = Fsmn_vad_online(model_dir, batch_size=1, quantize=quantize,
                             intra_op_num_threads=threads, max_end_sil=max_end_sil)
...
self.end_sil_ms = int(self.v.max_end_sil)     # 库把值暴露回来
```

**单位是毫秒，库内出厂 800**。我方当年把它收到 **400**（10-12 扫档结论：800 会让段撞到 20 s 上限、
`asr_ms` 尾部到 14.7 s；400 把撞限段数打到 0，代价是段数约 2.7 倍）。

## 三、链路自己的后静音参数（外层实现）

`asr_chain.py` 的 `--sil-stop-ms`（默认 **400 ms**）是**链路层面的后静音**，对 rms / webrtc 两档
有效：

```python
# 双门限迟滞（rms / webrtc 共用形状）
is_speech = value >= args.open_dbfs        # 或 webrtc 的语音占比 >= --webrtc-open-ratio
is_sil    = value <  args.close_dbfs       # 或 webrtc 的语音占比 <= --webrtc-close-ratio
# 连续判 is_sil 累计到 --sil-stop-ms 才闭合这一段
```

**所以四档的「后静音」最终都归一到一个数**：

| 档 | 后静音来自哪 | 怎么设 |
|---|---|---|
| rms | 链路外层 | `--sil-stop-ms`（毫秒） |
| webrtc | 链路外层 | `--sil-stop-ms`（毫秒） |
| silero | 库内（`min_silence_duration`）＋外层 | 库参数（秒）→ 需要 wrapper 换算 |
| ten | 库内（`min_silence_duration`）＋外层 | 同上 |
| fsmn（已删） | 库内（`max_end_sil`） | 毫秒直传 |

## 四、目录内容

```
lang/vad/
├─ README.md            本页
├─ webrtc/              WebRTC VAD 全套（2026-11-04 收拢）
│  ├─ webrtcseg.py        子帧占比分段器（现行链路在用）
│  └─ pylite/             `_webrtcvad…so`（aarch64）＋ `webrtcvad.py` 包装＋dist-info
└─ models/
   ├─ silero_vad.onnx       643,854 B   sherpa 版（v4 系）
   ├─ silero_vad_v5.onnx  2,313,101 B   silero 官方 v5.0
   └─ ten-vad.onnx          332,211 B   sherpa 版（**必须用这份**，见下）
```

**校验和**（2026-11-04 下载，可复验）：

| 文件 | 字节 | sha256 |
|---|---|---|
| `silero_vad.onnx` | 643,854 | `9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6` |
| `silero_vad_v5.onnx` | 2,313,101 | `6b99cbfd39246b6706f98ec13c7c50c6b299181f2474fa05cbc8046acc274396` |
| `ten-vad.onnx` | 332,211 | `718cb7eef47e3cf5ddbe7e967a7503f46b8b469c0706872f494dfa921b486206` |

### 下载来源（官方口径，不是随便 curl）

```bash
# sherpa-onnx 官方 release（注意标签是 asr-models，不是 vad-models）
https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx
https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.int8.onnx
https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/ten-vad.onnx
https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/ten-vad.int8.onnx

# silero 官方 v5.0（sherpa 文档里给的备选）
https://github.com/snakers4/silero-vad/raw/refs/tags/v5.0/files/silero_vad.onnx
```

**⚠️ ten-vad 必须用 sherpa 那份**：官方文档明写「We have added some meta data to the original
`ten-vad.onnx`」——**从 TEN-framework 仓库直接下的那份没这些 metadata，sherpa-onnx 加载不了**。

**⚠️ 标签名坑**：`vad-models` 这个 tag **不存在**（会 404）；真正的 tag 是 **`asr-models`**。
官方文档页（`k2-fsa.github.io/sherpa/onnx/vad/silero-vad.html`）里给的链接是权威。

## 五、各 VAD 的模型规格对照

| | Silero（sherpa 版） | Silero v5（官方） | Ten VAD |
|---|---|---|---|
| 文件 | `silero_vad.onnx` | `silero_vad_v5.onnx` | `ten-vad.onnx` |
| 大小 | 644 KB | **2.21 MB** | **332 KB** |
| 窗口 | 512 / 1024 / 1536 | 512（v5 推荐） | **160 / 256** |
| 后静音 | `min_silence_duration` 秒 | 同 | 同 |
| 库 | sherpa-onnx（自带 ORT） | sherpa-onnx | sherpa-onnx |
| 备注 | 板上现役 `klar-voice-gateway` 用的就是 2.3 MB 那份 v5 | | 官方基准全英文，中文未经证实 |

## 六、现状与待办

| 项 | 状态 |
|---|---|
| WebRTC 全套已移入 `vad/webrtc/`，`asr_chain.py` 的 `sys.path` 指向 `vad/webrtc` | ✅ 实测 import 通过 |
| 三份模型已落盘 | ✅ 校验和已记 |
| `--segmenter sherpa`（silero / ten）**尚未实现** | ❌ 只有 `rms` / `webrtc` 两档 |
| 板上 `sherpa-onnx` 2.0.1 的 `VadModelConfig.ten_vad` 字段 | ⚠️ 源码确认 v2.0.1 tag 有；**装的 wheel 是否同 tag 待板子回来实测** |
| 中文场景 silero vs ten 的 A/B | ⚠️ 待实测（ten 训练数据全英文） |

**下一步要做 `--segmenter sherpa`** 的话，按本页的字段名写 wrapper 即可，注意两点：

1. `min_silence_duration` 是**秒**，链路 `--sil-stop-ms` 是**毫秒** ⇒ 换算 `x/1000.0`
2. 链路主循环是 **100 ms 一帧**，而 Ten 窗口 256 样本＝16 ms ⇒ 一帧要切 6 个多子帧；
   Silero 512 样本＝32 ms ⇒ 一帧切 3 个子帧。**判决粒度比 WebRTC 的 20 ms 子帧更细**。

## 七、相关

- 链路侧参数与分段逻辑：`lang/asr/asr_chain.py`（分段器从 `../vad/` 引）（`--segmenter` / `--sil-stop-ms` / `--webrtc-*`）
- 起链脚本：`lang/asr/asr/run_remote_chain.sh`（`SEG=webrtc|rms`）、`run_onnx_chain.sh`
- 已删件的取证：git `11248e88:asr_chain/funasrarm/asr/vadseg.py`（FSMN wrapper 原文）
- sherpa-onnx 源码：`workenv/sherpa_src/`（v2.0.1 的 `silero-vad-model-config.h`、
  `ten-vad-model-config.h`、`vad-model-config.h`）
