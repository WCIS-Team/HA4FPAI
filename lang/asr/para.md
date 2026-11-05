# lang/asr 可调参数全录

**口径**：以 2026-11-04 的现行代码为准（`asr_chain.py` / `asr/backends.py` / `asr/remote_asr.py` /
`asr/tts_ekho.py` / `../server/archive/mimo_bridge.py` / 两个
起链脚本），逐个从代码里挖出来的默认值，不是转述记忆。`--help` 没写默认值的参数，本文标了
★（默认值来自 argparse 的 `default=`）。

**怎么读这张页**：链路里每个可调的东西分四层——① CLI 参数（`asr_chain.py` 直跑时）；② 起链脚本
环境变量（`run_remote_chain.sh` / `run_onnx_chain.sh`）；③ 服务端配置（`lang/config.yaml` + 环境变量）；
④ 服务自身 CLI。优先级一般是 **env > 配置文件 > 代码内置默认**（mimo_bridge 严格执行；asr_chain 的
CLI 参数 > env 兜底）。

---

## 一、asr_chain.py 全部 CLI 参数（44 个）

### 1. 音频源

| 参数 | 默认 | 说明 |
|---|---|---|
| `--source` | `mic` | `mic`／`wav` |
| `--device` ★ | `plughw:MCP01,0` | ALSA 采集设备。**点名 MCP01**（板上 USB 麦的卡短名，来自 USB 产品字符串描述符，与插哪个口、probe 顺序无关）；`plughw` 的 plug 层把原生 48k 重采样到 `--rate` |
| `--input` | 必填（wav 模式） | wav 路径 |
| `--rate` ★ | `16000` | 采样率 |
| `--frame-ms` ★ | `100` | 主帧长（分段节奏的最小单位） |

### 2. 分段（rms 档）

| 参数 | 默认 | 说明 |
|---|---|---|
| `--segmenter` | `rms` | `rms`（双门限能量）／`webrtc`（纯 C VAD）。fvad 档已随 FSMN/funasr 删除（2026-11-04） |
| `--open-dbfs` ★ | `-45.0` | 开口门限：帧 dBFS ≥ 它判语音、起段 |
| `--close-dbfs` ★ | `-55.0` | 收口门限：帧 dBFS < 它累计静音 |
| `--sil-stop-ms` ★ | `600` | 静音多久断句。**起链脚本都收到 400**（她 10-12 裁「更激进些」；800 会让段撞 20 s 上限，400 把撞限打到 0，代价是段数约 2.7 倍） |
| `--min-seg-ms` ★ | `300` | 短于此的段丢弃 |
| `--max-seg-s` ★ | `20.0` | 超长强制断（撞这个上限=分段没调好） |

### 3. 分段（webrtc 档，5 个）

| 参数 | 默认 | 说明 |
|---|---|---|
| `--webrtc-mode` | `3` | aggressiveness 0..3，0 最宽松、3 最严。**默认 3 是 10-27 扫档结论**：c247_60 上 0/1/2 会把整段判成语音（60 s 只切 4 段、两段撞 20 s），3 才切 8 段零撞限 |
| `--webrtc-sub-ms` | `20` | 子帧长，只认 10/20/30；100 ms 主帧切成 5×20 ms 逐块判 |
| `--webrtc-open-ratio` | `0.4` | 语音子帧占比 ≥ 它算语音帧。想更严可给 0.6，但 10-27 实测 c247_60/a067 结果没变 |
| `--webrtc-open-frames` | `1` | 连续几帧达标才起段；1=单帧就起，2=更严 |
| `--webrtc-close-ratio` | `0.2` | 占比 ≤ 它算静音。**取 0 会把换气/齿音全算语音，断不开句** |

### 4. VAD 公共

| 参数 | 默认 | 说明 |
|---|---|---|
| `--vad-lead-ms` | `0` | 起点前多带多少 ms。**默认 0 的理由**：fvad 档库内已含 window(200)+lookback(200) 前向扩展，再补会和上一句尾部重叠；webrtc/rms 档要防吃轻声字头可试 200 |
| `--vad-min-ms` | `250` | 闭合段短于此丢弃 |
| `--vad-keep-s` | `40.0` | PCM 环形缓冲保留秒数，决定能往回切多远 |

### 5. ASR 后端

| 参数 | 默认 | 说明 |
|---|---|---|
| `--backend` | `slot` | `slot`／`remote-api`／`sensevoice-sherpa`（funasr 四档已删） |
| `--model-dir` / `--backend-dir` | 按后端名查板端默认 | `sensevoice-sherpa` 默认 `/root/funasr/models/SenseVoiceSmall`；`remote-api` 不查权重 |
| `--asr-url` | 空 | 远程 endpoint，如 `http://127.0.0.1:18090/api/v1/asr` |
| `--asr-proto` | `multipart` | `multipart`（HTTP multipart/form-data 字段 `audio`，回 `{code,text}`；旧名 `funasr` 已废。注意 FunASR 官方 runtime 是 WebSocket，不是这个）／`openai`（`/v1/audio/transcriptions` 字段 `file`+`model`）／`json`（base64） |
| `--asr-field` | 按协议 | 默认 `audio`（multipart）／`file`（openai） |
| `--asr-model` | 空 | openai 协议里的 model 名；空＝不传该字段 |
| `--asr-token` | 空 | 非空则带 `Authorization: Bearer` |
| `--asr-timeout` ★ | env `ASR_REMOTE_TIMEOUT` 兜底 30 | 秒 |
| `--asr-retry` ★ | env `ASR_REMOTE_RETRY` 兜底 1 | 重试次数（不含首次）；**asr_ms 记的是含重试的真实往返** |
| `--asr-extra` | 空 | 额外字段，形如 `language=zh,itn=1` |
| `--threads` | `4` | ORT intra-op（本地后端档才用） |
| `--sv-language` | `zh` | SenseVoice 语言标签，`auto`＝自动 |
| `--sv-textnorm` | `withitn` | SenseVoice 逆文本规整：`withitn`＝数字转阿拉伯字符 |
| `--hotwords` | 空 | funasr-contextual 的热词串（**该后端已删，参数保留但无对象**） |

### 6. TTS

| 参数 | 默认 | 说明 |
|---|---|---|
| `--tts` | `none` | `none`／`piper`／`ekho` |
| `--tts-url` | 按引擎 | piper `:5002` / ekho `:2046` |
| `--tts-dir` | 板端 `/root/funasr/tts` | 合成 wav 落盘目录 |
| `--tts-timeout` ★ | env 兜底：piper 120 / ekho 60 | 秒。**piper 装载后合成慢**，120 是按实测给的 |
| `--tts-length-scale` ★ | env `TTS_LENGTH_SCALE` 兜底 1.0 | **<1 更快**。板上 piper RTF≈1.25~1.79 追不上实时，试 0.8（语速 +25%） |

### 7. 节奏 / 运行控制

| 参数 | 默认 | 说明 |
|---|---|---|
| `--pace` | `realtime` | `free`＝全速（最快出结果）／`realtime`＝仿实时（量 rtf/lag/backlog 才有意义） |
| `--max-seconds` | `0.0` | 只跑前 N 秒（0=不限） |
| `--queue-alarm` | `0` | >0 时待转写段数超过它记一次（**真麦上这等价于缓冲区溢出**） |
| `--drain-timeout` ★ | `900.0` | 读完后给这么多秒清积压 |
| `--segs-dir` / `--jsonl` / `--state` | 板端路径 | 段音频 / 每段一行读数 / 每秒心跳 |

### 起链脚本环境变量速查（映射到上面的 CLI 参数）

**链路 A（`run_remote_chain.sh`，默认 ekho）／链路 B（`run_onnx_chain.sh`，默认 piper）**：

| env | 默认 | 对应 CLI |
|---|---|---|
| `SRC` | `mic` | `--source` |
| `WAV` | 空 | `--input`（SRC=wav 时） |
| `DEV` | `plughw:MCP01,0` | `--device` |
| `PACE` | `realtime` | `--pace` |
| `SEG` | `webrtc`（A/B 相同） | `--segmenter` |
| `WR_MODE` / `WR_OPEN` / `WR_OPEN_FRAMES` | `3` / `0.4` / `1` | webrtc 三件 |
| `SIL`（A）/ `VAD_SIL`（B） | `400` | `--sil-stop-ms` |
| `TTS` | **A=ekho / B=piper** | `--tts` |
| `TTS_URL` | 按引擎 2046/5002 | `--tts-url` |
| `LENGTH_SCALE` | `1.0` | `--tts-length-scale` |
| `SECS` | `0` | `--max-seconds` |
| `PY` | `/usr/bin/python3`（两链相同，2026-11-04 起） | — |
| `AVAIL_MIN` | A=`120000` / B=`300000` | 起链内存闸门（MemAvailable kB 下限，不够就等） |
| `BG` | `0` | `1`＝setsid 后台 + 等端口 + 打印 pid |
| `JSONL` / `STATE` / `SEGS` | 板端 out/、/dev/shm/ | 输出三件 |

---

## 二、mimo_bridge.py（:18090，ASR+LLM 桥）

**读取优先级：环境变量 > `lang/config.yaml` 的 `mimo` 节 > 内置默认。**
配置文件向上逐级查找：`config.yaml → asr/config.yaml → lang/asr/config.yaml → lang/config.yaml`，命中即停。
**密钥不进配置文件**：只从 `MIMO_API_KEY` 或 `KEYFILE`（板上 `/root/funasr/.mimo-key`，chmod 600）。

| 配置键 / env | 默认 | 说明 |
|---|---|---|
| `MIMO_API_KEY` | **必填** | 缺失 exit（启动脚本先查 KEYFILE） |
| `MIMO_BASE_URL` / `base_url` | `https://api.xiaomimimo.com/v1` | |
| `MIMO_ASR_MODEL` / `asr_model` | `mimo-v2.5-asr` | |
| `MIMO_LLM_MODEL` / `llm_model` | `mimo-v2.6-flash` | |
| `MIMO_ASR_LANG` / `asr_lang` | `zh` | `asr_options.language` |
| `BIND` / `bind` | `0.0.0.0` | |
| `PORT` / `port` | `18090` | |
| `LLM_ENABLE` / `llm_enable` | `1` | `0`＝只 ASR 不接 LLM（**要稳的实时就关**，单段 660 ms） |
| `HISTORY_TURNS` / `history_turns` | `2` | 多轮上下文轮数；0=单轮无记忆 |
| `LLM_REASONING` / `llm_reasoning` | `none` | **none=关思考链，必须关**——不关每段白吃 1~8 s（10-27 板上实测：3.2 s → 1.5 s）；可给 low/medium/high |
| `LLM_MAX_TOKENS` / `llm_max_tokens` | `128` | 回复要求 ≤80 字，128 够 |
| `SYSTEM_PROMPT` / `system_prompt` | 内置口语短句提示词 | 约束：口语化 1~2 句、≤80 字、无 Markdown/列表/表情/序号、不复述问题、直接给答案 |
| `ASR_TIMEOUT` / `asr_timeout` | `60` | |
| `LLM_TIMEOUT` / `llm_timeout` | `60` | |
| `LOG_JSONL` / `log_jsonl` | `logs/bridge.jsonl` | 逐段日志 |

（env 与 config.yaml 键名对照：`MIMO_X` 环境变量对应 config 里小写 `x`，`MIMO_` 前缀去掉。）

---

## 三、piper_tts_server.py —— **已删除（2026-11-04 她裁）**

`piper_tts_server.py`（服务）、`tts_piper.py`（客户端）、`start_piper_server.sh`（launcher）三件
连同 `TTS=piper` 档一起删了；剩下的 TTS 只有 ekho（:2046）。

## 四、TTS 客户端细节（链路内）

| 项 | ekho（:2046） |
|---|---|
| 超时默认 | 60 s（env `TTS_EKHO_TIMEOUT`） |
| `voice` | env `TTS_EKHO_VOICE`，空=服务端默认（Mandarin, 48 kHz 立体声） |

## 五、ASR 客户端细节（remote_asr.py，链路内）

| env / opt | 默认 | 说明 |
|---|---|---|
| `ASR_REMOTE_URL` | 空（必填） | 也会被 CLI `--asr-url` / `--backend-dir` 覆盖 |
| `ASR_REMOTE_PROTO` | `multipart` | multipart／openai／json（传 `funasr` 会报错提示迁移） |
| `ASR_REMOTE_FIELD` | 按协议 | 上传字段名 |
| `ASR_REMOTE_MODEL` | 空 | openai 协议 model 名；空＝不传 |
| `ASR_REMOTE_TOKEN` | 空 | Bearer |
| `ASR_REMOTE_TIMEOUT` | `30` | 秒 |
| `ASR_REMOTE_RETRY` | `1` | 重试次数（不含首次） |
| `ASR_REMOTE_VERBOSE` | `0` | `1` 打请求细节 |
| `ASR_REMOTE_RATE` | `16000` | 声明采样率 |
| `ASR_REMOTE_EXTRA` | 空 | 形如 `language=zh,itn=1` |

## 六、sensevoice-sherpa 后端（本地 ONNX 档，可选）

权重：sherpa-onnx 镜像仓的 SenseVoiceSmall（`csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17`），
板上默认目录 `/root/funasr/models/SenseVoiceSmall`。可调项只有 CLI 三个：

| CLI | 默认 | 说明 |
|---|---|---|
| `--sv-language` | `zh` | `auto`/`zh`/`en`/`ja`/`ko`/`yue` |
| `--sv-textnorm` | `withitn` | `withitn`／`woitn`（不做逆文本规整，数字留汉字形态） |
| `--threads` | `4` | ORT intra-op |

**已知形状限制**（板上实测，不是文档）：`model_quant.onnx` 缺 `vocab_size` metadata ⇒ **量化档走不通**，
必须非量化 `model.onnx`；0 号 token 必须改名 `<blk>`。所以这档在板上一直没有现成权重可用——
**要开这档先解决权重**（`ms_sizes.py` 查仓 → `ms_get.py` 下非量化 model.onnx + 改 tokens.txt）。

## 七、调参怎么下手（按症状）

| 症状 | 动哪 | 经验值 |
|---|---|---|
| 一句话被切散 | `--sil-stop-ms` 加大 / `--webrtc-close-ratio` 降 | 板端现役 400；webui 粗采集口径是 2000 |
| 段撞 20 s 上限 | `--sil-stop-ms` 减小 / `--webrtc-mode` 升到 3 | 800→400 实测撞限归零 |
| 轻声字头被吃 | `--vad-lead-ms 200` | 默认 0 是防重叠；webrtc 档可加 |
| 换气断句 | `--webrtc-close-ratio` 升（0.2→0.3） | 取 0 会断不开 |
| TTS 积压（backlog 涨） | `LENGTH_SCALE=0.8` 或 `TTS=ekho` | piper RTF 1.25~1.79 > 1 必积压 |
| LLM 回得慢 | config `llm_reasoning: none`（必关）＋ `LLM_MAX_TOKENS: 64`＋ `llm_enable: 0` | 关思考链实测省 1~8 s/段 |
| 单段 ASR 慢 | 先 `?llm=0` 分离 ASR/LLM，再看 `asr_ms` 分布 | 远程链 p50 ≈165 ms 是网络往返下限 |
| 误触发/整段判语音 | `--webrtc-mode 3`（已经是） | 0/1/2 在板上实测不可用 |

## 八、历史读数（已删档的实证，留作对比基准）

这些档 2026-11-04 已删，参数没了，但**实测数字对调参仍有基准价值**：

| 项 | 读数 |
|---|---|
| FSMN-VAD（fvad 档） | `vad_pairs=18`→9 段，装载 3.3 s，链路 VmHWM 59.9 MiB（改 webrtc 后约 24 MiB） |
| funasr-paraformer（离线 int8） | asr_ms p50 163~165 ms（远程链） |
| funasr-contextual | 权重 830 MB，**从未装载**（超 0.5 G 门禁） |
| CT_Transformer 标点 | 装载 35~43 s，`VmHWM 530+ MiB`，`--punc` 档 |
| mock 链路 A（ekho） | rtf 0.44 追实时；链路 VmHWM 24.3 MiB，`majflt=0` |
| mock 链路 B（piper） | tts_rtf 1.248 追不上，backlog 一直涨 |
