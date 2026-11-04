# 113 板两条语音链路：A（ekho）/ B（piper）

10-27 落地。两条链路的 ASR 都**留空走远程 API**。
**2026-11-04 她裁「把 fsmn 和 funasr 删了」之后**，两条链路的分段器都是 WebRTC VAD（纯 C），
区别只剩 TTS 引擎（ekho vs piper）；链路 B 不再需要 `/root/funasr/venv`。

## 一览

| | 链路 A（lite） | 链路 B（onnx） |
|---|---|---|
| 入口 | `/funasr-lite` | `/funasr-onnx` |
| 分段 | WebRTC VAD（纯 C，无权重） | **WebRTC VAD**（原 FSMN-VAD 已删） |
| ASR | 远程 HTTP（口子，留空） | 远程 HTTP（同一个口子） |
| 出声 | ekho-server（纯 C++，:2046） | Piper（piper-tts + onnxruntime，:5002） |
| 链路进程 | 系统 python3，**不 import onnxruntime** | 系统 python3，**不 import onnxruntime**（原 venv+ORT 已删） |
| 链路进程 VmHWM | 24.3 MiB | ≈24 MiB（改 WebRTC 后；FSMN 档曾 59.9 MiB） |
| 额外常驻进程 | ekho-server（小） | piper 服务 165 MiB / 装载 22.9 s |
| 起链脚本 | `asr/run_remote_chain.sh` | `asr/run_onnx_chain.sh` |

两条都只读音频、不打开 playback 设备；段音频照旧落 `segments/`，每段一行 JSONL，`/dev/shm/asr/state*` 每秒心跳。

## 入口

```bash
# A：WebRTC VAD + 远程 ASR + ekho 出声
ASR_REMOTE_URL=http://127.0.0.1:18090/api/v1/asr /funasr-lite

# B：WebRTC VAD + 远程 ASR + piper 出声（原 FSMN 档已删）
ASR_REMOTE_URL=http://127.0.0.1:18090/api/v1/asr /funasr-onnx

# 现成 wav、跑 30 秒、后台
SRC=wav WAV=/root/funasr/testdata/c247_60.wav SECS=30 ASR_REMOTE_URL=... BG=1 /funasr-lite
```

`/funasr-lite --help` / `/funasr-onnx --help` 打全部环境变量；其余参数原样透传给 `asr_chain.py`。
日志：`logs/remote_chain.log`（A）、`logs/onnx_chain.log`（B）、`logs/piper_tts.log`、`logs/ekho_server.log`。

## TTS 两个引擎

都是"另起常驻服务 + 链路只发 HTTP"，链路进程不会因为 TTS 多 import 任何东西。

**ekho（链路 A 默认，:2046）** — `/root/ekho-bench/bin/ekho-server`，11.0，纯 C++ 单元拼接，不吃 ONNX。
接口（从二进制里核出来 + 实测）：`GET /health`、`GET /voices`、`POST /tts?wait=1`（body 是文本，回完整 WAV，48 kHz 立体声）、`POST /tts`（audio/L16 流）。
起服务：`bash /root/funasr/asr/start_ekho_server.sh`（launcher 发现端口没开会自动调）。
> 坑：这套 kit 编译时把 espeak-ng-data 路径烤成了 `D:/yzd/ekho-x/kit-espeak/share/...`，在 Linux 上它是**相对路径**，
> 所以 start 脚本会在 `kitcwd/` 下补出这层目录指向板上真正的 `espeak-ng-data`，语音数据用 `EKHO_DATA_PATH=/root/ekho-bench/p44`。

**Piper（链路 B 默认，:5002）** — `piper-tts 1.8.0`（conda env `/root/mf3/envs/tts`，py3.11 + onnxruntime 1.31.0），
模型 `zh_CN-huayan-medium.onnx`（63 MB，22050 Hz）。CLI 是一次性进程、每句重装 23 s，所以这里做成常驻服务
`lang/server/archive/piper_tts_server.py`（stdlib HTTP）：`GET /health`、`POST /tts {"text":...}` → wav。
起服务：`bash /root/funasr/asr/start_piper_server.sh`（装载约 23 s，launcher 会自动等端口）。

切换：`TTS=ekho|piper|none`，`TTS_URL=` 指定地址（默认按引擎 2046 / 5002）。

## ASR 口子（两条链路共用）

`asr/remote_asr.py`，`ASR_REMOTE_PROTO=multipart|openai|json`（`multipart` 是 HTTP multipart/form-data + `{code,text}`
约定，**不是** FunASR 官方协议——官方 runtime 走 WebSocket；旧名 `funasr` 已废，传了会报错）；
`ASR_REMOTE_TOKEN/TIMEOUT/RETRY/EXTRA`。
单段失败只记 `asr_status=error`，链继续跑；`text` 为空时不调 TTS。

## 板上自带 ASR+LLM 桥（`lang/server/archive/mimo_bridge.py`）

板上起一个 HTTP 服务，一段音频进来，**ASR → LLM → 回 LLM 文本**出去。链路只把它当普通远程 ASR，
一行代码不用改。

```
音频（multipart，字段 audio）
  → MiMo ASR   mimo-v2.5-asr   （OpenAI 兼容 chat/completions + input_audio，base64）
  → 识别文本
  → MiMo LLM   mimo-v2.6-flash （带 2 轮上下文，system prompt 约束成口语短句）
  → 回复文本 → 回 {"code":0,"text":"<LLM 回复>","asr_text":"<识别文本>", ...}
```

起服务：

```bash
bash /root/funasr/asr/start_mimo_bridge.sh          # 前台
BG=1 bash /root/funasr/asr/start_mimo_bridge.sh     # 后台，日志 logs/mimo_bridge.log
```

接链路（同机，走 127.0.0.1）：

```bash
ASR_REMOTE_URL=http://127.0.0.1:18090/api/v1/asr ASR_REMOTE_TIMEOUT=60 /funasr-lite
ASR_REMOTE_URL=http://127.0.0.1:18090/api/v1/asr ASR_REMOTE_TIMEOUT=60 /funasr-onnx
```

端点：`/api/v1/asr`（主）、`/api/v1/asr?llm=0`（只 ASR）、`/v1/audio/transcriptions`（openai 协议）、
`/llm`、`/reset`（清上下文）、`/health`、`/stats`、`/`（用法页）。
板上没有 curl，手测用 `asr/t_bridge.py`。

环境变量：`MIMO_API_KEY`（已内置）、`LLM_ENABLE=0` 只做 ASR、`HISTORY_TURNS=2`、
`LLM_REASONING=none`（默认关思考链）、`LLM_MAX_TOKENS=128`、`PORT=18090`、`SYSTEM_PROMPT`。

**关思考链是必须的**：`mimo-v2.6-flash` 默认先生成 `reasoning_content`，实测白吃 1~8 秒；
传 `reasoning_effort=none` 后 `reasoning_tokens` 归零，单次从 3.2 s 降到 1.5 s。

实测（c247_60.wav 60 s / 8 段，`PACE=realtime`，TTS=none）：

| 项 | 关思考前 | 关思考后 |
|---|---|---|
| ASR 单段 | 546~1169 ms | 643~1463 ms |
| LLM 单段 | 3.2~13.9 s | 1.4~13.9 s |
| ASR+LLM 合计 | 75.3 s / 8 段 | 53.1 s / 8 段 |
| `rtf_mean` | 1.276 | 0.899 |
| `backlog_s` | 19.1 | 16.7 |

**结论**：功能通，8 段全 ok，多轮上下文串得起来。但 LLM 延迟**由 MiMo 服务端决定**，
1.4~13.9 s 抖动、与回复长度不成正比，本地优化到头了。要稳的实时就 `?llm=0` 只走 ASR，
或者把 `SYSTEM_PROMPT` 压得更短 + `LLM_MAX_TOKENS=64`。

## 实测（113 板，2026-10-27，远端是本机假服务注入 150 ms）

**链路 A**（a067.wav 67 s，`PACE=free`，TTS=ekho）

| 项 | 值 |
|---|---|
| 段数 / ASR / TTS | 4 / 4 ok / 4 ok |
| asr_ms p50 | 165 ms |
| tts_ms max | 2086 ms（48 kHz 立体声，`tts_rtf_mean=0.44`） |
| 链路 VmHWM | 24.3 MiB，`majflt=0` |
| run_s | 8.7 s（67 s 音频） |

**链路 B**（c247_60.wav 前 12 s，`PACE=free`，TTS=piper）

| 项 | 值 |
|---|---|
| WebRTC VAD | （原 FSMN 档读数 `vad_pairs=18`→9 段 / `vad_load_s=3.3` 留作历史参考；改 WebRTC 后待重测） |
| asr_ms p50 | 163 ms |
| tts_ms max | 6886 ms，`tts_rtf_mean=1.248` |
| 链路 VmHWM | 59.9 MiB，`majflt=4`（FSMN 档历史读数） |
| lag_max / backlog | 30.3 s / 30.3 s |

**结论**：ekho 的 RTF 0.44 追得上实时；piper 的 RTF 1.25 **追不上**——链路 B 在仿实时下积压会一直涨，
要实时就把 `LENGTH_SCALE=0.8`（语速 +25%）或者链路 B 也换成 `TTS=ekho`。ASR 那头两个引擎都一样，p50 ≈165 ms 全是网络往返。

## 未决

- 用户提到已写了一个 `piper_stream.py` 想直接用：**板上（`/root`、`/home`、`/opt`、`/usr/local`、`/mnt`）和本机工作区都没找到这个文件**。
  现在链路 B 用的是 `lang/server/archive/piper_tts_server.py`（同样接口：`POST /tts {"text"}` → wav）。拿到 `piper_stream.py` 后，
  只要它也有一个"文本进、wav 出"的 HTTP 端点，把 `TTS_URL` 指过去即可，不用改链路。

## 文件

| 文件 | 作用 |
|---|---|
| `asr_chain.py` | 链路主体：`--segmenter rms|webrtc`、`--backend slot|remote-api|sensevoice-sherpa`、`--tts none|piper|ekho`（fvad/punc 已随 funasr 删除） |
| `asr/run_remote_chain.sh` | 链路 A 起链（自动拉 ekho） |
| `asr/run_onnx_chain.sh` | 链路 B 起链（自动拉 piper） |
| `asr/remote_asr.py` | 远程 ASR 客户端（三协议 + 重试 + 健康探测） |
| `vad/webrtcseg.py` | WebRTC VAD 分段（2026-11-04 移入 vad/） |
| `asr/tts_ekho.py` / `asr/tts_piper.py` | 两个 TTS 客户端（同签名 `say(text, seq) -> (wav, ms)`） |
| `lang/server/archive/piper_tts_server.py` | 常驻 Piper 服务（stdlib HTTP，模型只装一次） |
| `asr/start_ekho_server.sh` / `asr/start_piper_server.sh` | 两个 TTS 服务的启动器（含数据路径补丁 / 等端口） |
| `asr/mock_asr_server.py` | 假远端 ASR，验口子用 |
| `lang/server/archive/mimo_bridge.py` | 板上 ASR+LLM 桥（MiMo ASR → MiMo LLM，给 `ASR_REMOTE_URL` 用） |
| `asr/start_mimo_bridge.sh` | 桥的启动器（BG=1 后台，等端口） |
| `asr/t_bridge.py` | 桥的全端点自测（板上没 curl） |
| `/funasr-lite`、`/funasr-onnx` | 根目录入口 |
| `asr_chain.py.bak-20261027`、`asr/backends.py.bak-20261027` | 改造前原件 |
