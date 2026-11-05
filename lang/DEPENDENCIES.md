# DEPENDENCIES

本页集中记录 `lang/` 语音链与配套 HMI 的依赖和模型前提。运行入口与通信见 [README](README.md)，链路图见 [chain.txt](chain.txt)。权重不入库。

## 1. 语音服务：先探测，再按档位补齐

安装前先确认目标机已有的软件包、同类替代品、Python/系统/架构与实际启用档位；只有缺失且确实会用到的依赖才安装。安装完成还要验证导入、解码或模型装载，不能把“包已装”当作“链路可用”。一键入口及默认在线策略见 [DEPLOYMENT](DEPLOYMENT.md)。

Ubuntu 20.04 arm64 的 [Linux 安装包](packaging/linux/README.md) 将 Python3.8/venv、ALSA、mpg123 等声明为 apt 依赖；edge-tts、Wyoming、sherpa-onnx、NumPy、Hugging Face 官方客户端及其传递依赖随包提供固定版本 wheel，装入私有虚拟环境，不依赖系统 pip。模型后台下载，服务默认全在线，不构造或执行本地模型。

### 1.1 按运行方式选择依赖

| 运行方式 | 所需依赖 | 说明 |
|---|---|---|
| 本机自检（ASR/LLM 为 echo，TTS 为 sine；VAD 为 none/rms，KWS 为 passthrough） | 基础 HTTP 编排可用 Python 标准库 | 不需要 miniaudio、numpy、PyYAML 或模型运行时；应明确选自检档并用 `--no-warm` 跳过 sherpa 预热 |
| 板端在线链（ASR remote-api、LLM openai、TTS http） | HTTP 客户端为标准库 | 需要可达的远端服务及其认证配置；本地 VAD/KWS 若启用模型仍需相应依赖 |
| 板端在线链使用 Edge TTS | `edge-tts` 及一个可用的 MP3 解码后端 | miniaudio / mpg123 等选一个即可，见下节；需要联网 |
| 本地 sherpa VAD/ASR/KWS/TTS | `sherpa-onnx`、相应模型；numpy 按后端需要 | ASR、KWS、sherpa VAD 的代码会惰性导入 numpy；sherpa TTS 后端未直接导入 numpy，仍应满足 wheel 自身依赖 |
| Wyoming 网关 | `wyoming`（既有文档使用 1.10.2）加所选后端依赖 | 仅启动 HTTP 服务不需要 Wyoming；`tools/wyoming_probe.py` 还直接导入 numpy |
| 麦克风闭环 `server/loop.py` | Linux 上的 `arecord` / `aplay`（alsa-utils）、声卡与权限 | HTTP 上传音频不需要录音工具 |
| `vad: silero`（raw-ORT） | numpy、单独的 `onnxruntime` 与模型 | 第二份运行时；默认单运行时策略禁止加载，须显式调整策略 |
| `vad: webrtc` | 与目标架构/Python ABI 匹配的 `_webrtcvad` 扩展 | `vad/webrtc/pylite/` 带 aarch64 扩展；先验证可导入，不能在 Windows 复用板端 `.so` |

**当前默认全在线**：[config/server.yaml](config/server.yaml) 使用 `remote-api / openai / edge`，VAD 为 none、KWS 为 passthrough。模型文件仍安装到磁盘，但部署单元和本地托管 HTTP 都带 `--no-warm`，不预加载本地模型。直接手工启动 `app.py` / `wyoming_gateway.py` / `loop.py` 时也应加 `--no-warm`。此参数不会改变已选档，服务监听成功也不代表云端认证和供应商可达性已通过真实推理验证。

核心 [server/core.py](server/core.py)、[server/cfg.py](server/cfg.py)、[server/app.py](server/app.py) 和音频基础处理使用标准库；配置读取器处理 YAML 子集，无需单独安装 PyYAML。numpy 的可选加速路径可回退到标准库，具体模型后端的惰性导入另算。

### 1.2 Edge 解码：复用已有后端

[edge_tts_client.py](tts/models/edge/edge_tts_client.py) 支持 `miniaudio`、`soundfile`、`mpg123`、`ffmpeg`。`auto` 按此顺序尝试；显式指定时只尝试该项。当前 [config/online.yaml](config/online.yaml) 选择 `online.edge.decoder: miniaudio`。

- 已有可用 miniaudio：继续用它，无需再装 mpg123 或 ffmpeg。
- 已有可用 mpg123：把 `online.edge.decoder` 改成 `mpg123`，无需安装 miniaudio。
- 已有 soundfile 或 ffmpeg：先用实际 MP3 验证能解码再复用；soundfile 还取决于 libsndfile 的 MP3 支持。
- 都没有时：按目标平台选择一个有兼容发行包的后端。不要为了 Edge 解码把上述后端重复安装。

miniaudio 是 Edge 的可选解码实现，**不是语音服务全局必需依赖**。不使用 Edge，或远端 TTS 已返回 WAV 时，不需要为这条路径安装 MP3 解码器。

### 1.3 板端安装边界与运行时

目标板为 Linux aarch64 / Python 3.8。Python 包只能使用与架构、Python ABI 及系统库匹配的预编译 wheel；先检查已有包和离线 wheel。使用 pip 安装时必须启用 `--only-binary=:all:`，包括传递依赖；找不到兼容 wheel 就停止，不得回退 sdist 或在板上编译。系统工具优先复用已安装版本，缺失时用发行版二进制包。确需构建时交给批准的外部构建主机。

`sherpa-onnx` wheel 自带 ORT，与单独安装的 `onnxruntime` 是两份运行时。sherpa 的 VAD/KWS/ASR/TTS 共享 `SharedEnv.sherpa()`；默认 `env.single_runtime: true` 且 `allow_pip_onnxruntime: false` 时拒绝 raw-ORT。不要因启用 sherpa 就再安装一份 pip onnxruntime。

自动探测、依赖补装和官方模型获取统一由 [deploy.py](deploy.py) 执行，命令及配置保留策略见 [DEPLOYMENT](DEPLOYMENT.md)。默认仅列计划，`--apply` 才执行；`--models all` 准备已实现后端所需的 CPU 模型。已验证的 NPU 编译交付件仍需外部提供，不会在板上编译。

在线配置使用目标机自己的 `config/online.yaml`（具名忽略，不入库），首次安装参考 [无密钥模板](config-examples/online.yaml)。不要把开发机密钥复制进部署包；既有文件保持本机可用。

## 2. `../web/offlineHMI`（板端 HDMI 原生 HMI / kiosk）—— 板端依赖最小集

**完整运行前提**（含命令、寄存器、坑）见 [offlineHMI README](../web/offlineHMI/README.md)；
这里只回答"**要装什么、最小的那个是什么**"。

**既有取证说明**：本节板上版本、字体数量与覆盖读数为原 README 的历史记录，本次文档拆分未重新连接板端验证；安装前须现场探测。`fonttools` 只用于主机构建字体子集，不是 HMI 运行依赖。`/dev/mem`、udmabuf、USB 鼠标等约束对应实际板端显示/输入后端，离线 PNG 预演不需要这些硬件条件。

### 2.1 软件包与已有环境

| 依赖 | 必需？ | 板上现状（2026-11-03 只读取证，A） | 大小 |
|---|---|---|---|
| `python3` | ✅ | 3.8.10 | 已装 |
| **Pillow** | ✅ 界面全靠它画 | **9.3.0** | 已装 |
| `numpy` | ✅ RGB565 打包／重采样 | 1.23.0 | 已装 |
| `arecord`（alsa-utils） | 语音页选 arecord 录音源才需要；合成音源不需要 | 1.2.2 | 已装 |
| 中文字体 | 可选（见 2.2） | **只有 6 个字体，无 CJK** | 见下 |
| X / 浏览器 / GStreamer / Electron / ffmpeg | **不需要** | —— | —— |

### 2.2 中文字体：`fonts-wqy-zenhei` **不是最小的**

不装就全是豆腐块（板上 `fc-list` 只有 6 个字体）。
**focal 上两个 wqy 都可装**（2026-11-04 板上 `apt-cache policy` ＋ `apt-get -s install`
＋ `apt-get download` 解包实测，A 级；**只下载解包，没有安装**）：

| 包（focal 版本） | `.deb` | Installed-Size | 字体文件本体 | 许可证（包内 `copyright` 原文） |
|---|---|---|---|---|
| **`fonts-wqy-microhei` 0.2.0-beta-3** | **1,606,976 B（1.53 MiB）** | **5,134 KB** | `wqy-microhei.ttc` **5,177,387 B** | **`Apache-2.0 or GPL-3+ with Font exception`** |
| `fonts-wqy-zenhei` 0.9.45-7ubuntu1 | 7,468,354 B（7.12 MiB） | 16,430 KB | `wqy-zenhei.ttc` 16,791,251 B | `GPL-2 with Font embedding exception and M+ FONTS License` |
| `fonts-arphic-ukai` | — | 17,249 KB（Debian 13 读数） | — | 未核 |
| `fonts-arphic-uming` | — | 21,009 KB（同上） | — | 未核 |
| `fonts-noto-cjk` | — | 91,217 KB（同上） | — | 未核（太重，别用） |

⇒ 确认目标板确实缺少可用中文字体后，可安装 `fonts-wqy-microhei`——`1 个新装包、无依赖`，
比 zenhei **省 11.3 MB 安装空间 / 5.9 MB 下载**。

**CJK 覆盖（A）**：`fc-query` 读 microhei 的 charset ＝ `4e00-9fc3`，即基本 CJK 全区；
本 HMI 实际用到的 **546 个汉字**（实测统计）全在该区间内 ⇒ 显示不会缺字。

**许可证（A，包内 `/usr/share/doc/<pkg>/copyright` 原文）**：

| 包 | 字体许可证 | 对"随仓库分发子集"的含义 |
|---|---|---|
| `fonts-wqy-microhei` | `Apache-2.0 or GPL-3+ with Font exception`（可择一，**双授权**） | ✅ **Apache-2.0 档允许再分发**：带上 NOTICE/许可证即可把子集 ttf 放进仓库 |
| `fonts-wqy-zenhei` | `GPL-2 with Font embedding exception and M+ FONTS License` | ⚠️ 「Font embedding exception」只豁免**把字形嵌进文档**；字体文件本身随仓库走仍按 GPL-2，还要带上 M+ 的条款 ⇒ **不适合做仓库内子集** |

> 两个包 `debian/*` 打包部分另有 GPL-3+／GPL-2，但那管的是打包脚本，不影响字体使用。

### 2.3 比"装字体"更小的两条路（真·零依赖）

1. **界面文案只用 ASCII**（英文/拼音/图标）⇒ 字体依赖直接归零，最省；
2. **把固定文案做成子集字体**：本 HMI 源码里出现 **546 个不同汉字**（实测统计），
   以 **microhei（Apache-2.0）** 为基底用 `fonttools` 裁子集，预计 0.2–0.5 MB（C，未裁过），
   随仓库走、不装系统包。**别用 zenhei 做基底**（GPL-2 + M+，见上表）。
   代价：吃新字符要重新裁一次；许可证声明文件（Apache-2.0 全文 + NOTICE）要一起入库。

### 2.4 除软件包外还必须有（硬件／权限，不是"装"）

| 项 | 说明 |
|---|---|
| **root** | `/dev/mem` 是 `crw------- root root`；sink 要写 `0x40080054` |
| `/dev/udmabuf0` | 128 MiB @ phys `0x30100000`，`u_dma_buf` 模块已加载 |
| **USB 鼠标** | 板上目前没插；插上后设备号现场查（不是写死） |
| 麦克风（MCP01 USB 声卡） | 只有语音页要 |
| **1080p 显示器** | PL 时序写死 1920×1080；不接 SDI 相机时刷新率约 81 Hz（详见 offlineHMI/README 的警告） |

---

## 3. 模型权重（不入库，运行前自备）

| 用途 | 目录/路径 | 来源 |
|---|---|---|
| 本地 VAD/ASR/KWS/TTS | `/mnt/data/models/**`（板上） | sherpa-onnx 官方发布页（见 [VAD](vad/README.md)、[KWS](kws/README.md)、[TTS](tts/README.md)） |
| Edge TTS | 无需权重 | 在线服务 |
| NPU LLM（ERNIE/Qwen3） | `/mnt/data/...`、`/root/qwen3/...` | iCraft 编译产物（见 `llm/`；npuserv 位于 HA4FPAI 的 `../npuserv/`） |
| ekho TTS | `tts/ekho-linux/`（源码）＋板上 48k 音库 | 见 [TTS README](tts/README.md) |

---
