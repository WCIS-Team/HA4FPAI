# vocos-22khz-univ —— 声码器（频域三分量），NPU fp16 可用

> **在链路里的位置**：第 3 段「声码器」，与 `../HiFi-GAN/` **同一层、可互换**。
> 输入 `mel[1,80,T]` → 输出 `mag / cos / sin` 三个 `[513, T]`。
> **iSTFT 在模型图外，要自己做。**

## 0. 一句话定档

**fp16 可用**：硬件段 **19.29 ms**（CPU 是 2487 ms ⇒ **129×**），与同模型 CPU 波形相关
**0.969–0.978**，四句真实中文**试听确认可懂**。**int8 不可用**（相位全毁，见 `int8/`）。

---

## 1. 架构与算子面（272 节点，13.46 M 参数）

| 算子 | 个数 | 硬件耗时 | 说明 |
|---|---:|---:|---|
| **Matmul** | 17 | **11.14 ms（58.0%）** | 最大头，ConvNeXt 块的 MLP |
| Add | 25 | 2.86 ms（14.9%） | |
| Transpose | 43 | 1.73 ms（9.0%） | 个数多但便宜 |
| LayerNorm | 10 | 1.10 ms（5.7%） | |
| Conv2d | 9 | 0.88 ms（4.6%） | |
| Cast | 8 | 0.47 ms | |
| **Exp / Cos / Sin** | 1/1/1 | 0.14 / 0.11 / 0.10 ms | **int8 就坏在这三个上** |

**全部算子都在 icraft 的 114 个 spec 内，零 `Unsupport`**——这是它能一次编过五段的原因。
`Reshape` 51 个耗时 0 ms（纯数据搬运）。

---

## 2. 输入输出

| | 形状 | 说明 |
|---|---|---|
| ONNX 输入 | `mels [1, 80, T]` | mel，通道在前 |
| **icraft 输入** | **`[1, 1, 600, 80]`** | 声明 NHWC：把 `mel.T` 放进 `[600,80]` 零填充 |
| ONNX 输出 | `mag, x, y` 三个 `[1, 513, T]` | 输出顺序由 `get_outputs()` 明确是 `mag, cos, sin` |

**零填充规则**：`pad[:T, :] = mel.T[:T, :]`，其余补 0。600 是上限，**句长超过 600 帧会被截断**
（实测最长 409 帧，未触发）。

---

## 3. ★ 图外 iSTFT（必须自己做，参数不能猜）

从 ONNX metadata 读出、并逐项验证过：

| 参数 | 值 |
|---|---|
| `n_fft` | 1024 |
| `hop_length` | 256 |
| `win_length` | 1024 |
| `window` | **hann（周期型！）** |
| `center` | 1 |
| `pad_mode` | reflect |
| `normalized` | 0 |
| 采样率 | 22050 |

**窗必须是周期 Hann `0.5 − 0.5·cos(2πn/N)`**，不是对称 Hann。
用对了 STFT/ISTFT 往返误差 **3.357e-16**；用错了会得到"能出声但全错"的结果。

```python
nb = mag.shape[1]                          # 513
spec = np.zeros((mag.shape[0], 1024), complex)
spec[:, :nb] = mag * cos + 1j * (mag * sin)
spec[:, nb:] = np.conj(spec[:, 1:nb-1][:, ::-1])
fr = np.fft.irfft(spec, n=1024, axis=1)
# overlap-add + 除以窗平方和，最后裁掉 center 的 NFFT//2
```

脚本：`stft_verified.py`（验证件，往返 3.3e-16）。

---

## 4. int8 vs fp16 —— 为什么必须 fp16

见 `int8/README.md`（含完整根因链与三次失败尝试）。
一句话：**诸葛的 `CosNode`/`SinNode` 量化参数未实现（用默认），角度张量 scale 偏差 10.4%，
板上角度输出只剩 256 级 ⇒ 相位全毁。唯一解是整模型 fp16。**

| | int8 | **fp16** |
|---|---|---|
| 模型体积 | 18.7 MB | 31.3 MB |
| 硬件段 | **9.50 ms** | 19.29 ms（2.02×） |
| `mag` 相关 | — | **0.99868** |
| `cos` 相关 | — | **0.73168** |
| `sin` 相关 | — | **0.72839** |
| 波形相关 | 0.186 ❌ | **0.969–0.978** ✅ |
| 相位平均误差 | 81.1° | **22.5°**（78.1% 的点 <30°） |
| 需校准集 | 是 | **否** |

**即使 fp16 慢 2 倍，仍比 CPU 快 129×。**

---

## 5. 坑

| # | 坑 | 处置 |
|---|---|---|
| 1 | `int8` 相位坏 | 换 `--qdtype fp16`，别在 mix_precision 上耗 |
| 2 | `--dump_format` 单独用只出 scale 表 | **加 `--dump_output`** 才出真值 |
| 3 | Python `xrt.Session.forward` 连续调，第 2 句起全零 | 走 CLI，**一句一次进程** |
| 4 | 输入排布 | `reshape(80,T)` **不转置**；CPU 侧 corr 可达 1.000000 |
| 5 | 跨声码器比波形 | 别比，跟自己的 CPU 版比 |
| 6 | 句长 >600 帧 | 会截断，长文本要分段 |
| 7 | 窗函数用成对称 Hann | 必须**周期** Hann |

---

## 6. 编译（`build_container.sh`）

```bash
bash build_container.sh fp16     # 或 int8（int8 需要 qtset/ 校准集）
# 1-4 段在容器内；第 5 段 icraft-generate 必须本机 Windows CLI
```

**parse 的关键参数**（少一个就报 `Inputs params are required`）：

```text
--inputs "1,1,600,80" --inputs_layout NHWC --inputs_dtype FP32
--pre_method nop --pre_scale nop
--pre_mean "0 0 ... 0"(80 个)   --channel_swap "0 1 ... 79"
--target zhuge
```

> Jupyter/文档里曾写 `--pre_mean`/`--channel_swap` 用 `nop` 也能过（HiFi-GAN 就是这样），
> 但 vocos 这两项**必须显式给 80 个值**，否则会有静默的通道/均值偏差。

---

## 7. 目录内文件

| 文件 | 作用 |
|---|---|
| `build_container.sh` | ★ 五段之 1-4（parse→optimize→quantize→adapt） |
| `stft_verified.py` | ★ 周期 Hann STFT/iSTFT 对（往返 3.3e-16） |
| `cli_exact.sh` | ★ 逐句 CLI 上板（避免 Python 缓冲区复用） |
| `cli_samples.sh` | 样例批跑 |
| `read_vocos_npu.py` | 读 NPU 输出 |
| `fix_istft.py` | iSTFT 修正过程留档 |
| `mel_domain.py` | mel 域对比（隔离 iSTFT 变量） |
| `timenorm.py` | 时间归一化后比内容 |
| `int8/README.md` | ❌ int8 为什么坏（完整根因） |
| `fp16/README.md` | ✅ fp16 的做法与读数 |