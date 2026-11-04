# rewrite-5op —— LeakyRelu → 5 算子（旧方案，182.38 ms）

> **已不推荐。** 保留它是因为：① 它是"能编过"的第一个版本，留档证明路线可行；
> ② 它的逐算子读数（`agg_by_op.py`）是 `hifigan-prelu/` 提速 2.90× 的**对照基线**。

## 改动

```text
LeakyRelu(x, α)  →  Neg → Relu(x)=rp
                    └→ Relu(neg)=rn  → Mul(α, rn) → Sub(rp, mu)
```

69 个 `LeakyRelu` 变成 **345 个算子**，全图 **205 → 482 节点**。

## 读数

| | |
|---|---:|
| 节点数 | 482 |
| 算子数（板上） | 483 |
| **`hard` 时间** | **182.38 ms** |
| `wall` 时间 | 221.33 ms |
| 模型体积 | 5.9 MB |
| int8 硬件段 | 74.84 ms（相关 0.57 ❌） |
| fp16 硬件段 | 182.51 ms（相关 0.998 ✅） |

**为什么慢**：`Add` 113 + `ReLU` 138 + `Neg` 69 + `Multiply` 69 = **83.7% 的时间**，
真正的 `Conv2d` 只占 14.8%。详见 `../README.md` §4。

## 为什么当初这么做

因为 `LeakyRelu` 不在 icraft 的 114 个算子 spec 里，**当时以为只能拆**。
后来发现算子面里有 **`Prelu`**，而 `LeakyRelu(x,α) ≡ PRelu(x,α)` ⇒ 1 个算子顶 5 个。

## 文件

| 文件 | 作用 |
|---|---|
| `rewrite_hifigan2.py` | ★ 主改写脚本（LeakyRelu→5 算子 + Clip + Squeeze + opset11/IR8） |
| `rewrite_hifigan.py` | 更早的尝试（保留作演进入口） |
| `parse.sh` | parse 步骤 |
| `quantize_int8.sh` | int8 量化（需校准集） |
| `run_board.sh` | 板上跑 |
| `check_equiv.py` | 改写等价性检查 |
| `agg_by_op.py` | ★ 逐算子耗时聚合（本目录的核心证据） |

## 复跑

```bash
python rewrite_hifigan2.py                 # 原版 → hifigan_v2_icraft.onnx
python ../conv1d_to_2d.py <上一步产物>      # → *_c2d.onnx
bash parse.sh && bash quantize_int8.sh     # 容器内
# 第 5 段 generate 必须本机 Windows CLI
```