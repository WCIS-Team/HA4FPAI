# hifigan-prelu —— LeakyRelu → PRelu（**当前方案，62.88 ms**）

> 相对 `../rewrite-5op/`：**算子 483 → 166、`hard` 182.38 → 62.88 ms（2.90×）、
> 模型 5.9 → 4.4 MB**，而数值**一位不差**。

## 1. 一句话

`LeakyRelu(x, α) ≡ PRelu(x, α)`。icraft 算子面**有 `Prelu`**（`iCraft算子支持矩阵.md` 标 `√`），
`slope` 传常数 `[1]` 张量广播即可。**69 个算子顶 345 个。**

> ⚠️ **ONNX 侧算子名是 `PRelu`，不是 `PReLU`。** 写错报
> `No Op registered for PReLU with domain_version of 11`。

## 2. 结果

| | rewrite-5op | **hifigan-prelu** | 变化 |
|---|---:|---:|---:|
| 图节点数 | 482 | **206** | −276 |
| 板上算子数 | 483 | **166** | **−65.6%** |
| **`hard` 时间** | 182.38 ms | **62.88 ms** | **2.90× 快** |
| `wall` 时间 | 221.33 ms | **75.01 ms** | 2.95× 快 |
| 模型体积 | 5.9 MB | **4.4 MB** | −25% |
| **vs 同模型 CPU** | 0.9991/0.9997/0.9984/0.9996 | **0.999116/0.999745/0.998430/0.999563** | **逐位相同** |

**四句复测 `hard`：62.86 / 62.84 / 62.86 / 62.84 ms**（极稳——模型按 600 帧定长跑，
耗时与实际句长无关）。

### 新版逐算子（166 ops）

| 算子 | 个数 | hard(ms) | 占比 |
|---|---:|---:|---:|
| Conv2d | 74 | 27.23 | 43.3% |
| Add | 44 | 23.29 | 37.0% |
| ReLU | 28 | 9.66 | 15.4% |
| Div | 4 | 1.38 | 2.2% |
| ConvTranspose2d | 4 | 1.23 | 1.9% |

**`Neg` 与 `Multiply` 完全消失，卷积重新成为最大头。**

## 3. ★ 必做的两件验证

### 3.1 改写等价（CPU，改写后 vs 原版）

```bash
python verify_equiv.py
# 期望：corr = 1.000000000    max|d| = 0.000e+00
```

用 `hifigan_v2_prelu.onnx`（**未做 c2d 的那份**）——c2d 版是 2D Conv，onnxruntime 跑不了。

### 3.2 上板保真（NPU vs **同模型** CPU）

```bash
python ../cpu_reference.py      # 出 CPU 参考（原版 onnx，输入 [1,80,T] 三维）
bash board_test.sh              # 板上跑
python extract_output.py        # 取 dump 转 wav
```

**必须同模型比**——拿 vocos 的 CPU 输出当参考会得到假"负相关"（见 `../README.md` §4）。

## 4. 五段编译

```bash
bash build_container.sh      # 1-4 段（容器）：parse → optimize → quantize(fp16) → adapt
cmd //c gen_local.bat        # 5 段（本机 Windows CLI，约 25.4 s）
```

产物：`hifigan_v2_prelu_ZG.json`（288 KB）+ `hifigan_v2_prelu_ZG.raw`（4.1 MB）。

## 5. 坑（本目录本轮踩的）

| # | 坑 | 处置 |
|---|---|---|
| 1 | `--target ZG` | 报 `Zg is not a valid target`，要写 **`zhuge`**（异常埋在进度条后面，容易漏看） |
| 2 | ONNX 写 `PReLU` | 必须 **`PRelu`** |
| 3 | 容器内路径被 Git Bash 改写 | 加 **`MSYS_NO_PATHCONV=1`** |
| 4 | 用 icraft-c2d 版跑 CPU 参考 | 不行，2D Conv 报 `Attribute dilations has incorrect size` |
| 5 | 拿 vocos 的 CPU 输出当 hifigan 参考 | 会得出假"负相关"（0.029/−0.141），实际是 0.999 |

## 6. 文件

| 文件 | 作用 |
|---|---|
| **`rewrite_prelu.py`** | ★ 改写（LeakyRelu→PRelu，+ Clip/Squeeze/opset11/IR8） |
| **`verify_equiv.py`** | ★ 改写等价性（corr=1.0, max\|d\|=0） |
| **`build_container.sh`** | ★ 第 1-4 段 |
| **`gen_local.bat`** | ★ 第 5 段（本机 Windows CLI） |
| **`board_test.sh`** | ★ 上板对比 old/new |
| **`agg_by_op.py`** | ★ 逐算子聚合（新旧对照） |
| `extract_output.py` | 取 NPU 输出（`v_id=620`，153600 样本）转 wav |
| `four_sent_timing.py` | 四句耗时 |

## 7. 复现顺序

```bash
# 0) 前置：把 hifigan_v2.onnx 与 conv1d_to_2d.py 放到本目录
python rewrite_prelu.py            # → hifigan_v2_prelu.onnx（206 节点）
python ../conv1d_to_2d.py hifigan_v2_prelu.onnx    # → *_c2d.onnx
python verify_equiv.py             # 必须 corr=1.000000
bash build_container.sh            # 容器 1-4 段
cmd //c gen_local.bat              # 本机 5 段
bash board_test.sh                 # 板上（记得先 scp 模型）
python extract_output.py           # 出 wav
python agg_by_op.py                # 逐算子对照
```