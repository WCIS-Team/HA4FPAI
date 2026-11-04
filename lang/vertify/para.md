# lang/vertify 可调参数全录（CAM++ 声纹验证）

**口径**：本页每个参数、每个默认值，都摘自 **sherpa-onnx v2.0.1 源码**（本机副本
`workenv/kws/ex/sherpa-onnx-2.0.1/`，逐条标 `文件:行`）；每个数字都是 **2026-11-04 在本机实测**
（Windows / x86_64 / Python 3.13.11 / `sherpa_onnx` 2.0.1 / `onnxruntime` 1.24.2 / `numpy` 2.3.5）。
**不是转述记忆，也不从 kws/vad 类推**——这三条链路在参数面上差别很大（见 §六「与 kws 相反的那条」）。

> **本机 ≠ 板端**：本页所有 ms / MB 都是 x86_64 的数，板上（aarch64 轮子）**未测**。
> 厂商 NPU 侧的 CAM++ int8 是另一条路（编译期量化），数字见 §四末，别与本页的 ORT 量化混着引用。

---

## 〇、交付件与来源（`models/`，本机副本不入库）

模型来源＝**3d-speaker 的中文 CAM++**，由 ModelScope `iic/speech_campplus_sv_zh-cn_16k-common`
导出成 ONNX 后随 sherpa-onnx 发布页分发（ONNX 自己的 metadata 里就写着这个 url）。
**上游只发 fp32，没有 int8**；`models/` 里的 int8 是**本仓自己量化出来的**（§四，产物来自
`workenv/vertify/quant.py`，不是下载件）。

| 件 | 字节 | sha256（前 16 位） | 来源 | 入库 |
|---|---|---|---|---|
| `3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx` | 28,281,138（27.0 MiB） | `f682b514c05d947e` | GitHub release `speaker-recongition-models`，与官方 `checksum.txt` 逐字节一致 | ❌ `*.onnx` 被 `repos/.gitignore:24` 挡 |
| `3dspeaker_speech_campplus_sv_zh-cn_16k-common.int8.onnx`（动态量化 QInt8 权重 / per-channel） | 8,595,741（8.2 MiB） | `51ba2a525c304d69` | **本仓量化**（`quant.py`），校准/口径见 §四 | ❌ 同上 |
| `test_wavs/*.wav`（14 段，3 人 × `-sr` / `-test-sr`） | 1,923,010（1.83 MiB）合计 | 逐件对得上官方 checksum | 同一 release 的官方示例音频 | ❌ 本机副本（与 `kws/models/test_wavs` 同处置） |
| `checksum.txt` | 3,477 | — | 官方全表（该 release 全部说话人模型 + 14 段示例 wav 的 sha256） | ✅ 文本，可入库 |

```bash
# 重拉正本（唯一官方发布页；本机 huggingface.co 返 401，与 kws/README.md 同一条实测）
https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx
```

> **本机没有 GitHub 官方 CLI**（`gh` 未装），所以走 PowerShell 自带 `Invoke-WebRequest`
> （`workenv/vertify/dl.ps1`，带重试与 `.part` 半件保护）。下载后逐件对 `checksum.txt` 校验：
> 本次 15 件（1 模型 + 14 wav）**全对**。

---

## 一、四层旋钮与优先级

| 层 | 在哪 | 本仓能不能改 |
|---|---|---|
| ① **模型级** | `models/` 选 fp32 还是 int8；换模型（另有英文、中英 advanced、eres2net 系列） | 改文件／改档里 `model` |
| ② **实例级** | `SpeakerEmbeddingExtractorConfig` 只有 **4 个**（§二） | 改代码／配置 |
| ③ **判决级** | `SpeakerEmbeddingManager` 的 `threshold`（§三） | 改代码／配置，**这是声纹唯一真正"调"的旋钮** |
| ④ **运行时** | 喂音频的节奏与终帧（§六） | 每个实现自己写对 |

**优先级：③ > ② > 库内默认。** 声纹的可调面很窄——构造参数只有 4 个，真正决定
"判通过还是判拒绝"的是 ③ 的余弦门限；② 只影响算力，不影响判据。

---

## 二、实例级参数全表（`sherpa_onnx.SpeakerEmbeddingExtractor(...)`，共 4 个）

默认值摘自 `csrc/speaker-embedding-extractor.h:17-29`（结构体成员）与
`python/csrc/speaker-embedding-extractor.cc:43-56`（pybind 默认值）。两边同源、值相同。

| # | 参数 | 默认 | 干什么 | 本仓裁定 |
|---|---|---|---|---|
| 1 | `model` | 必填 | ONNX 路径 | **必填**。`Validate()` 只查两件事：非空 + 文件存在（`cc:38-51`），**不查 metadata 是否合法**（那是后面构造时才炸） |
| 2 | `num_threads` | **1** | ORT intra-op 线程 | 板上给 **1**（与 kws/asr 抢核，且 993 MB 内存）；本机实测 4 线程只快约 27%（§四表） |
| 3 | `debug` | `false` | 装载时把 model metadata 打到 stderr | 取数/排查时开，正常关 |
| 4 | `provider` | `"cpu"` | ORT EP | 板上只有 cpu；另有 cuda / coreml（`cc:34-35` 只认这三种） |

**只读、不可调但必须知道的三个值**（都从模型 metadata 读，`speaker-embedding-extractor-model.cc:96-103`）：

| 项 | 值 | 谁在读 |
|---|---|---|
| embedding 维度 | **192** | `Dim()`（`general-impl.h:31` 读 `output_dim`）。**不是 512**——512 是 eres2net/ecapa 那一族（`wiki/todo/dev/io待测放sop.md:291` 记的 eres2net 才是 512） |
| 输入形状 | `[N, T, 80]`（**T 动态**） | 一次吃整段，见 §六 |
| 输出形状 | `[N, 192]`（符号维名 `BatchNormalizationembedding_dim_0`） | 未归一化，见 §三 |

**CLI 同名旗标**（`Register()`，`cc:26-36`）：`--model --num-threads --debug --provider`。
CLI 与 Python 共用同一个 config 结构体，本表对两边都适用。

**模型派发**：构造时先开一次 session 读 metadata 的 `framework`（`impl.cc:38-87`），
只认 `wespeaker` / `3d-speaker` / `nemo` 三个值；**其它值直接 `SHERPA_ONNX_EXIT(-1)`**。
本件是 `3d-speaker` ⇒ 走 `GeneralImpl`（wespeaker 同路），nemo 走另一套。

> ⚠️ **量化过的模型必须先保住 metadata**：`framework` / `output_dim` / `sample_rate` /
> `normalize_samples` / `feature_normalize_type` 少一个就装载失败或行为走样。ONNX 量化器默认保留，
> 但做完**一定要 `sherpa_onnx.SpeakerEmbeddingExtractor` 真装一次**（本仓 int8 两档都装过）。

---

## 三、判决级：`SpeakerEmbeddingManager`（声纹真正的旋钮在这里）

接口全表（摘自 `csrc/speaker-embedding-manager.h`，实现 `manager.cc`）：

| 方法 | 语义 | 源码位置 |
|---|---|---|
| `add(name, v)` | 注册一个嵌入；**入库时做 L2 归一化**；同名已存在返 `False` | `manager.cc:25-41` |
| `add(name, v_list)` | 多段登记的**求和后归一化**（等价"先平均再归一"，源码明说不再除个数） | `:43-87` |
| `search(v, threshold)` | 全体比对，取最大余弦；**最大分 < 门限就返 `""`** | `:117-135` |
| `verify(name, v, threshold)` | 只跟一个已注册者比，余弦 ≥ 门限返 `True` | `:171-189` |
| `score(name, v)` | 只返相似度，不判决；**名字没注册返 `-2.0`**（不是 0） | `:191-206` |
| `remove` / `contains` / `num_speakers` / `all_speakers`（返回**已排序**）/ `dim` / `get_embedding` | 管理面 | `:89-115, 208-303` |

三条必须记住的语义：

1. **判据是余弦相似度**，两边都先 L2 归一化（`manager.cc:122-126`、`179-182`）。
2. **`extractor.compute()` 出来的向量不是单位向量**（本机实测范数 **11.5–16.1**）——
   自己算 cosine 时**必须自己归一化**，别以为拿到手就能点积。
3. **`threshold` 是余弦门限，取值 0..1**。官方 Python 示例默认 **0.6**
   （`python-api-examples/speaker-identification.py:88`）。

### 本机实测：门限扫描（3 人 / 7 段 test，小样本）

enroll＝每人 `-sr` 段各算嵌入后 `add(name, [ ... ])`（多段平均）；test＝7 段 `-test-sr`。
每行是"同人接受 / 异人误接受"，共 7 / 14 对。

| `threshold` | fp32 同人 / 异人 | dyn8（本仓 int8）同人 / 异人 |
|---|---|---|
| 0.30 | 7/7 / **4/14** | 7/7 / **5/14** |
| 0.40 | 7/7 / **1/14** | 7/7 / **3/14** |
| 0.45 | 7/7 / 1/14 | 7/7 / 1/14 |
| **0.50** | 7/7 / **0/14** | 7/7 / **0/14** |
| 0.60（官方默认） | 7/7 / 0/14 | 7/7 / 0/14 |
| 0.75 | 7/7 / 0/14 | 7/7 / 0/14 |

⇒ **本仓取 0.5**（与 kws 的 `keywords_threshold` 同一个数，语义不同、别互相抄）：
0.30–0.45 有跨人误接受，0.50 起干净，同人到 0.75 都不掉。
**保留**：3 人 7 段分辨不出 0.5 与 0.6 的差别——这是"选个安全落点"，不是 EER 寻优。
真调门限要用本仓自己的登记库与真实采集音频重扫。

本机余弦分布（fp32，供参照）：同人 **0.778–0.860**，异人最高 **0.452**（leijun vs liudehua 0.34–0.45，
fangjun vs 另两人 0.007–0.28）。⇒ 余量约 0.33。

---

## 四、模型级：int8 三档实测，以及**为什么交付的只有一档**

上游只有 fp32；`quant.py` 做了动态量化，`quant_static.py`/`quant_variants.py`/`quant_excl.py`
试了静态量化的四组设置。全部在本机同一批音频上跑完（14 段 60.07 s / 7 段验证 / 3 人登记）。

| 档 | 体积 | 图里的算子（关键） | 装载 | 嵌入 vs fp32 余弦 | 7 段验证 | 判别余量 | RTF（1 / 4 线程） | 结论 |
|---|---|---|---|---|---|---|---|---|
| **fp32（官方）** | 27.0 MiB | `Conv`×225 | 398–478 ms | —（基准） | 7/7 | 同人 0.778–0.860，异人 ≤0.452 | **0.0129 / 0.0094** | ✅ 交付 |
| **dyn8（本仓动态量化）** | 8.2 MiB | `ConvInteger`×225 ＋ `DynamicQuantizeLinear`×223（节点 3102→4340） | 781–1071 ms | **0.946–0.977**（均值 0.967） | 7/7 | 与 fp32 同级：0.50 起同为零误接受，0.30–0.40 略多误接受（4→5、3→4、1→3 of 14） | **0.2128 / 0.1089**（**16× 慢**） | ✅ 交付（只买体积/常驻） |
| sta8-QUInt8（静态，per-tensor 激活） | 8.3 MiB | `QLinearConv`×225 | 975 ms | **0.265–0.538**（均值 0.450） | 7/7（**侥幸**：全靠 argmax） | **塌**：同人 0.64–0.89、异人 0.41–0.75；门限 0.30 就 **14/14 误接受** | 0.0073 / 0.0067（**1.8× 快**） | ❌ 不可用 |
| sta8-QInt8（静态，对称 QInt8 激活） | ≈8.3 MiB | 同 QLinearConv 但走 QInt8 激活 | — | 0.817–0.882 | 未全量 | 未全量 | **0.2914**（比 dyn8 还慢） | ❌ 不留 |

**根因（op 直方图实证，不是猜）**：

- ORT 的动态量化对 Conv 只能生成 **`ConvInteger`**（动态 scale），本机走上这条慢路径 ⇒
  fp32 的 225 个 `Conv` 变成 225 个 `ConvInteger` + 223 个 `DynamicQuantizeLinear`，RTF 0.013→0.213。
  **这不是量化没做好，是这条路在 x86 上就这德行。** 4 线程能把差距从 16× 压到 11.6×，但仍慢。
- 静态量化给的是 **`QLinearConv`**（MLAS 的 int8 卷积核），确实快 1.8×——但这颗模型的激活动态范围
  太宽（fbank 特征归一化后 std 还有 2.7–4.4，且 CAM 层有 `Gather/Unsqueeze/Concat` 支路），
  per-tensor QUInt8 把精度压坏：嵌入与 fp32 只剩 0.45 余弦、异人分被抬到 0.5+。
- **救不回来的三组尝试**（都有 JSON 留档）：① 校准方法换 entropy / percentile 99.99 ⇒ 余弦
  **逐位相同**（`0.4659 / 0.4573 / 0.5064`，说明两个选项没改动 scale）；② 排除直接吃图输入的
  `Transpose` ⇒ 0.4765（没变）；③ 再排除最后 2 个 Conv ⇒ 0.4730（更差）。
- 与 `tts/para.md` 那条红线同一类失败：**量化不能只看体积**（那里是 vocos int8「`Cos` 算错」0.12；
  这里是声纹 int8 判别余量塌）。**本仓对 CAM++ 也只准把 int8 当"省盘/省常驻"用。**

**内存实测（独占进程，`rss_probe.py`）**：

| 档 | 装载后常驻 | 峰值 | 说明 |
|---|---|---|---|
| fp32 | 98.2 MB | 126.5 MB | 基线 |
| dyn8 | 83.7 MB | **126.0 MB** | 常驻只省 **14.5 MB**，**峰值几乎不变**（ORT 工作区占大头） |

⇒ **int8 的内存收益比"文件小 3.3×"看起来的要小得多**；板上真要省内存，得看常驻那 14.5 MB 值不值。

**板上没测**。要在板上拿 int8 的**算力**收益，正路是 **icraft NPU 那条**（编译期量化，不是 ORT 量化）：
厂商 ZG 档 `CAMpplus_int8.toml`（`saturation=kld` / `per=channel`，官方索引标 `ZG_30TAI_int8_kld-pc`），
精度 **EER 0.15173 / MinDCF 0.71847**（float 0.14227 / 0.66743），8bit 14.09 ms、16bit 19.569 ms（BY100TAI）。
**两条路的数不能互相引**：那边吃固定 `[1,365,80]`（3.65 s 定长），这边 T 动态。

---

## 五、前端 fbank（**库内固定，extractor 一个都不暴露**）

特征不是调用方给的，是 `GeneralImpl.CreateStream()` 按**模型 metadata + 结构体默认值**建的
（`general-impl.h:33-40`）。默认值出自 `csrc/features.h:16-91`；本件实际生效值如下：

| 项 | 值 | 出处 |
|---|---|---|
| `sampling_rate` | **16000** | 模型 metadata `sample_rate`，不是结构体默认值 |
| `feature_dim`（mel bins） | **80** | 结构体默认 80，与模型一致 |
| `low_freq` / `high_freq` | **20 Hz** / **-400** ⇒ 实际 7600 Hz | `features.h:28, 38`（负值＝从 Nyquist 减） |
| `dither` | **0**（关） | `features.h:46` |
| `snip_edges` | **false** | `features.h:55`（末帧不丢，帧数≈100×秒） |
| `frame_length_ms` / `frame_shift_ms` | **25 / 10** | `features.h:56-57` |
| `remove_dc_offset` / `preemph_coeff` | **true / 0.97** | `features.h:59-60` |
| `window_type` | **povey** | `features.h:61` |
| `normalize_samples` | **true**（波形按 `[-1,1]`，不再乘 32768） | metadata `normalize_samples=1`；`features.h:53` |
| `is_librosa` | false | `features.h:58` |
| `feature_normalize_type` | **`global-mean`** | 3d-speaker 特有：**每维减去整段均值**（`general-impl.h:69-82` 调 `SubtractGlobalMean`，`:103-110` 实现） |
| `is_mfcc` / `is_whisper` / `is_t_one` | 全 false ⇒ 走 fbank | `features.cc:62-70` |

**本机实测（14 段）**：**100.0 帧/秒**（99.88–100.09），T 从 218（2.18 s）到 824（8.24 s）；
归一化后特征 `mean≈0.000`、`std 2.7–4.4`、极值 ±20 量级。⇒ **T ≈ 100 × 秒数，±1 帧**。

**怎么把"库实际喂给网络的那份特征"打出来**（本页 §五/§四 的数就是这么来的，`featdump.py`）：
在官方 ONNX 上挂一个 `Flatten(axis=0)` 并把**输入接到第 0 个输出**，sherpa 的 `Compute()` 取
`outputs[0]`、按 `shape[1]` 拷 float ⇒ 拿回的就是 `T×80` 特征。自检：把 dump 出的特征直接喂回
**真** fp32 图（ORT），嵌入与 extractor 的嵌入余弦 **1.000000** ⇒ 前端还原无误。

> ⚠️ **与 kws 相反的那条**：KWS 档**不会**替你重采样（喂 48k 只会识别不出来，`kws/para.md` §五）。
> 这个档**会**——`FeatureExtractor::AcceptWaveformImpl()` 在采样率不一致时内部建
> `LinearResample`（`features.cc:106-131`，lowpass = 0.99×0.5×min(sr)、width 6），并打一行
> `Creating a resampler: ...` 到 stderr。实测：48 kHz 直接喂，嵌入与原生 16k 的余弦 **0.9997**
> （可以用，但**别依赖它**——链内 sar 已统一到 16k）；8 kHz 只有 **0.820**（高频没了，别这么干）。

---

## 六、运行时纪律（这些不是构造参数，但决定能不能用）

| 项 | 规矩 | 实测依据 |
|---|---|---|
| 采样率 | 链内 **16 kHz**（`core.WORK_RATE`）喂进来；48k 也能跑但不建议 | 见 §五 |
| 波形格式 | 单声道、float32、**[-1,1]**（`normalize_samples=1` 时不再乘 32768） | metadata + `features.h:53` |
| **终帧必须打** | `stream.input_finished()`，否则末帧不出 | 库语义（`features.cc:133-151`） |
| **一个 stream 只出一次嵌入** | `compute()` 取走**所有未处理帧**；再调就没有帧了（`IsReady`＝"还有未处理帧"，`general-impl.h:42-44`） | 源码语义 |
| **中途 compute 会吃帧** | 在 0.5 s 处先 `compute()` ⇒ 该嵌入与整段嵌入余弦只有 **0.5656**，剩下的帧再算得 **0.9411** | 实测（`probe.json` edge.feed） |
| 分段喂 vs 整段喂 | **逐位相同**（`np.array_equal` = True）：帧缓存在库内，不影响结果 | 实测 |
| 短音频 | **10 ms 也出 192 维**（0.01/0.02/0.03/0.05/0.1/0.2/0.3 s 全部 ok） | 实测 |
| 长音频 | 一次算整段：**60.07 s ⇒ 993.8 ms**（RTF 0.0165），内存不炸但**没有任何分块流式** | 实测 |
| 嵌入维度 | **192**（定长） | metadata |
| 该拿多长的段 | 建议按 VAD 段喂（2–6 s 一段）；本机 14 段 2.18–8.24 s 全部正常 | 实测 |

**`sherpa_onnx` 的 Python 面**只有 4 个方法 + 1 个只读属性：
`extractor.create_stream()` / `stream.accept_waveform(sr, samples)` / `stream.input_finished()` /
`extractor.is_ready(stream)` / `extractor.compute(stream)` / `extractor.dim`。
`OnlineStream` 与 KWS/ASR 共用同一个类，但**别把 KWS 的 `online_stream` 用法照抄**——
这里的 stream 没有 `input_finished` 之后的解码循环。

---

## 七、接进 `lang/server` 的现状与契约

**现状（2026-11-04）**：链的 `Chain.STAGES = ("sar","vad","kws","asr","llm","tokenizer","tts")`
（`server/core.py:111`）**没有声纹这一级**；`server/backends/__init__.py` 的 `REGISTRY` 里也没有 `spk`/`verify`。
⇒ **本页只落模型与参数面，档位没实现**，`lang/config/server.yaml` 的 `chain` 节里也不存在这一档。

要接进来时（按 `backends/__init__.py` 头注那三步走）建议的契约：

| 项 | 建议 | 理由 |
|---|---|---|
| 档名 | `spk: {sherpa: …}`（与 `kws: sherpa` 同款命名） | 一组件一文件、一档一名 |
| `run(segs) -> (name, score)` | 逐 VAD 段各算一个嵌入；**不要拿整条录音算一个** | 一录多段时"先说 A 后说 B"要分得开 |
| 运行时 | `self.shared.sherpa()`（**共用那一份 sherpa-onnx 运行时**） | 她的口径（`core.py:21-24`）：VAD/KWS/ASR/TTS 共用同一个 env，别新开 ORT |
| 登记库 | `SpeakerEmbeddingManager(dim)`；enroll 用 `add(name, [多段嵌入])` | 多段平均＋归一化是库内做的（`manager.cc:43-87`） |
| 门限 | **0.5**（§三） | 本机实测 0.5 起跨人零误接受 |
| 内存 | 本机峰值 126 MB 是**独占进程**的数；同进程叠加按 session 增量估（权重 27 MiB / 8.2 MiB） | `rss_probe.py` |

---

## 八、一行总结与红线

**声纹的旋钮只有三个语义**：**用哪颗模型（fp32 还是 int8）**、**判据阈值（余弦，取 0.5）**、
**喂多长的一段（按 VAD 段，16 kHz，整段算）**。构造参数只有 4 个，前端 fbank 一个都不暴露。

红线（背下来）：

1. **别用静态量化 QUInt8 那档**：又快又小，但判别余量塌（异人 0.41–0.75、门限 0.30 就 14/14 误接受）。
   本仓对 CAM++ 的 int8 只有"动态量化（`ConvInteger`）"这一档可用，代价是**慢 16×**。
2. **int8 买的是体积/常驻，不是速度**：8.2 MiB / 常驻 −14.5 MB；峰值几乎不变（ORT 工作区占大头）。
3. **判据是余弦**，`score()` 对未注册名字返 **-2.0**（不是 0）；`compute()` 出的向量**未归一化**（范数 11.5–16.1）。
4. **embedding 是 192 维**，不是 512（512 是 eres2net/ecapa）。
5. **这个档会内部重采样**（与 kws 相反）：48k 能跑（cos 0.9997），8k 别喂（cos 0.820）。
6. **一个 stream 只出一次嵌入**，中途 `compute()` 会把已就绪帧吃掉（实测余弦掉到 0.5656）。
7. 厂商 NPU 的 int8 是**编译期量化**（ZG `kld-pc`，EER 0.15173 / 14.09 ms，固定 `[1,365,80]`），
   **不要与本页的 ORT 量化数混引**。

---

## 未验证项（别当成结论）

- **板端全部未测**：本页 ms/MB 都是 x86_64；aarch64 上 `ConvInteger` / `QLinearConv` 的快慢、板端
  wheel 是否暴露同一个 `SpeakerEmbeddingExtractor` 都没验。
- **阈值是 3 人 7 段的小样本**，不是 EER；真实登记库（多人、同人不同麦/不同距离）要重扫。
- **int8 的板上表现**未测（只有本机 x86 的 16× 慢这一条）。
- **没有接进链路**（§七），`lang/server` 里没有这一档，`/health` 也没有它。
- 静态量化只试了 4 组设置（minmax/entropy/percentile/对称 QInt8 + 首尾 Conv 排除），
  **没有试 BN 融合后再量化、也没有试按层混合精度**——这两条也许能救回 sta8，本轮没做。

---

## 相关文件 / 复跑入口

| 文件 | 内容 |
|---|---|
| `models/3dspeaker_speech_campplus_sv_zh-cn_16k-common/` | 模型 + `test_wavs/` + 官方 `checksum.txt`（本机副本，不入库） |
| `workenv/vertify/dl.ps1` | 官方发布页下载（带重试、`.part` 保护） |
| `workenv/vertify/quant.py` | 动态量化（**交付的 int8** 就是它出的） |
| `workenv/vertify/quant_static.py`、`quant_variants.py`、`quant_excl.py` | 静态量化四组尝试（失败档的证据，留在 `workenv/vertify/variants/`） |
| `workenv/vertify/featdump.py` | dump 库实际喂进网络的 fbank 特征（挂 `Flatten` 探针模型，自检 cos=1.000000） |
| `workenv/vertify/probe.py`、`probe2.py`、`rss_probe.py` | 全量读数 / 三档对比 / 单档内存（结果 `probe.json`、`probe2.json`、`quant_variants.json`、`quant_excl.json`） |
| `../kws/para.md` | 同一条链的 KWS 参数（**那条不重采样、那条的门限是声学概率，别互抄**） |
| `../vad/para.md`、`../asr/para.md`、`../tts/para.md` | 其余链路组件的参数面；`tts/para.md` §四 是"int8 只准 fp16"的先例 |
| `../server/core.py`、`../server/backends/__init__.py` | 链路契约与档位注册表（声纹档要加在哪） |
| 上游源码 | `workenv/kws/ex/sherpa-onnx-2.0.1/sherpa-onnx/csrc/speaker-embedding-*.{h,cc}`、`features.{h,cc}` |