# ggradio —— lang 语音链试验台（参数全暴露 ＋ 逐段可跑可下载）

**一句话**：把 `repos/HA4FPAI/lang/config/*.yaml` 的**每一个参数**摊平成网页表单（说明取自 YAML 注释，
写回保留注释与顺序），并把 `repos/HA4FPAI/lang/server` 的**八级链路**（`sar → kws → vad → asr → router →
llm → tokenizer → tts`）做成"**任选子集、上传就跑到、每级产物都能下载**"的试验台。

> `kws` 先于 `vad`（她 2026-11-04 定）；`router` 是 ASR 之后由 klar NLP 决定去 LLM／固定回应／
> verify（verify 还没实装，留空跳过）。

## 颜色与 TTS 组合（2026-11-04 她定）

- **颜色口径**：<span>红＝不可用/未实测</span>；<span>黄＝实测可用但非交付</span>；正常＝现役交付（ekho）。
- `llm_profiles.npuserv.model` 的 3 个本地件（`ernie-4.5-0.3b`／`qwen3-0.6b`／`mock`）→ **全红**；
- TTS 选项除 **ekho** 外 → **全黄**；`mel+matcha+vocos`（唯一成功上板组合）也黄。
- **TTS 组合面板**：前端(mel)／声学／声码器 三下拉 ＋ 一体 TTS 下拉，**按依赖联动**——
  前端非空 ⇒ 禁用一体、只能 matcha+vocos；前端为空 ⇒ 只能选 piper/ekho。
  `mel+vits`／`mel+ekho` **不出现**（结构上不存在，见 `lang/tts/README.md` §2.4）。
  点「应用组合」写回 `chain.tts` / `tts_profiles.sherpa.kind` / `online.tts.proto`，重启服务生效。

代替 Gradio 的原因（2026-11-04）：`import gradio` 会 eager 拉 pandas／matplotlib／fastapi
全家桶（RSS 250–400 MB、wheel 81 MB），而这里要的只是"几个表单 + 一次跑"。本目录
**纯标准库**，RSS ≈ 18–25 MB，py3.8 / py3.13 通跑（板上同样是 3.8）。

## 文件

| 文件 | 作用 |
|---|---|
| `console.py` | stdlib `ThreadingHTTPServer`：参数读写 + 逐段运行 + 产物下载 |
| `page.html` | 单页（无框架）：① 参数面板 ② 逐段试验台 |
| `README.md` | 本页 |

产物落在**系统临时目录**（`CONSOLE_ARTIFACTS` / `--artifacts` 可改），不进库。

## 起法

```bash
# 本机（Windows/Linux 都行）：自动向上找 repos/HA4FPAI/lang
python console.py
python console.py --lang-dir S:/work/WCIS/repos/HA4FPAI/lang --port 7861
python console.py --host 0.0.0.0 --artifacts /tmp/lc        # 给别的机器看

# 板上 / 102：同一个 Python 3.8 也能跑（默认档 echo/sine/none/passthrough/punct 不要模型）
python3 console.py --lang-dir /root/HA4FPAI/lang
```

浏览器开 `http://127.0.0.1:7861/`。**路径优先级**：`--lang-dir` > `LANG_DIR` > 从本文件向上找 `lang/`。

## ① 参数面板

- 九个 YAML 一个文件一个标签页（`asr/klar/kws/online/sar/server/sherpa-onnx/tts/vad`），
  **119 个参数**全部列出：左边全路径（点一下复制）、中间按类型给控件、右边是说明。
- **说明从哪来**：本行行内注释 ＋ 上方整行注释 ＋ **所属 section 的注释（带 `[档名]` 前缀）**。
  最后这条必要——`asr_profiles.sherpa:` 那行写着"本地流式 zipformer（板端路径…）"，
  它下面 9 个参数自己没注释，不继承的话说明列全空。
- **控件按语义选**：`chain.*` 自动变成**档位下拉**（选项取自 `server/backends/__init__.py`
  的 `REGISTRY`，加 `none`）；`true/false` 给布尔下拉；数字给 number；其余给文本框。
- **密钥**：`api_key` 一律显示 `***`（`online.yaml` 里那两颗）。提交时 `***` 表示"不改"，
  所以页面既不会显示明文、也不会把掩码写进文件（D-27）。
- **写回**：按行替换值那一段，**缩进、行内注释、文件顺序一个字节不动**（PyYAML 做不到这点，
  所以这里是手写行解析，不是 YAML 库）。"保存并重载链路"会就地重建 `Chain`
  （等于重启链路，但不起新进程）。

## ② 逐段试验台

七级的契约与回包：

| 段 | 输入 → 输出 | 产物 | 说明 |
|---|---|---|---|
| `sar` | audio → audio | `sar-<rate>.wav` | 前处理/重采样（48k→16k 走 decimate3） |
| `vad` | audio → [seg…] | `vad-segs.zip`（逐段 wav） | 没切出段＝整段直通 |
| `kws` | audio → (hit,tag) | `kws.json` | `passthrough` 恒放行 |
| `asr` | audio → text | `asr.txt` | 多段逐识别再拼 |
| `llm` | text → reply | `llm.txt` | 失败降级回输入文本 |
| `tokenizer` | text → [chunk…] | `chunks.json` | 长句切短（压 TTS 峰值） |
| `tts` | text → audio | `tts-<rate>.wav` | 每个 chunk 都合成后拼接 |

- **任选子集**：勾哪几级就跑哪几级，**按链路固定顺序**（勾 `asr` 不勾 `kws` 也合法）。
- **上传/下载**：音频走 multipart `file` 字段；文本走 `text` 字段。文本段留空则用前面
  `asr` 的结果——所以"上传音频 → KWS → ASR → LLM → TTS → 下载 wav"是一条链走完的。
- 四个预设：`只过 KWS`、`KWS→ASR`、`全链`、`文本→LLM→TTS`。
- 每一级都回耗时、结果 JSON、产物下载链接；某一级失败只标红该行，不炸整次运行
  （与 `core.run_audio` 的"失败不断链"语义一致）。

## 参数控件：选项型自动变单选/下拉

| 情形 | 控件 |
|---|---|
| 选项 ≤ 6 且宽屏 | **一行铺开的单选点**（点一下即选，不用展开） |
| 选项 > 6 | 下拉框 |
| `true/false` | 两个单选点 |
| 数字/字符串 | number / text 输入 |

选项来源三档，优先级从高到低：

1. **registry**：`chain.<级>` 直接取 `server/backends/__init__.py` 的 `REGISTRY`（含 `none`）；
2. **curate**：`console.py` 里 `ENUMS`／`_NUM_TIERS` 手工登记的值域（`online.*.proto`、
   `online.*.reasoning`、`resample_mode`、`tts_profiles.sherpa.kind`、`edge.decoder`、
   `edge.voice`、`vad_profiles.webrtc.mode` 等）；
3. **comment**：兜底——从该参数的行内注释里抠 `a | b | c` 这种串（注释里写着"可选 A|B|C"的自动成选项）。

现值不在候选里时不隐藏：候选里补上现值并**标黄**，提醒你它在合法集合外。

## server 的接口面（它对外一共两套）

### HTTP（`lang/server/app.py`，默认 `:18091`）

| 方法 | 路径 | 传什么 | 回什么 |
|---|---|---|---|
| POST | `/api/v1/chain` | 音频（裸 body / JSON `audio_b64` / multipart） | JSON（`?format=wav` 则回裸 wav） |
| POST | `/api/v1/asr` | 音频（multipart 字段 `audio`） | `{"code":0,"text":…,"asr_ms":…}`（funasr 兼容；`?llm=1` 才带 LLM） |
| POST | `/v1/audio/transcriptions` | multipart 字段 `file` | `{"text":…}`（OpenAI 兼容） |
| POST | `/llm` | `{"text":…}` 或裸文本 | LLM 回复（`?tts=1` 连 TTS） |
| POST | `/tts` | `{"text":…}` 或裸文本 | wav（`?format=json` 回 base64） |
| POST | `/reset` | 可选 `?session=` | 清多轮上下文 |
| GET | `/health` `/stats` `/components` `/` | — | 装配状态／计数／档位表／用法 |

公共 query：`llm=0|1`、`tts=0|1`、`out_rate=<Hz>`、`session=<id>`、`format=wav|json`、
`tts_tier=<档名>`（请求级临时换 TTS 档）。

### Wyoming（`lang/server/wyoming_gateway.py`，三个 TCP 口）

`wake :10300`、`asr :10301`、`tts :10302`。给 Home Assistant 原生集成用，
**协议不变**（`Detect/Detection`、`Transcribe/Transcript`、`Synthesize/AudioChunk`）。
取舍：Wyoming 是分块推流，而本服务后端契约是"整段" ⇒ 网关先把 chunk **累积到 `AudioStop`**
再一次性回结果，**不支持部分转写**。依赖 `wyoming` 包（本机 PC 未装，只在板上/HA 侧跑）。

### 调度模型

`core.Chain` 是**进程内单例**：各级组件懒建、按 `(stage, 档名)` 缓存；TTS 合成带锁串行；
LLM 多轮按 `session` 隔离。HTTP 侧是 `ThreadingHTTPServer`（每请求一线程），
Wyoming 侧是 `asyncio`；两边共用同一个 `Chain`。**没有持久化队列**——请求即跑即回。

## ggradio 怎么和 server 通信

**两套，别混**：

| 用途 | 路径 | 生效范围 |
|---|---|---|
| **逐段试验台**（②页签） | **进程内**：`import lang/server` 后直接调 `core.Chain.stage()` | 只影响本控制台进程，**不碰运行中的 server** |
| **参数改动后生效** | 控制台**托管** `app.py` 子进程（`--serve`），「重启服务」= terminate → 等退出 → 重新 spawn | 影响真正的服务；重启后新配置才生效 |

### 现在 server 是怎么更新配置的（关键事实）

`app.py` 在**启动时**读一次 `lang/config/`（`Config` 构造在模块导入时完成），**之后没有任何热加载**。
所以：

- 改 YAML → **不会**自动生效；
- 控制台里那个"保存并重载链路"只重建**控制台自己**的 `Chain`（让试验台立刻用新档跑），
  **不会**重启 `lang/server/app.py`；
- 要让服务用上新配置，只有一条路：**重启进程**。这就是这次加的「重启服务」。

## 端点

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/` | 页面 |
| GET | `/api/config` | 九个 YAML 的全部参数（含 `desc`/`type`/`secret`/`options`）＋各段可选档 |
| POST | `/api/config` | `{file, items:[{path,value}], reload}` 写回；`value="***"` 跳过；回 `needs_restart:true` |
| POST | `/api/reload` | 只重载**控制台**的链路（不重启服务） |
| GET | `/api/stages` | 七级的 active 档、契约、说明 |
| POST | `/api/run` | multipart（`stages`,`file`,`text`,`session`,`out_rate`）或 JSON |
| GET | `/api/artifact/<name>` | 下载产物 |
| GET | `/api/health` | 控制台自身：链路路径、配置目录、各级 active |
| GET | `/api/server` | **托管服务状态**：managed/external、pid、uptime、port、`/health` 内容 |
| POST | `/api/server/restart` `/start` `/stop` | **重启／启动／停止** lang-server |
| GET | `/api/server/log` | 服务日志末尾 80 行 |

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/` | 页面 |
| GET | `/api/config` | 九个 YAML 的全部参数（含 `desc`/`type`/`secret`）＋各段可选档 |
| POST | `/api/config` | `{file, items:[{path,value}], reload}` 写回；`value="***"` 跳过 |
| POST | `/api/reload` | 只重载链路 |
| GET | `/api/stages` | 七级的 active 档、契约、说明 |
| POST | `/api/run` | multipart（`stages`,`file`,`text`,`session`,`out_rate`）或 JSON |
| GET | `/api/artifact/<name>` | 下载产物（只认自己产物目录里的文件） |
| GET | `/api/health` | 链路路径、配置目录、各级 active |

## 已知边界

1. **改配置＝直接改仓库里的 YAML**。这是设计意图（试验台就是用来改参数的），但意味着
   跑完记得 `git diff` 看要不要留；想只试不改，就 `cp -r lang /tmp/lang-test` 再用
   `--lang-dir /tmp/lang-test`。
2. **进程内跑链路**，所以需要的依赖跟 `lang/server` 一样（默认自检档一个都不需要；
   切到 `sherpa`/`silero`/`edge` 才需要模型与对应库）。
3. **模型路径是板端路径**（`/mnt/data/models/...`）：本机/School 机看到这些参数能改，
   但真跑那些档会报"路径不在"——面板照实显示，不做假默认值。
4. **麦克风**：本试验台不做浏览器录音（那是 webui.py 的事）。音频靠上传文件；
   要现场录，就用 `HA4FPAI/web/offlineHMI` 的原生录音，或另配。
5. 单位：`out_rate` 是链尾输出重采样目标（0＝跟随 `server.out_rate`）；`session` 只影响
   有上下文的 LLM 档（`echo` 档只把它回显在 meta 里）。

## 实测（2026-11-04）

- **py3.8.10（focal 容器）**：`console.py` 编译过；`/api/config` 回 **9 文件 / 119 参数**；
  `chain.asr` 的档位选项与说明正确；`api_key` 两条全部 `***`；`POST /api/config` 把
  `max_chars: 40→55` 写回且**注释原样保留**；`/api/run`（multipart，kws→asr）200 并出两个产物；
  `/api/artifact/*` 可下载。全链七级一次跑通：`sar 82ms → vad(跳过) → kws 144ms → asr 75ms
  → llm 91ms → tokenizer 94ms → tts 417ms`，产出 `sar-16000.wav / kws.json / asr.txt /
  llm.txt / chunks.json / tts-48000.wav` 六件。
- **本机 Windows py3.13**：页面渲染与交互（预设、跑、下载链接）用 Playwright 实点验证通过；
  RSS 实测 24.8 MB（上一版同名服务），py3.8 容器里 17.7 MB。