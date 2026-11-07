# HiFi-GAN v2 npuserv 适配——状态与已知问题（2026-11-07）

## 已验证 ✅

- **协议链**：npuserv 注册 → `/load`（apply 0.4s）→ `/generate`（mel_b64 / frames →
  wave_b64 / to_file WAV）→ `/unload` 全通；单次端到端 0.2s。
- **接入方式**：差异化配置全在本目录 `board/config.json`（图路径/形状/采样率/守卫）；
  registry 条目只留策略默认；npuserv 本体零模型细节——"每模型自治"口径的实证。
- **确定性**：板上同输入连续 4 次 forward（两种轴序 × 各 2 次）输出**两两相关 1.0000**。

## 未解 ❌（数值验收卡在这）

输出 buffer 的**读回口径**不对：读回内容前段是单调小正值（像输入 mel 的列），与 CPU
波形相关仅 0.35~0.38，且不同调用路径下读回内容会变（0.0001 / 0.3539 / 一次 0.9994）。
已排除：轴序（两种都试，输出不变）、session 复用（每次确定性一致）、设备残留（重启后
同值）、TensorType 写法（网络声明 intype 与手写 Layout 同结果）。

**怀疑**：`Tensor.read(buf, offset, byte_size)` 在这颗 SFB 图的输出上的 offset 语义
与 Qwen3 驱动的 KV（'***C' 布局）不同——或 Output value 有 merged_distrs 需要按
dump 格式（--dump_format SFB/HQB）解释。

## 下一步（按序）

1. **CLI 口径**：`icraft-run --dump_output` 的 dump 文件是 README 时代验证过的取数
   方式；当前 CLI 以 `--backends "ZG330;Host"` 会 segfault，先解 CLI 参数
   （对照 vocos README §1 的命令原样重试，特别是 --dump_format）。
2. **read 的 offset 语义**：对照 Qwen3 驱动 `Tensor2Numpy`（它在同 binding 上工作）：
   它用 `item.chunk().byte_size` + `item.offset()`；本 adapter 相同……差异在 Output
   value 是否有 merged_distrs（Qwen3 的 logits 是 **C 布局无 merged**；HiFi-GAN 的
   Output 声明 `@merged_distr([0]^ZERO:{v:[0,153600)})` ——**整个输出被 merge 成一维**
   → 可能要用 merged 后的 shape/offset 读）。
3. 若 2 成立，修 read 后重跑 AB（脚本 `workenv/migrate-real-preview/hifigan_ab3.py`）。

## 佐证数据

- probe 脚本：`workenv/migrate-real-preview/hf_variants_probe.py`（4 次 forward 两两 1.0000）
- npu 输出：`hf_probe_T80x600_r0.npy`（153600，前段单调，std 0.7375）
- cpu 参考：`ab_cpu.npy` 是 30720 截断版；全长用 `hifigan_ab3.py` 现算（std 0.6055）
- 历史唯一正确读回：2026-11-07 15:2x，corr=0.9994（调用序列未复现，勿当口径）
