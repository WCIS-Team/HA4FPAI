# mimo —— 极简 SiliconFlow ASR+LLM + ekho 语音回环

这是从板端旧 `/root/funasr` 链路整理出来的独立小包：**一个桥 + 一个板端脚本**，不依赖
`lang/server` 那一整套编排框架，也不需要 ONNX/模型权重。云端 API 走**硅基流动 SiliconFlow**
（原 MiMo `mimo-v2.5-asr` / `mimo-v2.6-flash` 已替换）。

```
板载麦克风 --arecord--> [mimo_voice.py] --multipart POST--> [mimo_bridge.py :18090]
                                                     │  SiliconFlow ASR (XingChenAGI/XingChenASR-V3.2-Ultra)
                                                     │  SiliconFlow LLM (Qwen/Qwen2.5-7B-Instruct)
板载喇叭 <--aplay-- [ekho-server :2046] <--POST /tts--  LLM 回复文本
```

## 最快用法（两个脚本）

### 1. 起 SiliconFlow 桥（电脑或板子都行）

```bash
export SILICONFLOW_API_KEY=你的key
bash start_mimo_bridge.sh          # 前台；或 BG=1 bash start_mimo_bridge.sh 后台
curl -s http://127.0.0.1:18090/health
```

密钥也可以写在 `.siliconflow-key`（`chmod 600`），**不要**写进任何脚本。

### 2. 板端录音、上传、ekho 念出来

```bash
python3 mimo_voice.py \
  --bridge-url http://192.168.1.107:18090/api/v1/asr \
  --device plughw:MCP01,0
```

- 说一句 → 自动断句上传 → 打印「识别/回答」→ 调 `ekho-server` → 有喇叭就 `aplay`，没有就落盘到 `tts/`。
- `--once` 只跑一轮；`--input testdata/a067.wav --tts none` 可以不接麦克风测上传链路。
- `--check` 检查 bridge 和 ekho 的 `/health`。

## 备选：原来的完整链路

如果你要的是原 `funasrarm` 的分段/JSONL/状态/多段处理，而不是上面的单文件版：

```bash
ASR_REMOTE_URL=http://192.168.1.107:18090/api/v1/asr bash funasr-lite
# 用现成 wav 自测：
SRC=wav WAV=testdata/a067.wav ASR_REMOTE_URL=http://127.0.0.1:18095/api/v1/asr TTS=none bash funasr-lite
```

`funasr-lite` 只是 `asr_chain.py` 的薄启动器；`asr_chain.py` 负责采集/分段/队列/JSONL/心跳，
远端 ASR 走 `asr/remote_asr.py`，TTS 走 `asr/tts_ekho.py`。它默认 `TTS=ekho`，找不到
`ekho-server` 时会自动降为 `TTS=none` 并只落 wav。

## 目录

| 文件/目录 | 作用 |
|---|---|
| `mimo_bridge.py` | SiliconFlow ASR+LLM 桥：`POST /api/v1/asr`（multipart 字段 `audio`），回 `{"code":0,"text":"...","asr_text":"..."}` |
| `start_mimo_bridge.sh` | 起桥（相对本目录，支持 `BG=1`、`PORT`、`SILICONFLOW_API_KEY`/`.siliconflow-key`） |
| `mimo_voice.py` | **最小板端脚本**：录音→上传→ekho→aplay |
| `install_ekho.sh` | ekho-server 安装器：支持 `--kit`/`--tar`/`--release`/`--build` |
| `funasr-lite` | 原完整链路入口（`asr_chain.py`） |
| `asr_chain.py` | 完整链路：采集/分段/远程 ASR/TTS/JSONL/状态 |
| `asr/remote_asr.py` | 远程 ASR HTTP 客户端（funasr/openai/json 三种协议） |
| `asr/tts_ekho.py` | ekho-server 客户端（文本→wav） |
| `asr/webrtcseg.py` + `asr/pylite/` | WebRTC VAD 分段（aarch64 CPython 3.8 预编译扩展） |
| `asr/start_ekho_server.sh` | 起板上已有的 ekho-server（`EKHO_ROOT` 可指定 kit 位置） |
| `asr/mock_asr_server.py` | 无 API key 时测口子用的假 ASR（:18095） |
| `testdata/a067.wav` | 16 kHz 单声道测试样本 |

## 依赖 / requirements

默认路径（`mimo_bridge.py` + `mimo_voice.py` + `asr_chain.py --backend remote-api`）**只用 Python 标准库**，
没有 numpy / onnxruntime / requests 依赖。Python 版本要求见 `requirements.txt`：
板端 3.8.10、本机 3.13 都可跑。

```bash
# 可选：默认路径不装第三方也能跑；这条主要是把 setuptools 补齐
python3 -m pip install -r requirements.txt
```

只有下面这些可选档才要额外装包：

| 档 | 需要的包 |
|---|---|
| `--segmenter webrtc`（板端默认，已带 `asr/pylite/`） | 无（其他平台才需要 `webrtcvad==2.0.10`） |
| `--backend sensevoice-sherpa` | `numpy`, `sherpa-onnx` |
| `--tts piper` | `piper-tts`（本包不带 piper，默认 ekho） |

`ekho-server` 是单独的 C++ kit（默认 `/root/ekho-bench`），不是 pip 包；没有它时链路仍可跑
`TTS=none`，`mimo_voice.py` 也可以只打印回答。

## ekho-server 从哪来 / 怎么装

**先回答身份问题：`ekho-server` 不是上游 `hgneng/ekho` 自带的东西，而是你的 Linux fork 里新增的服务。**
它对应：

- 仓库：`https://github.com/WCIS-Team/ekho-Linux-AIGC`（旧 `takomitkm/ekho-Linux`、`takomitkm/ekho-AIGC`、
  `takomitkm/ekho` 都会 301 到它）
- 源码：`src/ekho_server_main.cpp` + `src/http_server.cpp`
- 上游 `hgneng/ekho` 没有这个二进制、没有 `/tts`、没有 `/health`

### 给 MiMo 用户的三条路

| 场景 | 命令 |
|---|---|
| 已有打包好的 kit（113 板 aarch64 推荐） | `bash install_ekho.sh --kit /path/to/kit-ekho` |
| 已经有 kit 的 tar 包 | `bash install_ekho.sh --tar /path/to/kit-ekho-aarch64.tar.gz` |
| 从 GitHub Release 下载 kit | `bash install_ekho.sh --release` |
| 本机 Linux 从源码构建（较慢） | `bash install_ekho.sh --build` |

`install_ekho.sh` 会把 kit 安装到 `$HOME/.local/ekho`（可用 `--root` 或 `EKHO_ROOT` 改），
装完打印 `EKHO_ROOT` / `EKHO_SERVER` 两个变量；之后 `funasr-lite` 和 `mimo_voice.py`
默认走 `TTS=ekho` 就能直接用。

### 维护者：发布一个 kit 给 `--release` 用

当前 `kit-ekho` 是 aarch64 的完整可运行 kit（`bin/ekho-server` + `share/ekho-data` +
`share/espeak-ng-data`，约 146 MB，来源见 `kit.env` 的 `KIT_SOURCE`）。**已发布到 Release：**
<https://github.com/WCIS-Team/ekho-Linux-AIGC/releases/tag/ekho-kit-20261109>
（资产 `kit-ekho-aarch64.tar.gz`，123,496,339 B，md5 `A5785705F51F674839813786A5490DD5`）；
`bash install_ekho.sh --release` 现在可直接下载安装。要重发时：

```bash
cd /path/to/kit-parent
tar czf kit-ekho-aarch64.tar.gz kit-ekho
gh release create ekho-kit-20261109 kit-ekho-aarch64.tar.gz \
  --repo WCIS-Team/ekho-Linux-AIGC \
  --title "ekho aarch64 kit 2026-11-09" \
  --notes "ekho-server + Mandarin 48k pack for 113 board"
```

之后任何人执行 `bash install_ekho.sh --release`，脚本会从最新 Release 里自动找
`kit-ekho*.tar.gz` 并安装。

## SiliconFlow Bridge（替代原 MiMo Bridge）

原 `mimo_bridge.py` 已改为 **`siliconflow_bridge.py`**（文件名仍为 `mimo_bridge.py`），
使用硅基流动 SiliconFlow API：

- **ASR**：`XingChenAGI/XingChenASR-V3.2-Ultra`，OpenAI 兼容 `/audio/transcriptions`
- **LLM**：`Qwen/Qwen2.5-7B-Instruct`，OpenAI 兼容 `/chat/completions`
- Base URL：`https://api.siliconflow.cn/v1`
- 密钥：`SILICONFLOW_API_KEY` 环境变量（或 `.siliconflow-key` 文件，`chmod 600`）

环境变量对照表（原来 `MIMO_*` → 现在 `SILICONFLOW_*` / 通用名）：

| 旧环境变量 | 新环境变量 | 说明 |
|---|---|---|
| `MIMO_API_KEY` | `SILICONFLOW_API_KEY` | **必填**，硅基流动 API Key |
| `MIMO_BASE_URL` | `SILICONFLOW_BASE_URL` | 默认 `https://api.siliconflow.cn/v1` |
| `MIMO_ASR_MODEL` | `ASR_MODEL` | 默认 `XingChenAGI/XingChenASR-V3.2-Ultra` |
| `MIMO_LLM_MODEL` | `LLM_MODEL` | 默认 `Qwen/Qwen2.5-7B-Instruct` |
| `MIMO_ASR_LANG` | `ASR_LANG` | 默认 `zh` |
| `LLM_ENABLE` | `LLM_ENABLE` | 1=接 LLM，0=只做 ASR |
| `HISTORY_TURNS` | `HISTORY_TURNS` | 多轮上下文轮数，默认 2 |
| `LLM_REASONING` | `LLM_REASONING` | 推理强度，默认 `none`（关思考链） |
| `LLM_MAX_TOKENS` | `LLM_MAX_TOKENS` | 最大生成 token，默认 128 |
| `SYSTEM_PROMPT` | `SYSTEM_PROMPT` | 系统提示词 |

**板端完全不用改**：`ASR_REMOTE_URL=http://<本机IP>:18090/api/v1/asr` 依然有效，
`/funasr-lite`、`/api/v1/asr?llm=0`、`/llm`、`/health` 等端点都保持兼容。

### 自测（不需要 SiliconFlow key）

```bash
# 1) 起假 ASR 服务（兼容 openai /audio/transcriptions）
python3 asr/mock_asr_server.py --port 18095 &
# 2) 用现成 wav 走一遍最小脚本（不录音、不 TTS）
python3 mimo_voice.py --input testdata/a067.wav \
  --bridge-url http://127.0.0.1:18095/api/v1/asr --tts none --once
# 3) 原链路（rms 分段，避免非 aarch64 机器加载 .so）
python3 asr_chain.py --source wav --input testdata/a067.wav \
  --segmenter rms --backend remote-api \
  --asr-url http://127.0.0.1:18095/api/v1/asr --tts none \
  --jsonl out/selftest.jsonl --state out/selftest.state --segs-dir segments
```

## 发布 / 历史

- 2026-10-29：`asr_chain.py`、`mimo_bridge.py`、`tts_ekho.py` 等来自板端 `/root/funasr` 的整目录备份
  （`workenv/backup-asr`）；其中 `asr_chain.py` 的 md5 `1ff8815c…` 是当年板上现役件。
- 2026-11-09 白天：整理出 `S:\work\WCIS\repos\mimo`，补相对路径启动器、最小 `mimo_voice.py`、
  `requirements.txt`、ekho 安装器与自测说明，不再依赖 `/root/funasr` 绝对前缀。
- 2026-11-09 晚：云端 API 从 MiMo 换成硅基流动 SiliconFlow
  （`XingChenAGI/XingChenASR-V3.2-Ultra` + `Qwen/Qwen2.5-7B-Instruct`）；
  `/health`、`/llm`、整条 `/api/v1/asr` 均已实测通过（ASR 6.5 s / LLM 0.36 s）。
- 2026-11-09 晚：ekho aarch64 kit 打进
  [WCIS-Team/ekho-Linux-AIGC Release `ekho-kit-20261109`](https://github.com/WCIS-Team/ekho-Linux-AIGC/releases/tag/ekho-kit-20261109)。
- 本目录作为 `WCIS-Team/HA4FPAI` 的 **`mimo` 分支** 发布（从 `riir` 拉出，只新增 `mimo/` 目录）。
