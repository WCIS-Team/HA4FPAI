# HiFi-GAN v2 npuserv 适配——状态（2026-11-08 验收通过）

## ✅ 数值验收通过：CORR = 0.9996

| 项 | 值 |
|---|---|
| NPU 输出 | std 0.8122, peak 1.0000 |
| CPU 参考（onnxruntime, hifigan_v2.onnx, 同 mel600） | std 0.8099, peak 1.0000 |
| **corr(NPU, CPU)** | **0.9996**（6 段分段相关全 1.000） |
| meanabs | 0.0134（相对 1.75%，fp16 量化预期） |
| 判据 | ≥0.95 → **PASS** |

复现：`workenv/migrate-real-preview/ab_npu_side.py`（板端，正确排布直跑）+
`ab_cpu_side.py`（PC，取回两个 npy 后 onnxruntime 比对）。

## 根因：mel 输入排布（不是"读回口径"）

图输入声明 `@layout(NHWC) shape=[1,1,600,80]`——**600 时间 × 80 频带，C 序连续内存
就是 mel600 本身**。正确喂法：

```python
xrt.Tensor(np.ascontiguousarray(mel600).reshape(1, 1, 600, 80), intype)   # ✅
```

此前 adapter 喂的是 `np.ascontiguousarray(mel600.T)[None,None]`（[80,600]，频带主序）
——**字节流是转置的**，NPU 算出来是错排布产物（std 0.7756）。这一条推翻 11-07
STATUS 里"输出读回口径（merged_distrs 嫌疑）"的怀疑：**读回机制本来就是对的**，
错的是输入。

另一条独立教训仍然有效：**同一 session 连续 forward 有缓存/不重传**（喂不同数据
输出不变，corr=1.0000）——所以 adapter 保持 **fresh apply 每句**（0.4s），
与 vocos README "一句一进程"同源。

## 已验证 ✅

- **协议链**：npuserv `/load`（apply 0.4s）→ `/generate`（mel_b64/frames →
  wave_b64/to_file WAV）→ `/unload`；单次端到端 0.2s。
- **resident 并存**：qwen3-K128 同 device 下 hifigan 加载后再加载/生成全绿
  （见 npuserv README "Resident mode"）。
- **差异化配置全在本目录** `board/config.json`（图路径/形状/采样率/守卫）；
  registry 只留策略默认；npuserv 本体零模型细节。

## 使用约定

- 输入 mel：`[T, 80]`（时间×频带），float32；不足 600 帧按行 tile 补齐。
- 输出：`[153600]` = 600 帧 × 256 samples @ 22050Hz；按 `T*256` 截断使用。
- 每次 generate 重建 session（fresh apply 0.4s）——别改成复用，复用会读到缓存输出。
