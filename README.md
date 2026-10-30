# HA4FPAI — 板上语音/HA 变种栈

`klar-ha-nlu` 变种在 FMQL30TAI676H「悟净」板（192.168.1.113，aarch64 / Ubuntu 20.04 / py3.8）上的部署仓库。

## 结论先行（2026-10-30 实测）

| 项 | 实测值 | 判定 |
|---|---|---|
| 板 rootfs `/` | 26G，用 20G，**剩 4.3G（83%）** | 够装 voice 栈，**不够装 HA 本体** |
| `/mnt/data` (p3) | 30G，用 2.0G，**剩 26G** | 模型与运行数据放这里 |
| `/mnt/wcis-emmc` | 30G，用 838M，剩 28G | 备用 |
| 内存 | 993 MB total，可用 637 MB | 语音链（314MB 峰值）可跑，**HA 本体不可** |
| CPU | 4 核 aarch64 (A55)，`nproc=4` | — |
| 声卡 | **`/dev/snd` 不存在，`aplay -l` 无卡** | 板上无音频设备，走 HA 侧 mic / Wyoming over network |

## 装什么、不装什么

**已确认：这个板子装不下 Home Assistant 本体。**

- HA Core 最新版 `requires_python >= 3.14.2`（PyPI 实测），板上只有 Python 3.8.10（focal 全源最高 3.8，无 deadsnakes，无 docker/podman）。
- 即便退到历史版本，`homeassistant 2024.6.0` 在 arm64 + py3.8 上 `No matching distribution found`。
- 内存 993 MB 也低于 HA Core 实际需求。

**因此本仓库采用拆分部署**（与 `klar-ha-nlu` 官方两半结构一致）：

```
                         ┌─ 板 192.168.1.113 (aarch64) ─────────────────┐
  HA 侧 mic ──Wyoming──▶ │ ASR 10301   TTS 10302   KWS 10300            │
  HA ◀──Wyoming intent ─ │ klar engine 10500 / HTTP 10520               │
                         └──────────────────────────────────────────────┘
                                    ▲
  HA 本体 ──（HACS 集成，纯 Python，几百 KB）──▶ 对话代理 ◀── 设备执行
```

- **板上**：sherpa-onnx 语音链（VAD silero / ASR zipformer-zh / KWS / TTS piper-vits）＋ Wyoming 服务 ＋ klar Rust engine（官方 `klar-linux-aarch64.tar.gz` 4.5 MB，tag `2026.10.10`）。
- **HA 主机（不在本板）**：HA Core ＋ HACS 安装 klar 集成（conversation agent）。

## 模型与 RTF（config 级实测，非目录名推断）

| 组件 | 文件 | 来源（模型卡 README） | 大小 | RTF 实测 |
|---|---|---|---|---|
| VAD | `silero_vad_v5.onnx` | silero v5 | 2.3 MB | — |
| ASR-A | `encoder-epoch-99-avg-1.int8.onnx` + dec/join | `streaming-zipformer-small-bilingual-zh-en-2023-02-16` | 14M 主体 | **0.40** ✅实时 |
| ASR-B | `encoder.int8.onnx` (161 MB) | `icefall-asr-multi-zh-hans-zipformer-large`（2025-06-30 转换） | 163M 主体 | **0.96–1.19** ❌非实时 |
| KWS | `encoder-...-chunk-8-left-64.int8.onnx` | zipformer zh-en chunk-8 | 4.6 MB | — |
| **TTS（现用）** | `model-steps-3.onnx` + `vocos-22khz-univ.onnx` | matcha-icefall-zh-baker（data-baker 数据集） | 75.6 MB + 53.9 MB | **1.38–1.82**（3 线程）✅ |
| TTS（兜底档） | `zh_CN-xiao_ya-medium.onnx` | piper 中文 vits | 18.6 MB | 2.94–3.05（3 线程） |

> **关键取舍**：用户指定的 `sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30` 在 A55 上 **RTF 0.96–1.19（>1，跟不上实时）**，冷加载 46 s、峰值 RSS 308 MB。14M 小模型 RTF 0.40 满足实时。故配置里 **A 为默认、B 保留为可选档**（`asr.profile` 切换），两个都在板上，不删。
>
> TTS 默认走 **matcha-icefall-zh-baker + vocos vocoder**（RTF 1.38–1.82，比 piper 快约 2 倍，多占 64 MB）。`tts_profiles` 里保留 `piper-xiao-ya` 可随时切回。matcha 的声学模型**不能**当 vocoder 用（会 segfault），必须外挂 vocos。

## 目录

```
boards/
  klar-voice.json          # 统一 option JSON，装到板上 ~/klar-voice.json
  voice_kit.py             # 单进程共享 onnxrt，四组件独立 session
  klar_asr.py              # Wyoming ASR 服务 (10301)
  klar_tts.py              # Wyoming TTS 服务 (10302)
  klar_wake.py             # Wyoming wake/KWS 服务 (10300)
  klar_llm.py              # 预留 LLM 位（只聊天/改写，不碰设备）
ha/
  custom_components/klar_nlu/   # 占位：HACS 集成说明，非本板安装
deploy/
  install-voice.sh         # 校机交叉编译/本机构建，推板
  smoke.sh                 # 全链冒烟
docs/
  SOP.md                   # 可复现规程
```

## 关键约束（编译纪律）

板子 993 MB 内存，**禁止在 113 上编译**。任何编译走校机 `ssh school-guest`（EPYC 9654 / 125 GB / Debian 13），产物 scp 上板。

## 与上游的关系

- `mslycn/wyoming-sherpa-onnx`：参考其 Wyoming 服务封装形态（ASR/TTS 各起一个 `AsyncServer`，事件走 `Transcribe*`/`Synthesize*`）。
- `FABBricate-IT-Solutions/klar-ha-nlu`：engine 二进制取自其 GitHub Release，集成走 HACS。
- sherpa-onnx 2.0.1（板上已装，`/usr/local/lib/python3.8/dist-packages`），onnxruntime 由其内部捆绑——**这就是「共享 env」的实现基础：四个组件都在同一进程内共享同一份 onnxruntime，不再各起一套。**
