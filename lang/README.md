# repos —— 依赖与运行前提

本目录是 WCIS 语音链（`lang/`）的 README，2026-11-04 随 `lang` 移入 `HA4FPAI/`（板上层）内，路径统一为 `repos/HA4FPAI/lang/`。**权重/模型不入库**（`.gitignore` 挡 `*.onnx` 等）。
下面按"跑哪一块需要装什么"列，避免多装、避免为转码背 ffmpeg。

---

## 1. `server`（语音对话服务编排层）—— Python 依赖

| 依赖 | 必需？ | 用途 | 装法 |
|---|---|---|---|
| **miniaudio** | **✅ 必需**（Edge TTS 的 mp3→PCM 解码，~0.1 MB） | `chain.tts: edge` 时把 Edge 的 mp3 解成采样 | `pip install miniaudio` |
| **edge-tts** | 用 edge 档才需要 | 调 Edge「大声朗读」在线中文 TTS（无需 key，需联网） | `pip install edge-tts` |
| **sherpa-onnx** | 用本地 VAD/ASR/KWS/TTS 才需要 | 本地模型推理；**它自带 onnxruntime**（与 pip 的 ORT 是两份库，别混用） | `pip install sherpa-onnx`（板上用厂商/官方轮子） |
| **numpy** | 本地 sherpa 档 / silero-VAD / 加速路径需要 | 特征与张量 | `pip install numpy` |
| **onnxruntime** | **默认不需要**（单运行时）；只有 `vad: silero`（raw-ORT）＋显式关掉 `env.single_runtime` 才要 | 直接跑 silero VAD onnx＝**第二份运行时**（费内存，不推荐） | `pip install onnxruntime`（优先 `vad: sherpa`） |
| **wyoming**（1.10.2） | 用 `wyoming_gateway.py`（HA 上层对接）才需要 | Wyoming TCP 协议外壳 | `pip install wyoming==1.10.2` |
| webrtcvad | 用 `vad: webrtc` 才需要 | WebRTC VAD；`vad/webrtc/pylite/` 已带 **aarch64** 版 `.so`，板上不用另装 | （板上用 pylite；本机需 win 版） |

> **核心服务本身是纯标准库**（`app.py`/`cfg.py`/`core.py`/`fsm.py`/`audioio.py`），不装上面这些也能起服务、跑自检档（`echo`/`sine`）。
>
> **只有一份 onnx 运行时**：sherpa-onnx 自带 ORT，VAD/KWS/ASR/TTS 全共用这一份；`pip onnxruntime` 是第二份，
> 默认被 `env.single_runtime: true` 禁掉。`app.py` / `wyoming_gateway.py` 启动时会 **warm**
> （先拉 sherpa runtime，再装 active 档；`--no-warm` 可跳过）。

### 最小安装（本机自检）
```bash
pip install miniaudio numpy
```

### 上板（Ubuntu 20.04 arm64 / py3.8）跑全链
```bash
pip install miniaudio edge-tts numpy
pip install sherpa-onnx          # 本地 VAD/ASR/KWS/TTS
pip install wyoming==1.10.2      # 如果要 Wyoming 面给 HA
# vad: silero 档另装 onnxruntime
```

---

## 2. `../web/offlineHMI`（板端 HDMI 原生 HMI / kiosk）—— 板端依赖最小集

**完整运行前提**（含命令、寄存器、坑）在 `../web/offlineHMI/README.md`；
这里只回答"**要装什么、最小的那个是什么**"。

### 2.1 软件包：**除中文字体，一个都不用装**

| 依赖 | 必需？ | 板上现状（2026-11-03 只读取证，A） | 大小 |
|---|---|---|---|
| `python3` | ✅ | 3.8.10 | 已装 |
| **Pillow** | ✅ 界面全靠它画 | **9.3.0** | 已装 |
| `numpy` | ✅ RGB565 打包／重采样 | 1.23.0 | 已装 |
| `arecord`（alsa-utils） | 只有语音页要 | 1.2.2 | 已装 |
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

⇒ 板端装法：`apt-get install -y fonts-wqy-microhei`——`1 个新装包、无依赖`，
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

## 3. ffmpeg / mpg123 —— **不是必需**

Edge 输出 mp3，本仓用 **miniaudio** 解码（~0.1 MB）。**不需要 ffmpeg**（完整版 200 MB+）。
若不想装 miniaudio，也可换 `mpg123`（`apt install mpg123`，~0.2 MB），在
`config/online.yaml` 的 `online.edge.decoder` 指定即可（`miniaudio|mpg123|soundfile|ffmpeg|auto`）。

---

## 4. 模型权重（不入库，运行前自备）

| 用途 | 目录/路径 | 来源 |
|---|---|---|
| 本地 VAD/ASR/KWS/TTS | `/mnt/data/models/**`（板上） | sherpa-onnx 官方发布页（见 `vad/README.md`、`kws/README.md`、`tts/README.md`） |
| Edge TTS | 无需权重 | 在线服务 |
| NPU LLM（ERNIE/Qwen3） | `/mnt/data/...`、`/root/qwen3/...` | iCraft 编译产物（见 `llm/`、`npuserv/`） |
| ekho TTS | `tts/ekho-linux/`（源码）＋板上 48k 音库 | 见 `tts/README.md` |

---

## 5. 端口一览（`config/server.yaml`）

| 端口 | 服务 |
|---|---|
| 18091 | lang/server HTTP 面（`/api/v1/chain` 等） |
| 10300 / 10301 / 10302 | lang/server Wyoming 面（wake / asr / tts，取代 HA4FPAI voice_gateway） |
| 5002 / 2046 / 5003 | piper_tts / ekho-server / edge_tts_server（可选外部 TTS 服务） |
| 18080 | npuserv（板上 NPU 槽位监督） |
| 10500 / 10520 | klar NLU engine / HTTP（上层） |

---

## 6. 通信：`ggradio`（试验台）↔ `server`

**一句话**：`ggradio` 不拿 HTTP 打 `server`。它是**双通道**——试验台把链路嵌进自己进程跑（逐段粒度、可下载产物）；配置改动要靠**重启**才生效，重启由它托管 `app.py` 子进程完成。

### 6.1 试验台通道（进程内）

`../web/ggradio/console.py` 把 `server` 的 `sys.path` 插进来后直接 `import core`，
调 `core.Chain.stage(...)` 逐级跑（sar/vad/kws/asr/llm/tokenizer/tts）。**这是为了逐段粒度**——
HTTP 面没有"只跑 KWS"这种端点。副作用：试验台与运行中的 server **各自独立**，试验台改档不碰服务。

### 6.2 配置生效通道（托管子进程）

`app.py` 在**启动时**读一次 `config/`（无热加载）。要让服务用上新配置，唯一办法是重启进程。
`console.py --serve` 把 `app.py` 拉起为子进程（默认 `:18091`），页面上「重启服务」=
`terminate → 等退出 → 同参数 respawn → 探 /health`（实测冷启动 ~8 s，等待窗 30 s）。
端口上已有别人起的服务时只显示状态、**不接管**。

### 6.3 lang/server 对外接口（ggradio 之外的真实面）

**HTTP（`app.py`，`:18091`）**

| 方法 | 路径 | 传 | 回 |
|---|---|---|---|
| POST | `/api/v1/chain` | 音频（裸 body / JSON `audio_b64` / multipart） | JSON；`?format=wav` 回裸 wav |
| POST | `/api/v1/asr` | multipart 字段 `audio` | `{"code":0,"text":…}`（funasr 兼容；`?llm=1` 带 LLM） |
| POST | `/v1/audio/transcriptions` | multipart 字段 `file` | `{"text":…}`（OpenAI 兼容） |
| POST | `/llm` `/tts` | `{"text":…}` 或裸文本 | LLM 回复 / wav（`?format=json` 回 b64） |
| POST | `/reset` | 可选 `?session=` | 清多轮上下文 |
| GET | `/health` `/stats` `/components` `/` | — | 装配／计数／档位表／用法 |

公共 query：`llm=0|1`、`tts=0|1`、`out_rate`、`session`、`format=wav|json`、`tts_tier=<档>`（请求级临时换档）。

**Wyoming（`wyoming_gateway.py`，三个 TCP 口）**：`wake :10300`、`asr :10301`、`tts :10302`，
给 Home Assistant 原生集成（`Detect/Transcribe/Synthesize`），协议不变。
取舍：Wyoming 分块推流、后端契约"整段" ⇒ 网关把 chunk 累积到 `AudioStop` 再一次性回结果，
**不支持部分转写**。依赖 `wyoming` 包（板上/HA 侧才有）。

### 6.4 调度

`core.Chain` 进程内单例；组件按 `(stage, 档名)` 懒建缓存；TTS 合成带锁串行；LLM 按 `session` 隔离；
HTTP 侧 `ThreadingHTTPServer`（每请求一线程）、Wyoming 侧 `asyncio`，共用同一 `Chain`。**无队列**：请求即跑即回。

### 6.5 控制台自己的端点（`ggradio/console.py`，默认 `:7861`）

`GET /`（页面）· `GET/POST /api/config`（读/写 119 参数；`value="***"`=不改；回 `needs_restart`）·
`POST /api/config/reset`（恢复到启动快照）· `POST /api/reload`（只重载**控制台**链路）·
`GET /api/stages` · `POST /api/run`（multipart `stages,file,text,session,out_rate`）·
`GET /api/artifact/<name>` · `GET /api/health` ·
`GET /api/server` + `POST /api/server/{restart,start,stop}` + `GET /api/server/log`（托管服务管理）。
