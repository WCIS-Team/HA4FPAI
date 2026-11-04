# HiFi-GAN v2 —— 声码器（时域直出），NPU fp16 可用

> **在链路里的位置**：第 3 段「声码器」，与 `../vocos/` **同一层、可互换**。
> 输入 `mel[1,80,T]` → 输出时域波形 `[1, T×256]`。**没有 cos/sin，没有图外 iSTFT。**

## 0. 为什么选它（与 vocos 的差别）

| | vocos | **HiFi-GAN v2** |
|---|---|---|
| 输出形式 | 频域三分量 `mag/cos/sin` | **时域波形** |
| 图外步骤 | **要** 自己做 iSTFT | **不要** |
| 算子风险点 | `Cos`/`Sin` → int8 必坏 | 无三角函数，int8 相对安全 |
| 模型体积 | 31.3 MB | **4.4 MB**（PRelu 版） |
| 硬件段 | **19.29 ms** | 62.88 ms（PRelu 版） |
| 与同模型 CPU 相关 | 0.969–0.978 | **0.9984–0.9997** |
| 中文自然度 | 已验证（试听通过） | **已验证（2026-11-02 试听通过）** |

**vocos 略低（0.97）不是它差**，而是它多一步图外 iSTFT，那 0.03 的差来自「我的 iSTFT 与 vocos 内部 iSTFT」。

## 1. 三个官方变体（实测大小）

| 模型 | ONNX 大小 | 说明 |
|---|---:|---|
| `hifigan_v1.onnx` | 55.75 MB | 最大 |
| **`hifigan_v2.onnx`** | **3.75 MB** | **本目录用的就是它** |
| `hifigan_v3.onnx` | 5.86 MB | — |

sherpa-onnx 文档写明 `--matcha-vocoder` **可以直接换成这三个中的任一个**。

---

## 2. ★ 算子改动（这是本目录的核心内容）

原始 `hifigan_v2.onnx`（205 节点、opset 14、IR 14）**直接喂 icraft 会连续报四个错**，
必须做四处改写。**改写配方如下（每一处都对应一个真实报错）**：

### 2.1 `LeakyRelu` × 69 —— 两种改法，速度差 2.90×

`LeakyRelu` **不在 icraft 的 114 个算子 spec 里**（算子面里只有 `Relu`、`Prelu`）。

| 改法 | 节点数 | `hard` 时间 | 说明 |
|---|---:|---:|---|
| **A. 拆 5 算子**（`Neg/Relu/Relu/Mul/Sub`） | 483 | **182.38 ms** | 能跑，但慢 |
| **B. 换 `PRelu`** | **166** | **62.88 ms** | ★ **推荐** |

数学上：`LeakyRelu(x, α) ≡ PRelu(x, α)`。`slope` 传一个常数 `[1]` 张量广播即可。

**为什么差这么多**——见 §4「逐算子真相」。

> ⚠️ **ONNX 侧算子名是 `PRelu`，不是 `PReLU`。** 写错会报
> `No Op registered for PReLU with domain_version of 11`。
> icraft 侧的操作类型名是 `Prelu`。

具体脚本：
- A 版 → `rewrite-5op/rewrite_hifigan2.py`
- B 版 → `hifigan-prelu/rewrite_prelu.py`

### 2.2 `Clip` × 1 → `Max` + `Min`

icraft 只有 `ClampScalar` 形式，`Clip` 的 min/max 在 `Constant` 节点里，要取出来换成
`Max(x, min)` 再接 `Min(·, max)`。

### 2.3 `Conv1d` × 78 → `Conv2d`

报错：`Op (Conv) [ShapeInferenceError] Attribute dilations has incorrect size`
——icraft 的 `Conv` 按 **2D** 校验 `dilations`，1D 卷积只有 1 个元素所以被拒。

修法：给 kernel 补一个 H 轴（长度 1，数学上是 no-op），pad H 填 0，输入保留 H=1 轴。
**脚本 `conv1d_to_2d.py`，两版共用。**

### 2.4 `Squeeze` 的 axes 从输入挪进属性

opset 11 的 `Squeeze` 只接受 1 个输入：
`Node(/Squeeze) with schema(::Squeeze:11) has input size 2 not in range [min=1, max=1]`。

### 2.5 其它

- opset 14 → **11**（icraft 只支持 11/17）
- IR → **8**（报 `Unsupported model IR version: 14, max supported IR version: 8`）

---

## 3. 改写后必须做的两件验证（不做就等于瞎猜）

### 3.1 数值等价（CPU，改写后 vs 原版）

```python
# 两版都喂同一份 mel，输出必须逐位相同
corr = 1.000000000
max|d| = 0.000e+00
```

脚本：`hifigan-prelu/verify_equiv.py`。
**这一步不能用 icraft 改写版验**——改写版是 2D Conv，onnxruntime 直接报
`Attribute dilations has incorrect size`。要用 `hifigan_v2_prelu.onnx`（未做 c2d 的那份）。

### 3.2 上板保真（NPU vs **同模型** CPU）

| 句 | NPU vs CPU |
|---|---:|
| 01_问候 | 0.999116 |
| 02_天气 | 0.999745 |
| 03_数字 | 0.998430 |
| 04_长句 | 0.999563 |

产出 CPU 参考用 `cpu_reference.py`（**原版 onnx，输入 3 维 `[1,80,T]`**，
不是 icraft 的 4 维 `[1,1,600,80]`）：

```python
o = sess.run(None, {"mel": mel[None, :80, :T].astype(np.float32)})[0]
w = np.asarray(o).ravel()[: T * 256]
```

---

## 4. ★ 逐算子真相：这颗 NPU 上「Conv 和 ReLU 一样贵」

用 `icraft-run --log_time` 取逐算子 `hard` 时间（同一句、同一份 mel）：

### 旧版（LeakyRelu → 5 算子），483 ops / 182.38 ms

| 算子 | 个数 | hard(ms) | 占比 | 单算子 |
|---|---:|---:|---:|---:|
| Add | 113 | 59.48 | 32.6% | 0.526 |
| **ReLU** | **138** | 47.09 | 25.8% | **0.341** |
| Conv2d | 74 | 27.02 | **14.8%** | **0.365** |
| Neg | 69 | 23.34 | 12.8% | 0.338 |
| Multiply | 69 | 22.79 | 12.5% | 0.330 |
| Div | 4 | 1.33 | 0.7% | 0.332 |
| ConvTranspose2d | 4 | 1.23 | 0.7% | 0.307 |

**结论：真正的卷积只占 14.8%，83.7% 的时间花在逐元素算子上。**

### 新版（PRelu），166 ops / 62.88 ms

| 算子 | 个数 | hard(ms) | 占比 |
|---|---:|---:|---:|
| Conv2d | 74 | 27.23 | **43.3%** |
| Add | 44 | 23.29 | 37.0% |
| ReLU | 28 | 9.66 | 15.4% |
| Div | 4 | 1.38 | 2.2% |
| ConvTranspose2d | 4 | 1.23 | 1.9% |

**`Neg` 和 `Multiply` 完全消失，卷积重新成为最大头 —— 骨架合理了。**

### 由此得到的三条通用判据

1. **`Conv2d` 单算子 0.365 ms ≈ `ReLU` 单算子 0.341 ms** ⇒ 开销在「每个算子启动一次 ＋ 搬一遍张量」，**不在 FLOPs**。
2. **算子个数是一等公民**：483→166 就换来 2.90×。
3. **改写前先问"目标算子面里有几个对应物"**，比"能不能编过"值钱：
   一个 `LeakyRelu` 有 `PRelu` 这个 1:1 对应物时，绝不要拆成 5 个。

**顺带解释"为什么 7.2 MB 的模型比 31.3 MB 的慢"**：
参数是**内存**，速度看**算子数 × 每算子张量大小**。HiFi-GAN 参数只有 vocos 的 6.9%，
但它在 **22.05 kHz 样本率**上做卷积（60416 个位置），vocos 在 **mel 帧率**上（236 个位置）。

---

## 5. 五段编译（`hifigan-prelu/` 里可直接复跑）

```bash
# 1-4 段：容器内
bash build_container.sh
#     parse  --target zhuge          （写 ZG 报 "Zg is not a valid target"）
#     optimize
#     quantize --qdtype fp16          （不用校准集）
#     adapt
# 5 段：本机 Windows CLI（容器里报 libicraft_zhuge_codegen.so does not exist）
cmd //c gen_local.bat                 # 约 25.4 s
```

产物：`hifigan_v2_prelu_ZG.json`（288 KB）+ `.raw`（4.1 MB）= **4.4 MB**（旧版 5.9 MB）。

---

## 6. 坑清单

| # | 坑 | 处置 |
|---|---|---|
| 1 | `LeakyRelu` 不在算子面 | 换 `PRelu`（**不要**拆 5 算子） |
| 2 | ONNX 里写 `PReLU` | 必须写 **`PRelu`** |
| 3 | `Clip` 不支持 | `Max` + `Min`，min/max 从 `Constant` 取 |
| 4 | `dilations has incorrect size` | `conv1d_to_2d.py` |
| 5 | `Squeeze` input size 2 | opset 11，axes 挪进属性 |
| 6 | `IR version 14` | 降到 8，opset 降到 11 |
| 7 | 拿 icraft 改写版跑 CPU 参考 | 不行，2D Conv 会报错；用原版 onnx |
| 8 | 拿 vocos 的 CPU 输出当 hifigan 参考 | **错**。跨声码器比波形必然≈0，会比出假"负相关" |
| 9 | 输入维度搞混 | 原版要 `[1,80,T]`（3 维）；icraft 要 `[1,1,600,80]`（4 维） |

---

## 7. 目录内文件

| 文件 | 作用 |
|---|---|
| `cpu_reference.py` | ★ 出 hifigan 的 CPU 参考（原版 onnx，3 维输入） |
| `conv1d_to_2d.py` | ★ Conv1d→Conv2d，两版共用 |
| `rewrite-5op/rewrite_hifigan2.py` | 旧改写：LeakyRelu→5 算子 |
| `rewrite-5op/parse.sh`／`quantize_int8.sh`／`run_board.sh` | 旧版编译与上板 |
| `rewrite-5op/agg_by_op.py` | 逐算子耗时聚合（旧版） |
| `hifigan-prelu/rewrite_prelu.py` | ★ 新改写：LeakyRelu→PRelu |
| `hifigan-prelu/verify_equiv.py` | ★ 改写等价性验证 |
| `hifigan-prelu/build_container.sh` | ★ 第 1-4 段 |
| `hifigan-prelu/gen_local.bat` | ★ 第 5 段（本机 CLI） |
| `hifigan-prelu/board_test.sh` | ★ 上板对不 old/new |
| `hifigan-prelu/agg_by_op.py` | ★ 逐算子聚合（新旧对照） |
| `hifigan-prelu/extract_output.py` | 取 NPU 输出转 wav |
| `hifigan-prelu/four_sent_timing.py` | 四句耗时 |