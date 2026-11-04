# lang/kws 可调参数全录

**口径**：本页的每一个参数、每一个默认值，都摘自 **sherpa-onnx v2.0.1 源码**
（`sherpa-onnx/python/sherpa_onnx/keyword_spotter.py` 构造函数的签名与默认值、
`sherpa-onnx/python/csrc/keyword-spotter.cc` 的 pybind 默认值、
`sherpa-onnx/csrc/keyword-spotter.cc` 的 `Register()` / `Validate()`、
`sherpa-onnx/csrc/keyword-spotter-transducer-impl.h` 的 boost 与 reset 机制、
`sherpa-onnx/csrc/utils.cc` 的 `EncodeBase()` 行格式解析、
`sherpa-onnx/csrc/sherpa-onnx-keyword-spotter.cc` 的 CLI 行为）。
**不是转述记忆**，也不是从别的组件类推。

> 版本口径：v2.0.1 是**现役板端**装的那一版（`HA4FPAI/boards/voice_kit.py` 走
> `sherpa_onnx.KeywordSpotter`），源码副本在 `workenv/kws/ex/sherpa-onnx-2.0.1`。
> 模型清单与选型理由见同目录 `README.md`；**参数表在本文，模型在 `models/`**。

**怎么读这张页**：KWS 的旋钮分四层——

| 层 | 在哪 | 改法 |
|---|---|---|
| ① **模型级**（换模型/换 chunk/换关键词表） | `models/` 下选目录 + `keywords.txt` | 改文件 |
| ② **实例级**（`KeywordSpotter(...)` 构造参数） | 代码里显式传 | 改代码/配置 |
| ③ **关键词级**（`keywords.txt` 行内覆盖） | 行内 `:score` / `#threshold` | 改文件，**覆盖②** |
| ④ **运行时**（喂音频的节奏与补白） | 调用方代码 | 改代码 |

优先级：**③ > ② > 库内默认**。这是源码里 `EncodeBase()` 明确实现的：
per-keyword 的 `boost_scores_` / `thresholds_` 非空时，`CreateStream()` 与
`InitKeywords()` 都走 per-keyword 值；只有 per-keyword 列表为空时才回落到全局
`config_.keywords_score` / `config_.keywords_threshold`。

---

## 一、实例级参数全表（`sherpa_onnx.KeywordSpotter(...)`，共 13 个）

默认值抄自 `sherpa-onnx/python/sherpa_onnx/keyword_spotter.py:81-97`（与
`python/csrc/keyword-spotter.cc:34-39` 的 pybind 默认值一致）。

| # | 参数 | 默认 | 干什么 | 调法 |
|---|---|---|---|---|
| 1 | `tokens` | 必填 | 符号表，每行 `symbol integer_id` | 模型自带，**不可换**（换模型就得换 tokens） |
| 2 | `encoder` | 必填 | 编码器 onnx | **chunk-8 与 chunk-16 的文件不通用**，须整套换 |
| 3 | `decoder` | 必填 | 解码器 onnx | 同上 |
| 4 | `joiner` | 必填 | 合成器 onnx | 同上 |
| 5 | `keywords_file` | 必填 | 关键词表 | 见 §四 行格式 |
| 6 | `num_threads` | **2** | onnxruntime 计算线程 | **板上默认给 1**（`HA4FPAI` 的 `klar-voice.json` 是 `1`）——993 MB 的板子上多线程跟 ASR 抢核 |
| 7 | `sample_rate` | **16000** | 训练时的采样率 | **只作声明**（见 §五「它不改你的音频」） |
| 8 | `feature_dim` | **80** | 特征维度（FBank 维数） | 跟模型走，不可调 |
| 9 | `max_active_paths` | **4** | beam 宽度（`modified_beam_search` 用） | 源码注释明说「**只在 decoding_method 被改成 modified_beam_search 时才用**」——默认 greedy 路径下它不生效 |
| 10 | `keywords_score` | **1.0** | 关键词 token 的**加权提升分**（boost） | **越大越容易活下来**，见 §三 |
| 11 | `keywords_threshold` | **0.25** | 触发的**声学概率门限** | **越大越难触发**，见 §三 |
| 12 | `num_trailing_blanks` | **1** | 关键词后要跟几个 trailing blank 才算命中 | 关键词**互相有重叠 token** 时调大（如 8）；源码原话 |
| 13 | `provider` | **"cpu"** | onnxruntime EP | 板上只有 cpu；另有 cuda / coreml / openvino |
| 14 | `device` | **0** | cuda 设备号 | 本板用不到 |

> 严格说是 **13 个可调 + 4 个必填路径**。`device` 只在 `provider="cuda"` 时有意义。

### CLI 版同名参数（`sherpa-onnx-keyword-spotter`）

`config.Register(&po)` 把上面这些注册成了命令行旗标（`--` 分隔）：

```
--tokens --encoder --decoder --joiner --keywords-file
--max-active-paths --num-trailing-blanks --keywords-score --keywords-threshold
--provider --num-threads --decoding-method ...
```

外加 `feat_config` / `model_config` 带来的 `--sample-rate` `--feature-dim` 等。
**注意 CLI 与 Python 的默认值一致**（同源于 `KeywordSpotterConfig` 这一个结构体），
所以本表对两边都适用。

---

## 二、模型级旋钮：**chunk size 是最要紧的那个**

| 模型 | 文件名里的 chunk | 含义 |
|---|---|---|
| zh-en 3M（现役板端） | **chunk-8** | 每次推理吃 8 帧 |
| zh-en 3M | chunk-16 | 每次推理吃 16 帧 |
| wenetspeech 3.3M | **chunk-16** | 每次推理吃 16 帧 |

**约束（踩了会错）**：

1. **encoder / decoder / joiner 三个文件的 chunk 必须一致**，且必须与模型配套。
   板端 `klar-voice.json` 用的是
   `encoder/decoder/joiner-epoch-13-avg-2-chunk-8-left-64[.int8].onnx` 三件套。
2. **chunk 越大 → 单次推理越慢但延迟越稳；chunk 越小 → 越快但短词更容易掉**。
   本仓库 `README.md` 那张实测表里，zh-en 的 chunk-16 int8 是**唯一**出现
   「最差关键词余量掉到 0.3、阈值 0.5 只命中 6/8」的组合 ⇒ **同一个包里选 int8 就该选 chunk-8**。
3. `left-64` = 左侧上下文帧数，是模型自带的，不单独调。

---

## 三、两个核心旋钮的语义（**最容易调错的一对**）

`keywords_score` 与 `keywords_threshold` 作用在**不同环节**，源码路径不同：

```
keywords.txt 行  →  EncodeBase()  →  boost_scores_[] / thresholds_[]  (per-keyword)
                                            ↓
             ContextGraph(current_ids, config_.keywords_score, config_.keywords_threshold,
                          current_scores, current_kws, current_thresholds)
                                            ↓
              解码时：context_score 加进 log_prob（transducer-keyword-decoder.cc:143）
```

| | `keywords_score`（默认 1.0） | `keywords_threshold`（默认 0.25） |
|---|---|---|
| 作用环节 | **解码期加权**——给关键词 token 的路径加 log_prob 偏置 | **判决期门限**——路径概率要超过它才算命中 |
| 方向 | 越大 → 关键词路径越"占便宜" → 越容易命中 | 越大 → 越难命中 |
| 性质 | **加权提升，不是硬约束** | 概率门限 |
| 典型误用 | 以为调大它能"只输出这个词" | 以为调小它能提高识别率 |

> ⚠️ **这条与 `wiki/todo/todo.md` §② 记的口径一致**：sherpa-onnx 的
> hotwords / contextual biasing 是**加权提升，不是硬约束**。KWS 同理——
> `keywords_score` 改的是打分偏置，**不是**"限定输出集合"。
> 想要真正的"只允许输出这几个词"，得走 grammar／受限解码那一路（Vosk context 等）。

**per-keyword 覆盖能做什么**：在 `keywords.txt` 里给某一行单独写 `:score` / `#threshold`，
只调那一个词，不动全局。**这是"小克"这种关键唤醒词与"女儿""法国"这类易混词
分开调优的唯一入口**——全局只有一个 score/threshold 时无法区分对待。

### 源码里两条与调参相关的隐藏机制

| 机制 | 位置 | 行为 | 调参含义 |
|---|---|---|---|
| **1.5 秒自动 reset** | `keyword-spotter-transducer-impl.h` `DecodeStreams()` | 连续静音超过 **1.5 s**（`num_trailing_blanks × 4 × 0.01`）→ **自动 Reset**，状态清空 | 这不是参数，是**硬编码常量**。含义：KWS 不会因为长时间静音而"卡住"；也意味着**唤醒后若 1.5 s 内没说话，词表状态会被清掉** |
| **`num_trailing_blanks` 的双重身份** | 同上 + `transducer-keyword-decoder.cc:159` | 一是"关键词后要几个 blank 才算完整命中"，二是上面 1.5 s 的计算因子 | 调大它 → 命中更"保守"（要更长的静音才收口），同时自动 reset 的触发点也推后 |

---

## 四、`keywords.txt` 行格式（源码级，**自带的示例就是最好的模板**）

解析在 `sherpa-onnx/csrc/utils.cc` 的 `EncodeBase()`：行内用空格分词，逐词判断——
**在符号表里 → 当 token id**；**不在符号表里 → 看首字符**：

| 前缀 | 含义 | 例 |
|---|---|---|
| `:` + 数字 | 该词的 **boost score**（覆盖全局 `keywords_score`） | `:1.5` |
| `#` + 数字 | 该词的 **触发阈值**（覆盖全局 `keywords_threshold`） | `#0.3` |
| `@` + 文字 | 该词的**显示名**（识别到时返回这个） | `@小克` |

**三个前缀可任意组合，顺序不限**。两个模型的官方自带示例：

```
# wenetspeech 3.3M（中文专用，拼音建模）
n ǐ h ǎo j ūn g ē @你好军哥
d àn g d àn g ē @蛋哥蛋哥
x iǎo ài t óng x ué @小爱同学

# zh-en 3M（中英双语；英文走 en.phone 音素）
L AY1 T AH1 P @LIGHT_UP
w én s ēn t è k ǎ s uǒ @文森特卡索
n ǚ ér @女儿
```

| 坑 | 说明 |
|---|---|
| **token 必须在 `tokens.txt` 里** | 不在 → `EncodeBase` 打 `Cannot find ID for token %s`，该行标 `has_oov`，**整份表可能加载失败** |
| **中文按拼音（声母+韵母）拆** | wenetspeech 是**拼音建模**；一字一 token 写 `n ǐ h ǎo`，不是 `你好` |
| **英文走音素** | zh-en 模型的英文词要写成 `L AY1 T AH1 P`（`en.phone` 里的音素集），**不是字母** |
| **`@` 后的名字不能含空格** | `@` 之后到行尾整体作为显示名 |
| **`/ 可在行内追加关键词** | `create_stream("y ǎn y uán @演员/zh ī m íng @知名")`——`/` 分隔多个，**且会与 `keywords_file` 的词表叠加**（源码里 `current_ids.insert(...keywords_id_.begin()...)`） |

---

## 五、运行时旋钮（喂音频的节奏）

这些**不是** `KeywordSpotter` 的构造参数，但决定它能不能命中——**每个实现都得自己写对**。

| 项 | 官方取值 | 干什么 | 谁在用 |
|---|---|---|---|
| **采样率** | **16000**（模型原生） | 喂进去的音频必须是 16 kHz | 库**不替你重采样**——`sample_rate` 参数只是告诉模型"训练时是多少"，你喂 48 kHz 它不会报错，只会识别不出来。**重采样是我方链路的活**（`HA4FPAI` 用 `decimate3-mavg3` 48k→16k） |
| **tail padding** | Python 示例 **0.66 s**；C++ CLI **0.8 s** | 音频末尾补一段静音，把 streaming transducer 的尾部 blank **冲出来** | **不补就可能漏掉最后一个字**。`HA4FPAI` 的 `KwsSession.end()` 用的是配置项 `tail_paddings_s`，值 **0.66**（与 Python 示例一致） |
| **chunk 边界** | 按 chunk 喂 | 每次 `accept_waveform` 的采样数 | 库内部按 chunk 对齐，不用自己对齐 |
| **命中后 reset** | 必须 | 命中后调 `reset_stream(stream)` | 源码示例明确："Remember to call reset right after detecting a keyword"。**不 reset 会连报** |
| **推理批次** | 可批量 | `decode_streams()` 一次解多条流 | 多路 KWS 共用一份模型时省算力 |

---

## 六、`keywords_threshold` 的取值与"判别余量"的关系（读那张实测表的正确姿势）

`README.md` 那张表里的「判别余量」与「阈值 0.5 命中 8/8」是两件事，别混：

| 概念 | 含义 | 在哪 |
|---|---|---|
| **判别余量**（0.675 / 0.637 …） | 关键词相对**非关键词背景**的声学可分性——模型好不好 | 模型固有，**不可调** |
| **`keywords_threshold`** | 判决门限，**可调** | `KeywordSpotter` 构造参数 |
| **阈值 0.5 命中 N/8** | 把判决门限设在 0.5 时，8 个测试词里命中几个 | 由余量与门限共同决定 |

⇒ **调 `keywords_threshold` 是在"余量"这盘棋上选落点**：余量大的词，落点高低都命中；
余量小的词（最容易误触发／漏触发的那几个）才是真正需要调的。
`README.md` 那句保留说得准：**7 段音频、8 个关键词的小样本，分辨不出 0.675 与 0.637 的差别**——
所以**按"要不要英文唤醒词"来选模型，比按这点余量差来选更靠谱**。

### 本机实测（2026-11-04，x86_64 / sherpa-onnx 2.0.1 / int8）

**逐关键词 × 逐音频命中矩阵**（zh-en 3M chunk-8 int8，`keywords_threshold=0.25`，
`tail_paddings_s=0.66`）——**严格对角线**：

| 关键词 ↓ / 音频 → | zh_0 | zh_1 | zh_2 | zh_3 | zh_4 | zh_5 | zh_6 |
|---|---|---|---|---|---|---|---|
| 文森特卡索 | · | · | · | **1** | · | · | · |
| 周望军 | · | · | · | · | · | **1** | · |
| 朱丽楠 | · | · | · | · | · | · | **1** |
| 蒋友伯 | · | · | · | **1** | · | · | · |
| 女儿 | · | · | · | **1** | · | · | · |
| 法国 | · | · | · | **1** | · | · | · |
| 见面会 | · | · | · | · | · | · | **1** |
| 落实 | · | · | · | · | · | **1** | · |

⇒ **8 个中文词全部命中、零误报**。zh_0/1/2 是**负样本**（本就不含关键词），
zh_3 一段含"文森特卡索/法国"，zh_4 含"蒋友伯/女儿"，zh_5 含"周望军/落实"，
zh_6 含"朱丽楠/见面会"。英文两段也全中（`en_0`→LIGHT_UP、`en_1`→LOVELY_CHILD）。
**注意**：wenetspeech 包自带的那 7 段 wav 与 zh-en 的 7 段中文 wav **是同一批音频**，
但**配的关键词表不同**——wenetspeech 用 `test_wavs/test_keywords.txt`（文森特卡索那组），
包根的 `keywords.txt`（你好军哥/小爱同学那组）**不配这些音频**。

**门限扫描（命中段数 / 总段数）**：

| `keywords_threshold` | wenetspeech 3.3M（chunk-16 int8，7 段中文） | zh-en 3M（chunk-8 int8，9 段含 2 英文） |
|---|---|---|
| 0.1 | 4/7 | 6/9 |
| 0.25（默认） | 4/7 | 6/9 |
| 0.5 | 4/7 | 6/9 |
| 0.7 | **2/7**（开始掉） | **3/9**（开始掉） |

⇒ **门限 0.25→0.5 命中数不变，0.7 才掉**——这就是"余量大的词落点高低都命中"的实证。
**掉的是英文**（zh-en 的英文词余量相对弱），中文到 0.7 才开始掉。

**RTF（x86_64 本机，仅供参考，与板端 A55 不可直接比）**：
wenetspeech 3.3M chunk-16 int8 **0.031**；zh-en 3M chunk-8 int8 **0.047**。

> **两包的 int8 件数不一样（实测）**：wenetspeech 3.3M 的 **encoder / decoder / joiner 三件都有 int8**；
> zh-en 3M **只有 encoder / joiner 有 int8，decoder 只有 fp32**（包里根本没有 int8 decoder）。
> 所以 zh-en 的"int8 版"＝ encoder int8 ＋ joiner int8 ＋ **decoder fp32**，
> 这不是挑错了，是上游只发到这个粒度。

**chunk 混用的实测后果**：把 wenetspeech 的 chunk-16 decoder/joiner 塞给 zh-en 的 chunk-8
encoder → **能跑、不报错，但命中 0/9**。
⇒ **chunk 不匹配不会抛异常，只会静默地全废**——这是 `models/` 里两套文件必须分目录放、
不许交叉引用的实测理由。

**复跑入口**：`workenv/kws/verify_kws.py`（命中+门限扫描+chunk 混用）、
`workenv/kws/probe_per_keyword.py`（逐词×逐音频矩阵）。

---

## 七、`lang/server` 里的现状（这页参数什么时候能接上）

链路① 的顺序是 `sar → vad → kws → asr`。**`kws` 档 2026-11-04 已实现**：
`sherpa` 档（`lang/server/backends/front.py` 的 `KwsSherpa`）按本页 §一 的参数口径实现，
配置节是 `lang/config.yaml` 的 `kws_profiles.sherpa`。

- **默认档仍是 `chain.kws: passthrough`＝恒放行**（她 2026-11-04 定的留空口径没变）。
  要生效得把 `chain.kws` 指到 `sherpa` 并填 `kws_profiles.sherpa.model_dir`。
- 档位注册表：`lang/server/backends/__init__.py` 的
  `"kws": {"passthrough": KwsPassthrough, "sherpa": KwsSherpa}`。
- 接口：`kws.run(segs) -> (hit, tag)`（`lang/server/core.py:12`），
  命中与否决定后面 asr 跑不跑（`core.py:176-185`）；`tag` 是命中的显示名。
- 自检：`python -X utf8 tests/smoke.py`（31 项，含"kws 门控短路"）——**改动后必须仍 31/31**。

⇒ **本档暴露的参数**（对应 §一）：`model_dir` / `keywords_file` /
`keywords_score` / `keywords_threshold` / `num_trailing_blanks` /
`num_threads` / `tail_paddings_s` / `sample_rate` / `feature_dim`，
外加可单独覆盖的 `encoder` / `decoder` / `joiner`（**chunk 必须三件一致**）。

---

## 八、一行总结

**KWS 的旋钮只有四个语义**——**选哪个模型（chunk/精度）**、**boost 打分多高**
（`keywords_score`）、**判决门限多严**（`keywords_threshold`）、**词表怎么写**
（`keywords.txt` 的 `:score`/`#threshold`/`@名`）。
其余全是喂音频的纪律：**16 kHz、末尾补 0.66 s 静音、命中后 reset**。
`max_active_paths` 在默认 greedy 路径下**不生效**，别去调它。

---

## 相关文件

| 文件 | 内容 |
|---|---|
| `README.md` | **选型结论**（两个模型怎么选、实测排序表）——本页是参数，README 是选择 |
| `models/` | 两个模型的 int8 副本（**本机副本，不入库**，`*.onnx` 被 `repos/.gitignore` 挡） |
| `lang/server/backends/front.py` | kws 档位占位（待实现的 sherpa 档接口） |
| `lang/server/core.py` | 链路里 kws 门控的位置与签名 |
| `HA4FPAI/boards/voice_kit.py` | **板端现役实现**：`KwsSession` + `VoiceKit.new_kws()` |
| `HA4FPAI/boards/klar-voice.json` | 板端 kws 段的实际取值（`keywords_score` 1.0 / `keywords_threshold` 0.25 / `tail_paddings_s` 0.66） |
