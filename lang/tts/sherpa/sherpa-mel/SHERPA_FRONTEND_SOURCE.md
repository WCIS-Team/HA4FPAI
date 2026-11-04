# sherpa-onnx matcha 中文前端：源码正本（读代码得来，非猜测）

> 来源：`sherpa-onnx/csrc/offline-tts-matcha-impl.h`、`offline-tts-frontend.cc`、
> `lexicon.cc`（GitHub master），以及校机上安装的 `sherpa_onnx` 包与
> `sherpa_onnx/utils.py`。

## 0. 打开 sherpa 自己的 debug（本轮最重要的工具）

**`OfflineTtsModelConfig` 有 `debug` 字段（默认 `False`）**。设 `debug=True`，
sherpa 会把分词/句子切分直接打到 stderr：

```python
mc = sherpa_onnx.OfflineTtsModelConfig(matcha=..., num_threads=1, debug=True)
tts = sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(model=mc, max_num_sentences=1))
tts.generate(TEXT, sid=0, speed=1.0)
```

**实测输出（`"你好，我是你的语音助手。"`）**：

```text
Text is too long. Split it into 2 batches. batch size: 1. Number of sentences: 2
```

⇒ **sherpa 把这句话切成了 2 句**（在 `，` 处），并**分两批各跑一次 matcha**。

`GenerationConfig` 也有 `extra`（类型必须是 `Mapping[str, str]`，如
`{"debug": "1"}`），但那条只影响 gen_config 一层，**模型级 debug 更直接**。

## 1. 为什么不该自己拼 token

`sherpa` 的 C++ 前端做了一堆我原本不知道的事。**正确做法是照抄它，或直接调它**，
而不是按 `lexicon.txt` 逐字查表。我此前逐字查表得到 10 个 token、整句当一条序列，
与 sherpa 的真实处理（**切 2 句 + 各自加 blank**）不一致，所以帧数差 2 倍、波形相关≈0。


## 2. 完整流水线（`OfflineTtsMatchaImpl::Generate`）

```
text
 ├─ NormalizeText()            // 若配置了 rule_fsts/rule_fars
 ├─ ReplacePunctuations()      // ，→,  。→.  、→,  ；→;  ：→,  …→...  ！→!  ？→?
 ├─ SplitByAllPunctuation()    // 在 , . ! ? ; : 处切句，标点被剥掉
 ├─ MergeShortSentencesByWords(min_words_in_sentence=5)
 ├─ SplitLongSentenceByWords(max_words_in_sentence=20)
 ├─ 把"只有标点的句子"并回前一句
 ├─ TokenizeSentence() → TokenizeFromLexiconStr()
 │    · 空格→空格token；标点→token_str2id_ 查表
 │    · 其余走 lexicon **最长匹配**（max_lexicon_phrase_len_）
 │    · CJK 单字兜底：SplitUtf8 逐字查
 │    · 查不到 → "OOV ... skipped"
 ├─ 句尾若以标点结尾，补一个标点 token
 ├─ ApplyBosEos()               // use_eos_bos=0 时直接平铺
 └─ if (meta_data.add_blank)  x = AddBlank(x, meta_data.pad_id)   ★ 关键
```

## 3. ★ `add_blank` 是此前漏掉的关键

```cpp
if (meta_data.add_blank) { for (auto &k : x) k = AddBlank(k, meta_data.pad_id); }
```
`AddBlank(ids, pad)` 生成 **`2n+1`** 个 token（首尾各一个 pad，中间交替）。

实测（`"你好，我是你的语音助手。"`，10 个音节）：

| 送进去的 token 序列 | token 数 | matcha 输出帧数 |
|---|---:|---:|
| 裸 10 个音节 | 10 | 106 |
| **加 blank（pad_id=1）** | **21** | **187** |
| 加 blank 再加句号 token | 22 | 202 |
| sherpa 实际 | ? | **197–198** |

**⇒ `add_blank` 让帧数从 106 涨到 187，方向完全正确，但还差 10 帧。**

## 4. 模型 metadata（实测读出，用来伪造模型时必须逐项对齐）

```
model_type = matcha-tts          ← 写成 matcha-zh 会报 "Unsupported matcha tts model"
has_espeak = 0                   language = Chinese
n_speakers = 1                   jieba = 1
pad_id = 1                       sample_rate = 22050
use_eos_bos = 0                  num_ode_steps = 3
```

**注意**：`sil` / `eos` / `#0` 这三个符号**不在** `matcha-icefall-zh-baker/tokens.txt` 里，
所以 `Lexicon::ConvertTextToTokenIdsChinese` 里那些 `if (token2id_.count("sil"))` 分支全部不生效。

`tokens.txt` 共 2069 行，其中：
- 1648 个带声调音节（`ni3` 这类）
- 412 个不带声调音节（`ni` 这类）
- 5 个单字母（`a e m n o`）
- 8 个符号：`! " ' , . : ? _`
- **第 1 行是 `"  0"`——空格本身是 token(id 0)**，这行会让 Python 版
  `utils.text2token` 的 `assert len(toks) == 2` 报错（`line.strip().split()` 只剩 1 列）。

## 5. 用 sherpa 自己的 `text2token`（已被我调通）

`sherpa-onnx-cli.exe text2token` 需要 `sentencepiece` + `pypinyin` + `jieba`（已装）。
直接跑会因 tokens.txt 第 1 行断言失败；**做一个只改这一行、其余 id 全不变的副本**即可：

```
D:\yzd\ttsbench\tokens_fixed.txt      # 第1行改成 "__SP__ 0"，其余 2068 行 id 不变
sherpa-onnx-cli.exe text2token \
  --tokens  tokens_fixed.txt \
  --tokens-type fpinyin \
  <输入txt> <输出txt>
```

实测输出 **12 项**（10 音节 + `，` + `。`）。**但控制台是 GBK，中文被打成 `?`，
导致 token 查表失败** ⇒ 下一步要把输入/输出按 UTF-8 处理（或改用管道/重定向到文件后按 UTF-8 读）。

## 6. 其他实测事实

- `OfflineTtsMatchaModelConfig` 默认值：**`noise_scale = 1.0`**、`length_scale = 1.0`。
  （我先前以为是 0.667，错了。）
- `length_scale` 默认就是 **1.0**，**不需要调到 1.86/2.3**；我之前把它调大是为了
  硬凑时长，属于"用错参数掩盖 token 数不对"——这正是用户指出的错误。
- Python API **没有** `debug` 开关；C++ 的 `config.model.debug` / `gen_config` 的
  `debug` 才会打印 "Sentence N tokens: [...]"，那是最直接的取证手段。
- `speed` 与 `length_scale` 都在 `model_->Run(x, sid, speed)` 里生效。

## 7. 结论

- **不该自己拼 token**：应照抄 §2 流水线，尤其 **`add_blank`**。
- **不该调 `length_scale`**：默认 1.0；帧数不对是 token 数不对的症状。
- 目前缺口：加 blank 后 187 帧 vs sherpa 197 帧，**还差 10 帧**，
  需要把 sherpa 的真实 token 序列取出来（§5 的编码问题一解即可定案）。

## 8. 本轮追加实测（2026-11-01）

### 8.1 `add_blank` 是真的，但还不够
`AddBlank(ids, pad_id=1)` 把 n 个 token 变成 `2n+1`：

| 送进去 | token 数 | matcha 帧数 |
|---|---:|---:|
| 裸 10 音节 | 10 | 106 |
| 加 blank | 21 | 187 |
| 加 blank + pad | 22 | 193 |
| 加 blank + 句号 token | 22 | 202 |
| **sherpa 实际** | ? | **≈197–198** |

### 8.2 关键是"切句后分批跑"
debug 实测 sherpa 把 `"你好，我是你的语音助手。"` 切成 **2 句**：

| 方案 | mel 帧 | 音频 |
|---|---:|---:|
| 句1「你好」加 blank（5 token） | 66 | 0.755 s |
| 句2「我是你的语音助手」加 blank（17 token） | 160 | 1.846 s |
| 两句拼接 | **226** | 2.601 s |
| 两句不加 blank | ~113 | 1.312 s |
| **sherpa 实际** | **≈198** | **2.293 s** |

⇒ 加 blank 拼两句 = 226（太长），不加 blank = 113（太短），sherpa 的 198 在两者之间。
**说明切句/合并/blank 三者的组合还不是我这个版本的行为**（本地装的 sherpa 版本与
master 源码可能不同——`MergeShortSentencesByWords(min_words=5)` 在本地版显然没把
「你好」并进下一句，否则不会报 2 sentences）。

### 8.3 其他确认的默认值
- `OfflineTtsConfig.silence_scale = 0.2`（**默认就把静音压到 20%**）
- `OfflineTtsModelConfig.num_threads = 1`、`provider = 'cpu'`、`debug = False`
- `OfflineTtsMatchaModelConfig.noise_scale = 1.0`、`length_scale = 1.0`
- 用 `debug=True` 时**没有**打出 "Sentence N tokens: [...]" 那行（master 有、本地这版没有）
  ⇒ **本地版本比 master 旧**，不能完全按 master 源码推。

## 9. 结论（修正）

1. **不该自己拼 token、不该调 `length_scale`** —— 这两条用户指出的问题**成立**。
2. **应当用 sherpa 自己的实现**，但本地装的版本与 master 有差异，需要以
   **本地版本的实际行为**为准（`debug=True` 已能给出切句数）。
3. 目前仍未对齐的：切句后 2 句、blank、以及最终的 198 帧还不够自洽。
   **最省事的正解**：不要再复现，**直接把 sherpa 的 mel 取出来**——
   用 `debug=True` 找切句规律后，用与 sherpa **完全相同的分批方式**喂 matcha；
   或改用 sherpa 输出的音频做验收（NPU 只报性能）。
