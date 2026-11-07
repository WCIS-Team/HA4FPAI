# 音频/LLM 与视觉链共存——估算方法与实测（2026-11-07，板 113）

> 用户问"vocos/HiFi-GAN/ASR 能不能和视频那边一起运行，怎么算"。本页是**正本**：
> 估算公式 + 实测读数 + 结论表。复现脚本在 `workenv/migrate-real-preview/`（未入库）。

## 一、怎么算（三条预算线）

```
① NPU 会话（决定"常驻"可能性）——**2026-11-08 结论修正**
   原判"session 设备级独占"是**误诊**：真规则 = **`openDevice()` 每进程只能调一次**。
   第二把 device 句柄（同进程再开、或另一进程各开各的）会打坏第一把的 DMA 通道——
   这就是当年多 worker 实验与 11-07 晚双 worker 实验里 "DMA Write failed" 的真因。
   **同一把 device 上 session 可以任意叠加、任意卸载重建**（实测：hf session 重建 3 轮
   逐位相同；qwen3-K128 + hifigan 同进程同 device 交替 forward/apply 全绿）。
   ⇒ **多模型常驻可行**：npuserv `--resident`（单 worker 单 device 多 session），
   qwen3+hifigan 并存交替 generate 实测通过（README "Resident mode" 节）。
   约束仍在：每个 adapter 必须接受 device 注入（不再自己 openDevice）——qwen3/hifigan
   已转，ERNIE 已转（e41_chatd.E41(device=)）；worker 崩 = 全部 resident 丢。
   切换代价：同 device 叠 session 时 **apply 就是切换代价**（HiFi-GAN 0.4s / ERNIE ~40s
   / Qwen3 ~170s），但 apply 之后各 session 并存，不再互踢。

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
| **Qwen3 + HiFi-GAN 同时常驻** | ✅ **2026-11-08 实测通过** | npuserv `--resident` 单 device 多 session：q3 2.06→2.54 tok/s、hf peak 1.0、交替全绿（原判 ❌ 系误诊，见 §一①） |
| **ERNIE(NPU) + 视频链** | ❌（同 Qwen3 机制） | LLM apply 均吃 CMA（未单独量化，机制同） |

**架构结论（2026-11-08 修订）**：模型间互斥**已被 resident 模式解除**（单 device 多
session 叠加）；**仍然互斥的是 LLM apply ↔ 相机**——那是 CMA 预算问题（apply 吃光
CmaFree），与会话数无关。即：多模型并存 ✅，多模型+相机（含 LLM apply 阶段）仍要
分时（apply 期间视觉让位；apply 完成后 LLM 推理不吃 CMA，推理阶段与相机并存待验
——推理本身若不碰 CMA 则窗口期后可共存，未单独实测）。

## 四、附带实测事实（读数存证）

- **swap 与 LLM 速度无关**：Qwen3 512 件有 swap 1.04 / 无 swap 1.08、0.90 tok/s——
  瓶颈是每步 NPU 计算 + KV DMA，不是换页。
- **K128 深度重编译后**：2.01–2.13 tok/s（KV DMA 112MB→28MB/步），语义 26/26 全等——
  详见 Qwen3 README/pipeline §9。
- Qwen3 apply 后 `CmaFree` 1.6MB 时 LLM 本体运行正常（推理不吃 CMA），死的只是相机。


## 五、第四条预算线：CPU 抢核（2026-11-07 实测，板 113）

硬件：**4× Cortex-A53**（0xd03×4，无大小核），Linux CFS 自动调度。

实测（视频三服务+相机全开 + ASR 30s 音频×4 pass 连续 CPU 密集推理 ~160s）：
- **每核 busy 仅 12–17%**（四核对视频全链的占用合计约 0.6 核当量）
- 相机帧率**满帧 30fps**（180 帧/6s）、age 0.35–0.8s——**无掉帧**
- ASR 实时率从独占 0.68× 变 **1.36×**（并发分摊核时间，慢约 2 倍但都活着）
- loadavg 0.27（4 核远未饱和）

**结论（两轮实验，第二轮推翻第一轮的乐观版）**：
1. **轻负载下核不是瓶颈**（第一轮：ASR 2 线程，每核 12-17%，相机满帧）——但**负载一重
   就抢**（第二轮：ASR 4 线程满负荷，rtf 2.18、相机 age 从 0.46 漂到 1.24s、帧率掉半）。
2. **分配核有效**：taskset 把 4 线程 ASR 钉 cpu2-3（2 线程）+ 视频进程钉 cpu0-1 后，
   ASR rtf 反而更好（1.89），相机 age 回 0.24-0.38 满帧。**核分配是真实可用的手段**。
3. **历史事故归因修正**：当年 VAD 4 线程自抢（决策记录：rtf 1.161→0.8025）+ 10-21
   多进程抢 /dev/video0/iCraft（PS 无响应→单 owner 架构）——前者是"同进程多线程
   自抢"（修法=减线程），后者是"设备独占"（修法=单 owner）。**跨进程 CPU 抢占**
   这条 2026-11-07 实测也存在（重负载下相机掉帧），修法=taskset 分核。
4. Vendoring/fork 解决不了抢核（代码归属 ≠ 运行时调度）。
5. 真正的独占资源依旧是：**NPU 会话（1 个）+ CMA 池（LLM apply 吃光）**——见 §一/§三。

**分核方案（实测有效，按需启用）**：
```
# 视频实时链钉 cpu0-1（boot 后或拉起服务时）：
taskset -pc 0-1 $(pgrep -f serve_photos | head -1)   # camera_owner 同 pid
# CPU 密集件钉剩余核（ASR 2 线程够用，4 线程自抢反而慢）：
taskset -c 2-3 python3 asr_worker.py ...
```
未满 3 个 CPU 密集件时不必启用；启用时机＝实时链出现 age 漂移/掉帧且确认是
CPU 推理挤压（NPU 侧互斥见 §一①，另案）。

复现：`workenv/migrate-real-preview/cpu_stress.sh`（ASR 后台循环 + 相机采样 +
每核快照）；per-core 读数用 `percore.py`（/proc/stat 差分）。


## 六、视频压核实验（2026-11-07 晚）：1 核跑满 30fps

三档实测（video/serve_photos+camera_owner taskset 绑定，连续 10s 帧计数）：

| 档位 | fps | age |
|---|---|---|
| 自由调度（4 核随便跑） | 29.5 | 0.23 |
| **钉 1 核（cpu0）** | **29.5** | 0.67 |
| **钉 1 核 + ASR 4 线程钉另 2 核** | **29.4** | **0.23** |

结论：
1. **视频链 1 核就满帧 30fps**（camera_owner 32% + serve_photos/record/vpu 的调度
   尾巴，合计 <1 核）。之前预留 2 核是过度的。
2. **重 ASR 并发下相机照样满帧**（age 0.23 与自由调度持平）——分核把"抢"变成了
   "各用各的"。ASR 4 线程被压到 rtf 4.45（抢不到核，这是隔离的代价），按需调
   线程数/核数。

**分核方案 v2（实测定案）**：
```
# 视频：钉 1 核（cpu0）——满帧
taskset -pc 0 $(pgrep -f serve_photos | head -1)
# CPU 密集件：钉 cpu1-3（3 核可用，线程数≤核数防自抢）
taskset -c 1-3 python3 asr_worker.py
```
可分配额度修正：**视频 1 核 + ASR/vocoder 等 CPU 件 3 核**（比之前"视频 2 核+ASR
1.5 核"多让出 1 核）。核不再是任何组合的瓶颈。
