# 音频/LLM 与视觉链共存——估算方法与实测（2026-11-07，板 113）

> 用户问"vocos/HiFi-GAN/ASR 能不能和视频那边一起运行，怎么算"。本页是**正本**：
> 估算公式 + 实测读数 + 结论表。复现脚本在 `workenv/migrate-real-preview/`（未入库）。

## 一、怎么算（三条预算线）

```
① NPU 会话（决定"常驻"可能性）
   ZG330 session 是设备级独占：第二会话 apply 成功也会破坏第一会话的 DMA 通道
   （实验：Qwen3 常驻后 HiFi-GAN 第二 worker apply→Qwen3 forward "DMA Write failed"；
    同进程双 session 同样崩）。⇒ NPU 上**任意时刻只有一个会话**，多模型只能"时间分片"，
    切换代价 = 杀 worker + 重新 apply（件大小决定：HiFi-GAN 0.4s，ERNIE ~40s，Qwen3 ~170s）。

② 主存（决定"同时活着"的进程集合）
   Σ RSS ≤ 993MB − 系统~220MB − 安全余量
   逐进程实测（视频全开）：serve_photos 77 + camera_owner 43 + recording 20 + vpu_live 17
   = 157MB；control 20MB；npuserv server 20MB；ASR zipformer int8（CPU）增量极小（见下）；
   LLM worker：Qwen3 ~450MB / ERNIE ~300MB。

③ CMA（真正的共存开关，最容易被忽略）
   256MB 池：udmabuf0 开机占 128MB + 驱动自留 ≈ CmaFree 130MB（相机 DMA 用）。
   **LLM apply 会把 CmaFree 吃到 1.6MB**（Qwen3 实测两次）⇒ 相机 DMA 失败 →
   owner quarantine（hardware-review-required）→ 视觉链断。
   vocoder/ASR 不吃 CMA（vocoder apply 前后 CmaFree 不变；ASR 是 CPU）。
```

## 二、实测读数（全部板上实测，2026-11-07）

| 件 | 运行时 | 加载代价 | 推理 | 内存足迹 | CMA |
|---|---|---|---|---|---|
| **ASR** zipformer zh int8 | CPU（sherpa-onnx 2.0.1，onnxruntime） | 秒级 | 2s 音频 **2.9s**（实时率 0.68×，2 线程） | **used 仅 +1MB**（mmap） | **零变化**（130.5MB 恒定） |
| **HiFi-GAN v2 PRelu fp16** | NPU（imodel 4.4MB） | **apply 0.4s**（热切换可行） | 600 帧 **0.2s/句**（数值验收见 STATUS.md 卡点） | worker 轻 | 不变 |
| **vocos fp16** | NPU（31.3MB） | 同上量级 | 19.29ms 硬件段 | 同上 | 不变 |
| **Qwen3-0.6B**（K128 深度） | NPU | **apply 163-172s** | 2.13 tok/s | worker ~450MB | **CmaFree 130MB→1.6MB** |
| **ERNIE-4.5-0.3B** | NPU | apply ~40s | 连贯输出 | worker ~300MB | 同样吃 CMA（未单独量化） |
| 视频链（参照） | VPU+NPU 混合 | — | 30fps H.264 | 157MB | 相机 DMA 用 |

**共存期间相机健康度**（ASR 推理循环中采样）：`ready=True` 全程、frame 3991→6811 推进、
`frame_age_s` 0.45–1.21s——**CPU 侧推理对相机零影响**。

## 三、结论表

| 组合 | 判定 | 依据 |
|---|---|---|
| **ASR(CPU) + 视频链** | ✅ **实测通过** | CPU 推理零 NPU 冲突、内存增量 1MB、相机零影响（本页 §二） |
| **HiFi-GAN(NPU) + 视频链** | ✅ 时间分片可行 | apply 0.4s 热切换；face 逐请求推理与 vocoder 按句交替；CMA 可恢复 |
| **Qwen3(NPU) + 视频链** | ❌ **不共存**（实测两次） | LLM apply 吃光 CMA → 相机 DMA 失败 → 门禁 quarantine。H07 长稳期间"视频开着 Qwen3 跑"成立是因为当时 Qwen3 未 load；load 之后相机必死 |
| **Qwen3 + HiFi-GAN 同时常驻** | ❌ | NPU session 设备独占（§一①），实验实证 |
| **ERNIE(NPU) + 视频链** | ❌（同 Qwen3 机制） | LLM apply 均吃 CMA（未单独量化，机制同） |

**架构结论**：语音/LLM 线与视觉线在**NPU 时间轴上互斥**，共享靠"分时+小件热切换"
（vocoder apply 0.4s 可随句切换；LLM apply 170s 只能整段让出视觉）。若日后要真并存，
解法在**驱动/图层面**（NPU 多会话或 CMA 预算重划），不是 npuserv 能解决的。

## 四、附带实测事实（读数存证）

- **swap 与 LLM 速度无关**：Qwen3 512 件有 swap 1.04 / 无 swap 1.08、0.90 tok/s——
  瓶颈是每步 NPU 计算 + KV DMA，不是换页。
- **K128 深度重编译后**：2.01–2.13 tok/s（KV DMA 112MB→28MB/步），语义 26/26 全等——
  详见 Qwen3 README/pipeline §9。
- Qwen3 apply 后 `CmaFree` 1.6MB 时 LLM 本体运行正常（推理不吃 CMA），死的只是相机。
