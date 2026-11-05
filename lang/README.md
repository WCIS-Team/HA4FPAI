# lang —— 语音链与服务接口

本目录是 WCIS 的语音链，位于 `repos/HA4FPAI/lang/`。依赖、模型和配套 HMI 的运行前提统一见 [DEPENDENCIES](DEPENDENCIES.md)；权重/模型不入库。

## 导航

- [链路图](chain.txt)：HTTP、Wyoming、麦克风三个入口与各处理阶段。
- [依赖与模型前提](DEPENDENCIES.md)：按自检、板端在线、本地 sherpa 档探测和补齐依赖。
- [服务运行说明](server/README.md)：语音服务与运行入口。
- [一键部署与模型获取](DEPLOYMENT.md)：探测环境、补缺失依赖、获取模型、备份部署和验收。
- [Ubuntu 20.04 Linux 安装包](packaging/linux/README.md)：解包运行 `sudo ./install.sh` 或 `sudo apt install ./lang-server_版本_arm64.deb`；apt 安装系统依赖，模型后台下载，默认全在线。
- [服务部署说明](server/deploy/README.md)：systemd 单元与部署布局。
- [offlineHMI](../web/offlineHMI/README.md)：配套 HDMI 原生界面。

## 端口一览（`config/server.yaml`）

| 端口 | 服务 |
|---|---|
| 18091 | lang/server HTTP 面（`/api/v1/chain` 等） |
| 10300 / 10301 / 10302 | lang/server Wyoming 面（wake / asr / tts，取代 HA4FPAI voice_gateway） |
| 5002 / 2046 / 5003 | piper_tts / ekho-server / edge_tts_server（可选外部 TTS 服务） |
| 18080 | npuserv（板上 NPU 槽位监督） |
| 10500 / 10520 | klar NLU engine / HTTP（上层） |

---

## 通信：`ggradio`（试验台）↔ `server`

**一句话**：`ggradio` 不拿 HTTP 打 `server`。它是**双通道**——试验台把链路嵌进自己进程跑（逐段粒度、可下载产物）；配置改动要靠**重启**才生效，重启由它托管 `app.py` 子进程完成。

### 试验台通道（进程内）

`../web/ggradio/console.py` 把 `server` 的 `sys.path` 插进来后直接 `import core`，
调 `core.Chain.stage(...)` 逐级跑（sar/vad/kws/asr/llm/tokenizer/tts）。**这是为了逐段粒度**——
HTTP 面没有"只跑 KWS"这种端点。副作用：试验台与运行中的 server **各自独立**，试验台改档不碰服务。

### 配置生效通道（托管子进程）

`app.py` 在**启动时**读一次 `config/`（无热加载）。要让服务用上新配置，唯一办法是重启进程。
`console.py --serve` 把 `app.py` 拉起为子进程（默认 `:18091`），页面上「重启服务」=
`terminate → 等退出 → 同参数 respawn → 探 /health`（实测冷启动 ~8 s，等待窗 30 s）。
端口上已有别人起的服务时只显示状态、**不接管**。

### lang/server 对外接口（ggradio 之外的真实面）

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
**不支持部分转写**。所需依赖见 [DEPENDENCIES](DEPENDENCIES.md)。

### 调度

`core.Chain` 进程内单例；组件按 `(stage, 档名)` 懒建缓存；TTS 合成带锁串行；LLM 按 `session` 隔离；
HTTP 侧 `ThreadingHTTPServer`（每请求一线程）、Wyoming 侧 `asyncio`，共用同一 `Chain`。**无队列**：请求即跑即回。

### 控制台自己的端点（`ggradio/console.py`，默认 `:7861`）

`GET /`（页面）· `GET/POST /api/config`（读/写 119 参数；`value="***"`=不改；回 `needs_restart`）·
`POST /api/config/reset`（恢复到启动快照）· `POST /api/reload`（只重载**控制台**链路）·
`GET /api/stages` · `POST /api/run`（multipart `stages,file,text,session,out_rate`）·
`GET /api/artifact/<name>` · `GET /api/health` ·
`GET /api/server` + `POST /api/server/{restart,start,stop}` + `GET /api/server/log`（托管服务管理）。
