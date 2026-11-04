# sherpa-mel —— 前端 / mel 的来源（**这目录解决的问题是"mel 从哪来"**）

> **在链路里的位置**：第 1 段「前端」。文本 → token（→ 交给 `../matcha/` 出 mel）。
>
> **这个目录的由来**：我一开始按 `lexicon.txt` 逐字查表自己拼 token，结果 mel 少一半、
> 时长对不上、音频全是坏的。**正确做法是不自己拼，让 sherpa 把它自己的 token 打印出来照抄。**

---

## 1. ★ 正解：打开 sherpa 的 model-level debug

**`OfflineTtsModelConfig` 有 `debug` 字段（默认 `False`）**。打开后 sherpa 会把分词结果
和**最终 token ID 序列**直接打到 stderr：

```python
mc = sherpa_onnx.OfflineTtsModelConfig(
        matcha=sherpa_onnx.OfflineTtsMatchaModelConfig(...),
        num_threads=1,
        debug=True)                      # ★ 这一行
tts = sherpa_onnx.OfflineTts(
        sherpa_onnx.OfflineTtsConfig(model=mc, max_num_sentences=1))
tts.generate(TEXT, sid=0, speed=1.0)
```

实测输出（`"你好，我是你的语音助手。"`）：

```text
Sentence 0 tokens: [ni2(1116), hao3(597), ,(2), wo3(1737), shi4(1518), ni3(1117),
                    de(314), yu3(1872), yin1(1845), zhu4(1993), shou3(1522), .(3)]
Sentence 0 final IDs (25): [1, 1116, 1, 597, 1, 2, 1, 1737, 1, 1518, 1, 1117, 1, 314,
                            1, 1872, 1, 1845, 1, 1993, 1, 1522, 1, 3, 1]
```

**照抄这串 ID 喂 matcha 即可。**

### ⚠️ 版本要求

| sherpa-onnx 版本 | `debug=True` 能打出什么 |
|---|---|
| **1.13.8**（校机原装） | 只有句子切分（`Number of sentences: N`） |
| **2.0.1**（要升到这个） | **完整 token 与 final IDs** |

装法（**不要动现役 ASR venv**）：
```bash
pip install --target D:\yzd\ttsbench\sh_new --upgrade sherpa-onnx
# 运行时：set PYTHONPATH=D:\yzd\ttsbench\sh_new
```

---

## 2. ★ 三处我搞错的地方（自查清单）

| # | 错在哪 | 正确做法 |
|---|---|---|
| 1 | 「你」按 lexicon 查是 `ni3`(1117) | **`ni2`(1116)** —— **变调**，逐字查表必错 |
| 2 | 标点被丢掉 | **`,`(2) 与 `.`(3) 本身是 token**，要送进去 |
| 3 | 直接送音节序列 | **每个 token 之间插 `pad=1`**（这就是 `add_blank`，n 个变 `2n+1`） |

**任何一条错，帧数就对不上，音频就废。**

---

## 3. 前端完整流水线（从 C++ 源码读出来的，不是猜的）

源码：`sherpa-onnx/csrc/offline-tts-matcha-impl.h`、`lexicon.cc`、`offline-tts-frontend.cc`。
完整记录见 **[`SHERPA_FRONTEND_SOURCE.md`](SHERPA_FRONTEND_SOURCE.md)**。

```text
text
 ├─ ReplacePunctuations()      // ，→,  。→.  、→,  ；→;  ：→,  …→...  ！→!  ？→?
 ├─ SplitByAllPunctuation()    // 在 , . ! ? ; : 处切句
 ├─ MergeShortSentencesByWords(min_words=5) / SplitLongSentenceByWords(max=20)
 ├─ TokenizeFromLexiconStr()   // lexicon 最长匹配；CJK 单字兜底；空格与标点查 token 表
 ├─ 句尾若是标点则补一个标点 token
 ├─ ApplyBosEos()              // use_eos_bos=0 ⇒ 直接平铺
 └─ if (add_blank) AddBlank(x, pad_id=1)    ★ 关键：n → 2n+1
```

### 相关默认值（实测）

```python
OfflineTtsMatchaModelConfig.noise_scale  = 1.0
OfflineTtsMatchaModelConfig.length_scale = 1.0     # 不用调
OfflineTtsConfig.silence_scale           = 0.2     # 默认就把静音压到 20%
OfflineTtsModelConfig.debug              = False
```

---

## 4. `tokens.txt` 的结构（搞懂它才知道为什么 add_blank）

`matcha-icefall-zh-baker/tokens.txt` 共 **2069 行**：

| 类别 | 数量 | 例 |
|---|---:|---|
| 带声调音节 | 1648 | `ni3`、`hao3` |
| 不带声调音节 | 412 | `ni`、`hao` |
| 单字母 | 5 | `a` `e` `m` `n` `o` |
| 符号 | 8 | `!` `"` `'` `,` `.` `:` `?` `_` |
| **空格 token** | 1 | **第 1 行是 `"  0"`——token 就是空格本身，id 0** |

**`sil` / `eos` / `#0` 不在表里**，所以 `Lexicon::ConvertTextToTokenIdsChinese` 里
那些 `if (token2id_.count("sil"))` 分支**全部不生效**。

> 第 1 行那行会让 **Python 版** `utils.text2token` 的 `assert len(toks) == 2` 崩
> （`line.strip().split()` 只剩 1 列）。C++ 侧没这问题。绕法见 `fix_tokens.py`。

---

## 5. 曾试过且失败的路线（不必再试）

| 路线 | 结果 |
|---|---|
| 扫 `length_scale` 19 档硬凑时长 | 最高相关 **0.036** |
| 从 sherpa 音频 STFT 反推 mel | **−0.008** |
| 假 matcha 回显 token（metadata 全对齐 `model_type=matcha-tts` 等 12 项） | sherpa 仍 `MemoryError: bad allocation`（其 C++ 按 mel shape 预分配音频缓冲） |
| 假 vocoder 透传 mel | 同类内存错误 |
| `sherpa-onnx-cli text2token` | 装上 `sentencepiece`/`pypinyin`/`jieba` 后仍失败：产物是**带声调符号的拼音** `nǐ`，而模型 `tokens.txt` 是**数字声调** `ni3` ⇒ **这个工具对 matcha 无意义** |

**结论：只有 §1 那条 debug 打印的路子是通的。**

---

## 6. 目录内文件

| 文件 | 作用 |
|---|---|
| **`SHERPA_FRONTEND_SOURCE.md`** | ★ 前端源码正本 + 全部调查记录（读 C++ 得来） |
| **`enable_debug.py`** | ★ 打开 model-level debug 取 token 的最小例子 |
| **`build_exact.py`** | ★ sherpa 取 ID → matcha 出 mel → 出 wav 的完整链 |
| `try_debug.py` | 找 debug 开关的探针（试 kwarg / GenerationConfig） |
| `fix_tokens.py` | 修 `tokens.txt` 第 1 行（空格 token），让 Python `text2token` 能跑 |
| `text2token_probe.py` | 调 sherpa 自己的 `text2token`（发现声调格式不兼容） |
| `two_sent.py` | 切句行为验证 |
| `frontend_reimpl_study.py` | **手抄前端流水线的研究件——已被放弃，留档说明为什么不该这么做** |

---

## 7. 坑

| # | 坑 | 处置 |
|---|---|---|
| 1 | 自己按 lexicon 拼 token | **别拼**，用 `debug=True` 打印 |
| 2 | 1.13.8 版打不出 token | 升到 **2.0.1**（装独立目录） |
| 3 | 忘了 `add_blank` | n → `2n+1`，漏了帧数差一半 |
| 4 | 忘了标点也是 token | `,`(2)、`.`(3) 要送 |
| 5 | 以为「你」是 `ni3` | 是 **`ni2`**（变调） |
| 6 | 用 `text2token` 工具 | 它产带声调拼音符号，与本模型 `tokens.txt` 不兼容 |
| 7 | 调 `length_scale` 掩盖 token 错 | 默认 1.0，不动 |

---

## 8. 相关

- 下游 → `../matcha/`（吃 token 出 mel）
- 决策 → `wiki/archive/开发管理/状态源与决策记录.md` 的 **`D-1793547016`**（推翻手工拼 token）
- 文档正文 → `wiki/PS/Backend/MatchaVocosNPU.md` §8.6 / §8.7