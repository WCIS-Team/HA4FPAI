# 板端 CPU-only 基准（espeak-ng / Piper / Kokoro）

> 这里回答一个问题：**"不碰 NPU，这块板子能跑什么 TTS、多快、吃多少内存？"**
>
> 上面所有"跑不动"的结论都出自这批读数。**脚本与原始 jsonl 的正本在**：
> `wiki/PS/Backend/mods/audio/ARM CPU TTS性能测试.md` 及其挂的板上目录
> （`/root/ttsbench`、`/root/mf3`）——本目录**只留口径与结论**，不复制数据。

---

## 1. 三引擎对照

| 引擎 | 路线 | 峰值内存 | RTF | 判定 |
|---|---|---:|---:|---|
| **ekho** | 录音拼接 | **~20 MB** | **0.49** | ✅ **可用（现役）** |
| **eSpeak NG** | 共振峰合成 | 很小 | 快 | ⚠️ 能跑，但机械腔 |
| **Piper** | VITS 单体 | **391–755 MB** | **0.65–0.81 常驻**（快于实时 23–54%） | ❌ **OOM**，但不是慢 |
| **Kokoro-82M** | 神经 | 430 MB 级 | 更重 | ❌ 慢（zh_only RTF 0.12 的相对口径、慢 8×） |

**结论（真数钉死）**：这块 4×A53 板上，**"中文听感 + 实时"的交集仍只有录音拼接一条路**。
神经引擎要换算力预算——**除非把某一段搬到 NPU**（这就是 `../sherpa/vocos/`、`../sherpa/HiFi-GAN/` 存在的意义）。

---

## 2. ★ OOM 是这批数据的核心现象

板子 **993 MB RAM**，跑神经 TTS 时：

- **12 次 OOM 击杀**，`rc=137`（SIGKILL）
- 逐次读数（`free -m` 六列、`dmesg` 解析）在
  `ARM CPU TTS性能测试.md` §十三 / §十四

### 一个重要的方法论发现

> **`avail` 在这颗板上没有预测力**（含 `CmaFree` 与当时的 `buff/cache`）。
> 别拿"看起来还有多少空闲"当门禁依据——它杀你之前 `avail` 可能还很漂亮。

同时：`/sys/class/thermal` 空、无 hwmon ⇒ **内核零温度读数**；
`/proc/asound/cards` = `no soundcards` ⇒ **板上没有放音通路**。
所以"板子热不热""有没有声音"这类问题，**软件侧判不了**，只能靠外接。

---

## 3. 几个易被误读的读数

| 现象 | 真相 |
|---|---|
| `峰值 kB = 0` 的行 | **不是占 0 MB，是没读到**——进程在建 recognizer 之前就抛了，没到采样点 |
| `type=case` 行数为 0 的 jsonl | 被 OOM 杀在落盘之前，只有 `meta`/`load`/`phonemes` 三段 |
| 日志里有、jsonl 里没有的行 | 进程被杀前没落盘，证据等级只能是 B |

---

## 4. 环境信息（可复现的前提）

| 项 | 值 |
|---|---|
| 板子 | FMQL30TAI676H 悟净，4×Cortex-A53 @1 GHz |
| RAM | 993 MB |
| CMA | 256 MB |
| OS | Ubuntu 20.04 / arm 内核 |
| onnxruntime | 板上 py3.8 有，线程数 2（基准里固定 `ORT_NUM_THREADS=4` 的是另一批） |

---

## 5. 相关

- **读数正本**：`wiki/PS/Backend/mods/audio/ARM CPU TTS性能测试.md`
- 结论与取舍：`wiki/PS/Backend/mods/audio/实时伪tts.md`
- 神经模型的编译结果：`wiki/PL/MOD/modellist/TTS同量级候选进NPU横评.md`
- 现役路线：`../ekho-linux/README.md`
- 两段式方案：`../sherpa/matcha/`、`../sherpa/vocos/`、`../sherpa/HiFi-GAN/`