# matcha-icefall-zh-baker —— 声学模型，**留在 CPU**

> **在链路里的位置**：第 2 段。吃 `token`，吐 `mel[80,T]`。
> **当前不上下沉 NPU**（原因见 §2），但它是 mel 的唯一来源，必须懂它。

## 0. 基本信息

| | |
|---|---|
| 模型 | `model-steps-3.onnx`，75.6 MB，4802 节点 |
| 采样率 | 22050 Hz |
| 输出 | `mel [N, 80, L]` |
| 输入 | `x [N,L]` int64（token）、`x_length`、`noise_scale [1]`、`length_scale [1]` |
| metadata | `model_type=matcha-tts`、`jieba=1`、`pad_id=1`、`num_ode_steps=3`、`use_eos_bos=0`、`language=Chinese` |

**关键默认值**（实测读出来的，不是猜的）：

```python
noise_scale  = 1.0        # 官方默认
length_scale = 1.0        # ★ 不要动
```

---

## 1. ★ 三条必须记住的事实

### 1.1 `length_scale` 保持默认 1.0，**永远不要为凑时长去调**

曾经为了"让时长对上 sherpa"把它调到 1.86 / 2.16 / 2.3——**这是用错参数掩盖"token 数不对"的症状**。
调到 2.3 会把每个音节拉长 2.3 倍，听成叠字（"你你你好好好"）。

### 1.2 `noise_scale = 0` 才能做 A/B

`matcha` 是概率模型。实测：

| `noise_scale` | 两次跑是否逐字节相同 | 相关 |
|---|---:|---:|
| **0.0** | **是（完全相同）** | 1.0000 |
| 0.667 | 否 | 0.954 |
| 1.0 | 否 | 0.920 |

**做任何对照都必须 `noise_scale=0`。**

### 1.3 帧数对不上 = token 数不对，**不是模型问题**

| 送进去 | token 数 | 输出帧数 |
|---|---:|---:|
| 裸 10 个音节（按 lexicon 逐字查） | 10 | **106** |
| sherpa 真实序列 | **25** | **~197** |

差 2 倍但时长只差 1.9 倍 ⇒ 一度以为是 `length_scale`，其实是 **token 流少了一半**。
**正解见 `../sherpa-mel/`。**

---

## 2. 为什么上不了 NPU（`rc=71`）

| 项 | 读数 |
|---|---|
| parse | **`rc=71`**（预检报 2 条） |
| 缺陷算子 | `Range`、`RandomNormalLike` |
| 已产出的中间件 | `json 1.6 MB + raw 131 MB` |
| 能否 `optimize` | **不能**：`Unable to match pattern with Op, Op_id is 3, Op_type is icraft::xir::AddNode` |

**`rc=71` 连 optimize 都进不去**，所以：
- **不存在"CPU 补算"这条路线**——`Unsupport` 只是解析占位符，**没有前向实现**，
  不是"回落到 CPU"的机制。
- matcha 留在 CPU 是**结构性的**，不是暂时妥协。

> 同族另两个（`matcha-icefall-zh-en`）同样 `rc=19015`、同样 `Range`/`RandomNormalLike`。

---

## 3. 实测耗时（板上 4×A53，onnxruntime 2 线程）

| 场景 | matcha（CPU） |
|---|---:|
| 短句 20 音素 | **1730 ms** |
| 中句 60 音素 | **4504 ms** |
| 长句 150 音素 | **12211 ms** |

**它占两段总耗时的 64–67%**——这就是为什么"只把 vocos 搬走"就能把整链从
"慢于实时 11–17%"拉到"快于实时 29–40%"。

---

## 4. token 流调查（`token/`）

这一串脚本是"帧数为什么对不上"的完整调查，**结局是 token 数不对**：

| 脚本 | 查了什么 | 结论 |
|---|---|---|
| `tok_probe.py` | `tokens.txt` 里到底有哪些 token | 2069 行：1648 带声调音节 + 412 裸音节 + 5 单字母 + 8 符号；**第 1 行是空格 token(id 0)** |
| `punct_test.py` | 标点是否影响帧数 | **不影响**（`，`/`。` 不在 token 表且帧数不变） |
| `frame_gap.py` | 帧数差距全局排查 | 106 vs 197，`ls=1.86` 才能凑到 197 |
| `blank_shapes.py` | `add_blank` 各种形态 | `2n+1`（首尾各 pad）→ 187 帧；`+dot` → 202 帧 |
| `find_exact.py` | 暴力找能对上 197 帧的序列 | 都对不上，**说明 token 内容不同而不只是数量** |
| `exact_ids.py` | 用 sherpa 的精确 ID 复算 | 帧数 236（仍与 sherpa 不同 ⇒ 还有别的参数） |

**这些脚本的价值是"排除法留档"**：它们证明了问题不在标点、不在 blank、不在 `length_scale`，
**在 token 内容本身**——最终由 `../sherpa-mel/` 解决。

---

## 5. 坑

| # | 坑 | 处置 |
|---|---|---|
| 1 | 按 `lexicon.txt` 逐字查 token | **错**。见 `../sherpa-mel/`：变调、标点、`add_blank` 三者都会错 |
| 2 | 调 `length_scale` 凑时长 | 永远别调，默认 1.0 |
| 3 | `noise_scale>0` 做对照 | 每次结果不同，必须 0 |
| 4 | 拿 matcha 的 `rc=71` 当"能补算" | 没有 CPU 补算这回事 |
| 5 | `tokens.txt` 第 1 行是空格 token | 会让 Python 版 `text2token` 的 `assert len(toks)==2` 崩 |

---

## 6. 相关

- **mel 的正确来源** → `../sherpa-mel/`
- 声码器 → `../vocos/`、`../HiFi-GAN/`
- 决策 → `wiki/archive/开发管理/状态源与决策记录.md` 的 `D-1793547016`