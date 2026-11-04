# lang/server/archive —— 语音侧的 HTTP／UI 服务

这里放**语音链路的服务端件**（2026-11-04 她定）。`lang/asr/` 是**链路**（拉流式管线，跑完退出，
没有端口），这里是**服务**（常驻、有端口、等别人来打）。

## 清单

| 文件 | 类型 | 端口 | 状态 | 外部依赖 |
|---|---|---|---|---|
| `mimo_bridge.py` | ASR + LLM 桥 | 18090 | ✅ **能用**（从 `asr/` 移入） | 无（纯标准库）＋ MiMo API key |
| `piper_tts_server.py` | TTS 服务 | 5002 | ✅ **能用**（从 `asr/` 移入） | piper-tts（conda env，py3.11 + ORT） |
| `server_app.py` | ASR 服务（双协议） | 9000（配置里定） | ❌ **不能直接跑**（从 git 恢复） | fastapi + uvicorn + `asr.engine`（已删） |
| `webui.py` | Web UI | 7860 | ❌ **不能直接跑**（从 git 恢复） | gradio + numpy + `asr.engine` + `asr.wavio`（均已删） |

前两件是**活的服务**，后两件是**归档参考**（从 `11248e88` 逐字节还原，保留原注释里的设计理由）。

---

## mimo_bridge.py —— ASR + LLM 桥（✅ 可用）

一段音频进，**ASR → LLM → 回 LLM 文本**出。链路只把它当普通远程 ASR，一行代码不用改。

```
音频（multipart，字段 audio）
  → MiMo ASR   mimo-v2.5-asr   （OpenAI 兼容 chat/completions + input_audio，base64）
  → 识别文本
  → MiMo LLM   mimo-v2.6-flash （带 2 轮上下文，system prompt 约束成口语短句）
  → {"code":0,"text":"<LLM 回复>","asr_text":"<识别文本>", …}
```

**端点**：`/api/v1/asr`（主）、`/api/v1/asr?llm=0`（只 ASR）、`/v1/audio/transcriptions`（openai 协议）、
`/llm`、`/reset`（清上下文）、`/health`、`/stats`、`/`（用法页）。

**起法**：

```bash
bash lang/asr/asr/start_mimo_bridge.sh          # 前台
BG=1 bash lang/asr/asr/start_mimo_bridge.sh     # 后台，日志 logs/mimo_bridge.log
```

**配置**：读 `lang/config.yaml` 的 `mimo` 节（向上逐级查找：`config.yaml` → `asr/config.yaml` →
`lang/asr/config.yaml` → `lang/config.yaml`，命中即停）；**环境变量逐项覆盖**。
密钥**不进配置文件**：只从 `MIMO_API_KEY` 或 `KEYFILE`（板上 `/root/funasr/.mimo-key`，chmod 600）来。

**⚠️ 关思考链是必须的**：`mimo-v2.6-flash` 默认先生成 `reasoning_content`，板上实测白吃 1~8 秒；
传 `reasoning_effort=none` 后 `reasoning_tokens` 归零，单次从 3.2 s 降到 1.5 s。

**实测**（c247_60.wav 60 s / 8 段，`PACE=realtime`，TTS=none）：`?llm=0` 单段 660 ms；ASR+LLM 单段
ASR 714 ms + LLM 1514 ms；`rtf_mean` 0.899（关思考链后）。

---

## piper_tts_server.py —— Piper TTS 服务（✅ 可用）

文本进、wav 出。**为什么必须常驻**：piper 每装载一次模型要 **22~27 s**（63 MB onnx + ORT），
一次性 CLI 没法进链路。

**端点**：`GET /health`（回 `load_s`）、`POST /tts {"text": "..."}` → wav。

**跑在哪个 python**：**必须**是装了 piper-tts 的那个 conda env（默认
`/root/mf3/envs/tts/bin/python`，py3.11 + onnxruntime 1.31.0）。**链路的 python 不需要装 piper。**

**模型**：`zh_CN-huayan-medium.onnx`（63 MB，22050 Hz），默认路径
`/root/ttsbench/models/piper/`。

**板上实测**（4 核）：装载 23~27 s、`VmHWM 165 MB`、**RTF 1.25~1.79**。
⇒ 仿实时场景**追不上**（会积压）：要实时就 `LENGTH_SCALE=0.8`（语速 +25%）或者链路改 `TTS=ekho`
（RTF 0.44）。

**起法**：

```bash
bash lang/asr/asr/start_piper_server.sh      # 自动等端口（最多 90 s）
```

---

## server_app.py —— FunASR 常驻服务（❌ 归档；要跑得补三处）

**它是什么**：PC 侧 Windows 常驻 ASR 服务，**共享一个本地引擎**、**双协议**：

| 端点 | 协议 | 回包 |
|---|---|---|
| `POST /v1/audio/transcriptions` | OpenAI 兼容（multipart: `file` [+ `model`]） | `{"text": "..."}` |
| `POST /api/v1/asr` | FunASR runtime 兼容（multipart: `audio` [+ 附加字段]） | `{"code":0,"text":"...","asr_ms":n}` |
| `GET /health` | — | 健康检查（**板上 `remote_asr.health()` 探的就是它**） |
| `GET /v1/models` | — | 模型列表 |

约定：`code` 非 0 视为失败；文本剥 `<|...|>` 标签；WAV 16k 单声道进。
host/port/preset 从 `config.yaml [server]` 读，`FUNASR_HOST/PORT/PRESET` 可盖。

**为什么跑不起来**：

| 缺什么 | 说明 |
|---|---|
| `asr/engine.py` | 引擎抽象层，**随 PC 侧 funasr 一起删了**（`11248e88:asr_chain/funasr/asr/engine.py` 可取回，但它是 funasr 专用） |
| `asr/wavio.py` | WAV 读写＋重采样，同样已删（可取回，**这件是纯工具、与 funasr 无关**） |
| `fastapi` / `uvicorn` | 原来装在已删的 venv 里 |

**要真跑起来的三步**：① 恢复 `wavio.py`（纯工具，可直接用）；② 把 `engine_mod.build(preset, device)`
换成 `lang/asr/asr/backends.py` 的 `build(name, model_dir, threads)`（现在只认
`slot`／`remote-api`／`sensevoice-sherpa`）；③ `pip install fastapi uvicorn`。

---

## webui.py —— Gradio Web UI（❌ 归档；要跑得补四处）

**用什么做的**：**Gradio**（`gr.Blocks` / `gr.Tab` / `gr.Audio` / `gr.Slider`）。默认
`http://127.0.0.1:7860`，`--no-browser` 适合后台。

**模型切换在哪**：**命令行 `--preset`，不是网页下拉框**——

```bash
python webui.py --preset sensevoice       # 或 paraformer / profile 名
python webui.py --host 0.0.0.0 --port 7860 --no-browser
```

页面上能调的是**分段参数**（5 个滑杆：`sil-stop` 200~2000 ms、`min-seg`、`max-seg`、`open` dBFS、
`close` dBFS、`lead` ms）。

**核心设计——两段式分段**（这是它比普通 demo 有意思的地方）：

> 家居里人说话常带「想了想」的停顿，分段器若按 600 ms 静音收口会把一句话切散。
> 做法是把「停顿容忍」和「切分」拆开：
> **粗段**（收口 = 停顿容忍，默认 2 s）决定「这一轮说到哪算完」，宁大勿小、保整句；
> **细段**（收口 = 600 ms 正常 VAD 口径）只在粗段内部切，决定「给 ASR 的粒度」，逐段识别再拼接。

**两个 Tab**：

| Tab | 干什么 |
|---|---|
| 录音 / 上传识别 | 整段录完（浏览器不掐停顿）→ 细切分 → 逐段 ASR → 拼接 |
| 流式（自动断句） | 边说边传 → 粗分段只认「停顿容忍」收口（=整轮）→ 收口即细切 → ASR → 文本实时追加 |

**为什么跑不起来**（四处）：

| 缺什么 | 说明 |
|---|---|
| `gradio` | 原来装在已删的 venv 里 |
| `numpy` | 同上 |
| `asr/engine.py` | 已删（funasr 专用） |
| `asr/wavio.py` | 已删（纯工具，可取回） |
| `from asr_chain import RmsSegmenter, iter_frames` | **现行 `asr_chain.py` 里这两个名字都没了**——RMS 双门限逻辑已内联进主循环，不再导出类 |

⇒ 恢复 gradio／numpy／wavio 之后，还得把分段器那段改成调用现行链路的分段逻辑（或把
`RmsSegmenter` 重新导出），才谈得上跑。

---

## 起链脚本怎么找到这些服务

`lang/asr/asr/start_mimo_bridge.sh` 与 `start_piper_server.sh` 已改成**三级查找**：

```bash
SVC_DIR=${SVC_DIR:-/root/funasr/asr}          # ① 显式给 / 板上默认位置
[ -f "$SVC_DIR/xxx.py" ] || SVC_DIR=<脚本相对 ../../server/archive>   # ② 仓库布局
[ -f "$SVC_DIR/xxx.py" ] || exit 6            # ③ 都找不到就明确报错，不猜
```

⇒ 板上仍按 `/root/funasr/asr/` 找（部署布局不变）；仓库里自动退回 `lang/server/archive/`。

## 与 `lang/tool/` 的分工

| 目录 | 放什么 | 判据 |
|---|---|---|
| `lang/tool/` | 一次性／测量用**工具**（cap/hb/探针/下载器/批跑） | 跑一次出读数，不是常驻 |
| `lang/server/archive/` | **服务端件**（HTTP 服务、Web UI） | 常驻、有端口、等请求 |
