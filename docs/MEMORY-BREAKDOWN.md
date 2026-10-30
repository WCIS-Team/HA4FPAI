# 内存实测与优化方案（2026-10-30）

## 一、逐组件实测（RSS / PSS，板 993 MB 总内存）

| 服务 | RSS | PSS | 占比 | 说明 |
|---|---|---|---|---|
| **klar-voice-tts** | **199.8 MB** | **196.3 MB** | **41%** | matcha 75.6MB + vocos 53.9MB + numpy/onnx 运行时 |
| klar-voice-asr | 113.0 MB | 109.7 MB | 23% | 14M zipformer int8 |
| klar-voice-wake | 55.5 MB | 51.3 MB | 11% | 3M KWS int8 |
| wcis-face-photo | 53.0 MB | 48.0 MB | 10% | NPU 人脸（含 YuNet+MobileFaceNet 缓存会话）|
| wcis-face-recording | 29.9 MB | 25.0 MB | 6% | 录制服务 |
| wcis-sdi-vpu-view | 14.8 MB | 9.9 MB | 3% | VPU 直播 |
| klar-nlu-engine | 5.4 MB | 5.5 MB | 1% | Rust 静态二进制（极省）|
| wcis-heartbeat | 0.3 MB | 0.3 MB | 0% | — |
| **合计** | **471.7 MB** | **445.7 MB** | 100% | |

```
MemTotal     995 MB
MemFree       76 MB
MemAvailable 261 MB      ← 仍有约 260 MB 余量
```

**结论：当前不是"650 MB"，实测语音链路（4 个 klar 服务）= 373.7 MB RSS。**
我上一轮说的 650 MB 是估的，这里以实测为准并纠正。

## 二、TTS 是最大头，199.8 MB — 怎么降

TTS 内部构成（实测）：
| 文件 | 大小 | 是否常驻 |
|---|---|---|
| `model-steps-3.onnx`（matcha 声学） | 75.6 MB | ✅ |
| `vocos-22khz-univ.onnx`（vocoder） | 53.9 MB | ✅ |
| `lexicon.txt` | 1.4 MB | ✅ |
| sherpa-onnx + numpy + Python 运行时 | ~69 MB | ✅ |

### 方案 A：回退到 piper（省 84 MB，但慢一倍）
| 项 | matcha | piper |
|---|---|---|
| RSS | **199.8 MB** | **148.6 MB** |
| RTF | **1.40** | 3.05 |

**净收益 = −51 MB RSS，代价 = 播报慢 2.2 倍。** 你说过 TTS 就用现在这个，所以**不推荐**。

### 方案 B：TTS 按需加载（省 ~145 MB，推荐）
现在 TTS 服务常驻，模型加载后一直占着。改成**用到才加载、空闲超时卸载**：

```python
# klar_tts.py 改造思路
IDLE_UNLOAD_S = 300   # 5 分钟不说话就释放模型
```

效果：不说话时 TTS 从 199.8 MB 降到 ~50 MB（只剩 Python + numpy）。
代价：下次首播要重新加载（冷加载实测 21–28 s）。
**折中：只卸载 vocos + matcha，保留进程。**

**实测价值：−145 MB，回复延迟 +20s（仅空闲后的第一句）。**

### 方案 C：TTS 换机器（省 199.8 MB 全部）
把 TTS 放到校机或别的机器，板子只发 HTTP 请求。（但你说全放板上，不推荐）

### 方案 D：量化 matcha（未知，需实测）
matcha 声学模型 75.6 MB fp32。如果能转 fp16/int8，可望降到 ~40/~20 MB。
**但这需要在校机做模型转换（板上不能编）。** 属于"有条件做"，先不定。

## 三、ASR 113 MB — 已是最优档

当前是 14M 小模型 int8（RTF 0.40）。另一档 `zh-large-2025-06-30` 峰值 308 MB，已在配置里标为 `realtime: false`。**不建议动。**

可做的：ASR 也按需卸载（省 ~100 MB，代价首句 +6s 冷加载）。ASR 是每次唤醒都用的，卸载不划算。

## 四、KWS 55.5 MB — 可优化

KWS 只做唤醒词检测，模型 4.6 MB。55.5 MB 里大头是 Python + numpy + onnxruntime。

**方案：把 KWS 合进 ASR 进程**（同一个 VoiceKit 单例本来就共享 onnxruntime）。
现在三个服务各起一个 Python 进程，每个都有自己的解释器 + numpy ≈ 各 30 MB 固定开销。

**三个语音服务合并成一个进程，可省 ~2 × 30 MB = 60 MB。**

代价：一个服务崩全崩（但都在一台板上，本来也如此）。这是**最划算的一项。**

## 五、优化优先级（按性价比）

| 优先 | 措施 | 省多少 | 代价 | 建议 |
|---|---|---|---|---|
| **P0** | 三 voice 服务合并成单进程（共享 onnxruntime 本就是设计目标） | **~60 MB** | 无实质代价 | ✅ 做 |
| **P1** | TTS 空闲卸载（5 分钟不说话释放模型） | **~145 MB** | 空闲后首句 +20s | ✅ 做（可调阈值） |
| P2 | TTS 回退 piper | 51 MB | 播报慢 2.2 倍 | ❌ 你说用 matcha |
| P3 | matcha 量化 | ~35–55 MB | 需校机转换 + 音质验证 | 有条件再做 |
| P4 | TTS 移到别的机器 | 200 MB | 违背"全在板上" | ❌ |

**P0 + P1 合计约 −205 MB**，从 445.7 MB PSS 降到约 **240 MB**，`MemAvailable` 从 261 MB 升到约 460 MB。

## 六、当前不做优化的理由

板上还有 **261 MB MemAvailable**，不紧张。优化建议留到：
- 你要加更多模型（比如加 LLM、加防伪模型）时
- 或者内存真的开始吃紧

**我建议先做 P0（三合一），因为它本来就是"共享 env"的设计目标，还能顺带简化部署；P1 等你说要再做。**
