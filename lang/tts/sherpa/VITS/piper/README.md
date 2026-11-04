# Piper（VITS 单体 TTS）—— ❌ 板端 CPU 就过不去

> **这不是"另一个声码器"，是"整个 TTS"**：Piper 把 前端 + 声学模型 + 声码器 **打包成一个 ONNX**，
> 文本进、波形出，**中间张量不落地**。
>
> 正因为它切不开，**也就无法只把声码器那段搬到 NPU**——这跟 `../../vocos/`、`../../HiFi-GAN/`
> （可以单独下沉）是结构性差别。

---

## 1. 它是什么

Piper = **VITS** 架构的端到端 TTS（`text → phoneme → mel → waveform` 全在一个模型里）。
本板上的三个中文变体：

| 模型 | 大小 |
|---|---:|
| `vits-piper-zh_CN-huayan-medium` | 63.2 MB |
| `vits-piper-zh_CN-chaowen-medium` | 63.2 MB |
| `vits-piper-zh_CN-xiao_ya-medium` | 63.2 MB |

板上已有 `/mnt/data/models/tts-piper`（20 MB，现役链路用的一部分）。

---

## 2. ★ 为什么失败（两层，别混）

### 2.1 第一层：**板端 CPU 就跑不动**（这是主要死因）

| 指标 | `piper_zh_CN-huayan-medium` | `piper_en_US-lessac-medium` |
|---|---:|---:|
| **峰值 RSS** | **391.5 MB** | **754.8 MB** |
| 单进程 HWM | 134.1 MB | 132.4 MB |
| 模型加载耗时 | **23.2 s** | **21.8 s** |
| **标准 RTF**（生成/音频，<1＝快） | **常驻 0.65–0.81 ＝ 快于实时 23–54%**；冷启动 0.80 | 常驻 0.90–1.01（临界） |
| 是否被 OOM | **是**（`rc=137` = SIGKILL） | **是** |

**板子只有 993 MB RAM**，Piper 峰值 391–755 MB ⇒ **CPU-only 基准里被 OOM 杀了 12 次**。
逐次读数在 `wiki/PS/Backend/mods/audio/ARM CPU TTS性能测试.md` §十四。

> **一句关键对照**：`piper_zh_CN` 常驻 **标准 RTF 0.65–0.81（快于实时 23–54%）**——**它是算得动的**；
> 而 matcha+vocos 在"只把 vocos 搬上 NPU"之后整链是 **0.716–0.777（快于实时 29–40%）**，**两者速度同量级**。
> **真正的差别在内存**（见 §2.1）。

### 2.2 第二层：**编译期也过不去**

在 `TTS同量级候选进NPU横评` 的 10 个候选实测里，三个 piper 变体全部失败：

| 模型 | icraft 结果 | 断点 |
|---|---|---|
| `vits-piper-zh_CN-chaowen-medium` | `rc=-1073741819`（**段错误**） | `Split` 改写引发崩溃 |
| `vits-piper-zh_CN-huayan-medium` | 同上 | 同上 |
| `vits-piper-zh_CN-xiao_ya-medium` | 同上 | 同上 |

⇒ **连"编过"都没做到，更谈不上上板**。

---

## 3. ⚠️ 口径澄清（很重要，别写成错的）

**Piper 从来没有尝试上 NPU。** 那批测的是 **CPU-only 基准**。

所以正确的说法是：
- ✅ 「Piper 在**这块板上跑得动但放不下**（常驻快于实时 23–54%；但峰值 391–755 MB ⇒ **12 次 OOM 击杀**）」
- ✅ 「Piper 是单体模型，**切不开**，所以没法像 vocos 那样只把一段搬去 NPU」
- ❌ **不要写**「Piper 上 NPU 失败」——没试过，而且短期也没试的意义

---

## 4. 与 matcha+vocos 的结构性对比

| | **Piper（失败）** | **matcha+vocos（成功）** |
|---|---|---|
| 架构 | 单体 VITS，端到端出波形 | **两段式**：matcha 出 mel、vocos 出频域 |
| 中间张量 | **不落地**，切不开 | `mel[1,80,T]` 是明确边界（48000 B 定长） |
| 部署位置 | **只能整条放 CPU** | 只把 vocos 放 NPU，其余留 CPU |
| 板上内存 | 峰值 391–755 MB | vocos 模型 31.3 MB |
| 是否 OOM | **是（12 次）** | 否 |
| 速度（标准 RTF） | **0.65–0.81 常驻（快于实时 23–54%）** | 整链 0.716–0.777（快于实时 29–40%） |
| 失败性质 | **资源问题** | 资源问题**被拆分解决了** |

**一句话**：Piper 的失败是「整条链在这块 993 MB 板上既放不下也跑不赢」；
matcha+vocos 的成功是「把链切开，只搬占 33–36% 耗时、31 MB 的那一段」——
**不是同一件事的两种做法，是把问题重新分解了。**

---

## 5. 另外几个 VITS 候选也失败

见 `../vits-others/`：`sherpa-onnx-vits-zh-ll`、`vits-icefall-zh-aishell3`、
`vits-melo-tts-zh_en`、`vits-zh-hf-eula` —— **全部在 icraft 编译阶段失败**
（`tensor(int64)` 类型错误、`rc=19020`、`rc=26000` 等）。

---

## 6. 结论

**Piper 这条路在 ZG330 上不通，而且不是"再调调参数"能通的。**
- 想在这块板上做中文 TTS：走 `../../ekho-linux/`（拼接，RTF 0.49、RSS ~20 MB）
  或 `../../matcha/` + `../../vocos/`（两段式，可下沉 NPU）
- 想要 Piper 的音色：它需要一块内存 ≥1.5 GB 且能跑得动 VITS 的板子，
  **与"把声码器搬到 NPU"这件事无关**

---

## 7. 相关

- CPU 读数正本：`wiki/PS/Backend/mods/audio/ARM CPU TTS性能测试.md`（§十四 12 次 OOM）
- 取舍讨论：`wiki/PS/Backend/mods/audio/实时伪tts.md`
- 候选横评：`wiki/PL/MOD/modellist/TTS同量级候选进NPU横评.md`
- 文档正文对照表：`wiki/PS/Backend/MatchaVocosNPU.md` §11.4