# HA4FPAI — 板载语音控制栈（FMQL30TAI676H「悟净」）

在 FMQL30TAI676H「悟净」板（192.168.1.113，aarch64 / Ubuntu 20.04 / py3.8）上，
自给自足的语音控制栈：**不装 Home Assistant 本体**，用
[`klar-ha-nlu`](https://github.com/FABBricate-IT-Solutions/klar-ha-nlu)（MIT）
的 Rust NLU 引擎 + `sherpa-onnx` 语音链 + 自建执行器，
让**这块板自己既是控制器、也是被控设备**。

> **这不是 HA 的 fork。** 本仓库是我方自有代码；klar 以**官方发布二进制**引入，
> 前端源码单独取自上游并可本地重建（见下方「与上游的关系」）。

## 重构实施入口

[重构总清单](docs/REFACTOR-CHECKLIST.md)是本轮实施项的唯一勾选正本：包含26项已完成模块/调查与46项待办，每项列组件、实现方式、验收和证据边界。每做完一步更新对应复选框，不以测试数量当产品完成率。

[目标架构](docs/REFACTOR-ARCHITECTURE.md)定义控制单写者、视频owner/recorder所有权、JSON1迁移桥与配置applier；[起点与实施历史](docs/REFACTOR-HISTORY.md)记录c670285分叉、archive-2026-11-06封存及后续合流/实验。旧章节为既有系统说明，不代表新架构已生产切换。

CI、打包检查、性能前后对比及 SSH 实机检查见 [CI 使用与维护](docs/CI.md)。
测试覆盖见 [失效模式清单](docs/CI-FAILURE-MODES.md)，实跑结果和边界见 [CI 验证记录](docs/CI-VALIDATION.md)。

---

## 一、为什么板上不装 Home Assistant

实测证据（2026-10-30，板上直查）：

| 检查 | 结果 |
|---|---|
| `python3 -V` | **3.8.10**（focal 全源最高，无 deadsnakes，无其他解释器） |
| PyPI `homeassistant` `requires_python` | **>= 3.14.2** |
| `pip3 download homeassistant==2024.6.0` | `No matching distribution found` |
| `command -v docker podman` | 空 |
| `free -m` | total **993 MB** |
| `/dev/snd` | 不存在（只有 `timer`），`aplay -l` 无卡 |

最后一条可用的 HA 版本是 `2021.2.3`（27.2 MB / 973 集成），
但它**没有 `wyoming`、没有 `assist_satellite`**——语音助手整套都不存在。

⇒ HA 本体这条路在板上走不通，**改走「自己补齐 HA 负责的那几层」**。

---

## 二、当前架构（路线 2）

```
 mic 48kHz
   ↓ decimate3-mavg3（实测 −12.3 dB @20 kHz 抗混叠）
 16kHz
   ↓
  lang/server 的 wyoming_gateway.py  ← 单进程：一份 sherpa-onnx（共用 env，约 300 MB）
   ├ 10300 KWS「小克」   → 命中才往下走
   ├ VAD silero v5       → 判断说话起止
   └ 10301 ASR           → 中文文本，RTF 0.40（14M zipformer）
   ↓ "打开客厅的灯"
 klar_executor.py  ← 双通道
   ├ 通道 B 直连命令表（~0 ms，确定性）：看看是谁 / 开始录制 / 我回来了开门 / 现在什么状态
   └ 通道 A klar engine（~25 ms，有澄清多轮）：家居词 → {intent, slots}
   ↓ 归一成 (动作, slots)
 板子自己的 HTTP API  ← 这就是「被控设备」
   8091 NPU 人脸   8092 画面   8093 录制
   开门另过四道闸（door_unlock.py）：口令 → 人脸 → 防伪 PAD → 声纹
   ↓ 结果
 speak.py → 10302 TTS（matcha，RTF 1.16 热机；TTS 服务由 lang/server 起）
   ↓ 22050 → linear 升采样 → 48000
 落盘 /mnt/data/klar/speech/*.wav（板上无声卡，等喇叭方案）
```

### 各服务端口

| 端口 | 是什么 | systemd 单元 |
|---|---|---|
| 10300 | Wyoming 唤醒（KWS） | `lang-server-voice`（**在 lang/server**） |
| 10301 | Wyoming ASR | 同上（单进程） |
| 10302 | Wyoming TTS | 同上（单进程） |
| 10500 | klar Wyoming intent | `klar-nlu-engine` |
| 10520 | klar HTTP + UI | 同上 |
| 10900 | **统一控制台** | `klar-console` |

> 三个语音服务**合并成一个进程**（P0 优化）：合并前 383.1 MB → 合并后 318 MB，
> 省 65 MB。端口不变，对客户端透明。

---

## 三、实现了哪些 HA 的逻辑

HA 在这条链上负责 4 件事：

| # | HA 原职责 | 本仓库实现 | 状态 |
|---|---|---|---|
| 1 | 实体表（房间/设备命名真值） | `klar/make_registry.py` 生成 `core.entity_registry` + `core.area_registry` | ✅ 日志 `6 Entitäten, 3 Räume` |
| 2 | 意图执行器 | `klar/klar_executor.py` 双通道 | ✅ 8/8 实测 |
| 3 | 设备接入 | 不用 Zigbee 等——被控设备就是板子自己的 HTTP API | ✅ 全端点实测 |
| 4 | 状态查询 | `act_status()` 并发读三服务 | ✅ |

**HA 有、本仓库故意没做**（不影响「说一句就控制」主线，影响「7×24 自己跑」）：

| 能力 | 影响 |
|---|---|
| 多轮上下文 | executor 每次新建会话，没接 klar 的 `conversation_id` → 「再关掉」类指代失效 |
| 自动化/场景 | 无定时触发器 → 「每天 7 点叫我」做不到 |
| 定时器真执行 | klar 只输出 `HassStartTimer`，到期无人执行 |
| 实体状态历史 | 无数据库 → 「刚才谁来过」查不了 |
| 设备注册表 | 只填 entity+area，未填 device |
| 多用户鉴权 | 内网，只有 `confirm_risky_actions` |

**HA 没有、本仓库额外补的**：语音 I/O 层、重采样、多因子开门、声纹识别。

---

## 四、klar engine 的定位（避免误解）

它**只做确定性 NLU 解析**：

```
"打开客厅的灯" → {decision:"execute", intent:"HassTurnOn", entity_id:"light.keting"}
```

**它不执行、不认识语音、不合成语音、不存状态。**

- 只跑 klar = 能听懂，但什么都不发生
- 只跑 executor = 能开关，但听不懂人话
- **两者合起来，才等于 HA 的「对话代理 + 自动化」那一半**

---

## 五、目录

```
boards/                被控设备侧代码（Python 3.8 兼容）
  console.py           统一控制台（10900）
  door_unlock.py       多因子开门：口令+人脸+防伪+声纹
  voiceprint.py        声纹录入/校验（campplus-zh 192 维）
klar/                  klar 部分（NLU 集成 + 语言包 + 文档 + 部署件，详见 klar/README.md）
  klar_executor.py     双通道执行器（通道A klar 解析，通道B 直连命令）
  make_registry.py     生成 core.entity_registry/core.area_registry 给 klar
  langpacks/           zh-CN 话术包原机翻版 + 手工重译版 + en 参考包 + _verify.py
  docs/                KLAR-401-BYPASS.md、KLAR-CUSTOM-PHRASES.md
  deploy/              klar-nlu-engine.service、deploy-klar-ui.sh
deploy/                上层 systemd 单元（`klar-console` / `klar-executor`）；语音单元与 smoke 已移交 lang/server
docs/                  实测记录与决策
ha/                    HA 侧接入说明（占位，本板不装 HA）
tools/                 （空；UI 部署脚本已并入 klar/deploy/）
archiver/              HA4FPAI 语音链路退役件归档（从 lang/server/archive 迁入，2026-11-04；见 archiver/ha4fpai-voice/README.md）
```

> 语音链（`voice_*`／`klar_asr·tts·wake`／`speak`／`wyoming_probe`／`bench_resampler`／`klar-voice.json`）
> 已按决定迁到 `lang/server` 实现，旧实现存档在 `archiver/ha4fpai-voice/`（原在 lang/server/archive，2026-11-04 迁入本仓归档）。

---

## 六、模型与 RTF（config 级实测，非目录名推断）

> 语音模型与 RTF 读数现由 **`lang/server`** 的现役实现承接（本仓不再装载语音模型）；
> 下表保留作实测记录，配置正本在 `repos/HA4FPAI/lang/config/`。

| 组件 | 模型 | 大小 | RTF 实测 |
|---|---|---|---|
| VAD | silero v5 | 2.3 MB | — |
| ASR-A（默认） | `streaming-zipformer-small-bilingual-zh-en-2023-02-16` int8 | 14 M | **0.40** ✅实时 |
| ASR-B（可选档） | `icefall-asr-multi-zh-hans-zipformer-large` (2025-06-30) | 163 M | 0.96–1.19 ❌非实时 |
| KWS | `sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20` chunk-8 int8 | 4.6 MB | 0.499 |
| **TTS（现用）** | matcha-icefall-zh-baker + vocos-22khz-univ | 75.6 + 53.9 MB | **1.16–1.82** |
| TTS（兜底档） | piper `zh_CN-xiao_ya-medium` (vits) | 18.6 MB | 2.90–3.52 |
| 声纹 | campplus-zh-common | 28.3 MB | ≈0.24 |

> **关键取舍**：用户指定的 `sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30` 在 A55 上
> **RTF 0.96–1.19（跟不上实时）**，冷加载 46 s、峰值 RSS 308 MB。14M 小模型 RTF 0.40 满足实时，
> 故 A 为默认、B 保留可切（`asr.profile`），两个都在板上不删。
>
> **TTS 默认 matcha**（比 piper 快约 2 倍，多占 64 MB）。`tts_profiles` 保留 `piper-xiao-ya`
> 可随时切回。matcha 声学模型**不能**当 vocoder 用（会 segfault），必须外挂 vocos。

### 重采样选型（实测，非推断）

| 方向 | 算法 | 耗时 | 依据 |
|---|---|---|---|
| 升采样 22050→48000 | **linear（np.interp）** | 16.6 ms/2 s | 比 polyphase **快 107 倍**；真实 TTS 语音频谱相关 **0.9997** |
| 降采样 48000→16000 | **decimate3-mavg3** | 3.6–9.9 ms | 与正规 31 抽 sinc 低通**识别结果完全一致**，快 11–14 倍 |

升采样不产生混叠只需平滑；降采样必须抗混叠（3 抽平均实测把 20 kHz 混叠压到 −12.3 dB）。
重采样总开销相对 TTS 推理仅约 0.6%，继续优化无意义。

---

## 七、内存（实测）

| 服务 | RSS |
|---|---|
| lang-server-voice（三语音合一，原 klar-voice-gateway） | 318–328 MB |
| klar-nlu-engine | 5.4 MB |
| klar-console | <10 MB |
| WCIS 系（face/record/stream 等） | 98 MB |
| **全板合计** | **471.7 MB** |

`MemTotal` 993 MB，`MemAvailable` 409 MB（合并前只有 263 MB）。

---

## 八、当前真实状态（实测）

| 项 | 状态 |
|---|---|
| 语音（lang-server-voice）/ engine / console | ✅ active + enabled |
| VAD/ASR/KWS/TTS | ✅ 全在跑 |
| ASR | ✅ RTF 0.40 |
| TTS | ✅ 热机 RTF 1.16 |
| 声纹 | ✅ 可用（库中 1 人，**假数据**） |
| 执行器命令 | ✅ 8/8 |
| **NPU 人脸 / 画面** | ❌ 503（试声学模型期间 yolo 被下，摄像头线路黑） |
| **喇叭** | ❌ 板上无声卡 |
| **麦克风** | ❌ 板上没有 |
| PAD 防伪 | ⚠️ 链路已通，模型能出 `model_live`/`model_non_live`，**安全标定未做**，不能作开锁依据 |

---

## 九、与上游的关系

| 上游 | 关系 |
|---|---|
| `FABBricate-IT-Solutions/klar-ha-nlu`（MIT） | engine 二进制取自其 GitHub Release；**前端源码单独取自上游**，在校机可本地重建并替换板上 UI（字节级可复现已验证） |
| `mslycn/wyoming-sherpa-onnx` | 参考其 Wyoming 服务封装形态（`AsyncServer` + `Transcribe*`/`Synthesize*`） |
| sherpa-onnx 2.0.1 | 板上已装，`/usr/local/lib/python3.8/dist-packages`；onnxruntime 由其内部捆绑 |

> **「共享 env」的实现基础**：四个组件在同一进程内共享同一份 onnxruntime，
> 不再各起一套。

---

## 十、关键约束

- **禁止在 113 上编译**（993 MB 内存、无 Node）。前端构建走校机
  `ssh school-guest`（`D:\yzd\tools\node\`），产物经本机中转上板——
  校机在 `192.168.212.0/24`，板子在 `192.168.1.0/24`，**跨网段不通**。
- 板子**不能编译**（993 MB 内存、CMA 池 256 MB，编译会挤死现役链）。
- 临时产物与一次性脚本一律进 `workenv/`。

---

## 相关文档

| 文档 | 内容 |
|---|---|
| `docs/ARCHITECTURE-OVERVIEW.md` | 完整链路图 + HA 逻辑逐条对照 |
| `docs/MEMORY-BREAKDOWN.md` | 逐组件内存实测与优化方案 |
| `docs/RESAMPLER-CHOICE.md` | 重采样算法实测与选型 |
| `docs/PAD-SEMANTICS.md` | PAD 字段语义（源码级核对） |
| `klar/docs/KLAR-401-BYPASS.md` | klar 401 根因与本机 token 代理 |
| `docs/CONSOLE-AND-TOKEN.md` | 控制台与 `KLAR_UI_DIR` |
| `docs/ROUTE2-IMPLEMENTED.md` | 路线 2 实施记录 |
| `docs/NO-HA-CHECKLIST.md` | 不装 HA 的能力对照 |
| `docs/HA-PYTHON-DOWNGRADE.md` | HA 降级到 py3.8 的源码级可行性（结论：不可行） |
| `ha/HOME-ASSISTANT.md` | 若将来真在别的机器上装 HA 的接法 |
