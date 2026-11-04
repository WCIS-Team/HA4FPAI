# funasrarm —— 113 板双语音链路副本（目录名沿用旧称，代码已不含 funasr）

从内网开发板 `192.168.1.113` 的 `/root/funasr` 复制而来（2026-10-29）。
复制那一版**代码一行没改**，md5 已逐文件核对。
**2026-10-30 起这份副本改成正本**：采集侧那三件（`DEV` 点名／卡在位门禁／零帧非零退出）先在这份改、`bash -n`＋`py_compile` 过完再推回板上，两端 md5 逐件对齐——板上现役 `1ff8815c…`／`8e782850…`／`fbe95e76…`，登记见 wiki `PS/板端我方代码与产物台账.md` **#217**。

板子：aarch64（U.FMSH），4 核 / 993 MB 内存，Ubuntu 20.04，系统 python 3.8.10。

## 这是什么

两条并存的语音链路，共同点是 **ASR 走远程 HTTP**（板上不留声学模型）：

| | 链路 A（`funasr-lite`） | 链路 B（`funasr-onnx`） |
|---|---|---|
| 分段 | WebRTC VAD（纯 C，见 `lang/vad/webrtc/`） | **WebRTC VAD**（2026-11-04 起；原 FSMN-VAD 已删） |
| ASR | 远程 HTTP | 远程 HTTP |
| TTS 产物 | ekho-server 以 `--no-save` 流式回传，`tts_ekho.py` 落 `/root/funasr/tts/*.wav` | `piper_tts_server.py` 把 wav 回给调用方，`tts_piper.py` 落同一目录 |
| 链路进程 | ≈24 MB | ≈24 MB（改 WebRTC 后不再装 ORT，原 FSMN 档 ≈60 MB） |
| 链路进程是否用 onnxruntime | **不用** | **不用**（只剩 Piper 那个独立常驻进程用 ORT） |

**2026-11-04 她裁「把 fsmn 和 funasr 删了」**：FSMN-VAD 权重、`vadseg.py`、`puncseg.py`、
`env_probe.py`、`shims/`、funasr 取模型脚本与 funasr-onnx 四个后端全删；链路 B 的分段器
改成 WebRTC，`PY` 回到系统 python3 ⇒ **`/root/funasr/venv` 不再需要**。
两条链现在唯一的区别是 TTS 引擎（ekho 纯 C++ vs Piper 神经 TTS）。

## 目录

```
funasrarm/
├─ asr_chain.py            链路主体：分段 / 队列 / 心跳 / jsonl / TTS 串联
├─ selftest.py             早期自检
├─ funasr-lite             链路 A 入口（原在板子根目录 /funasr-lite）
├─ funasr-onnx             链路 B 入口（原在板子根目录 /funasr-onnx）
├─ asr/                    后端、分段器、TTS 客户端、起链脚本
│  ├─ remote_asr.py        远程 ASR 客户端（funasr / openai / json 三协议）
│  ├─ tts_ekho.py          链路 A 的 TTS 客户端（合成→落盘 `/root/funasr/tts/`）
│  ├─ tts_piper.py         链路 B 的 TTS 客户端（合成→落盘 `/root/funasr/tts/`）
│  ├─ piper_tts_server.py  常驻 Piper 服务（stdlib HTTP，:5002）
│  ├─ mimo_bridge.py       板上 ASR+LLM 桥（MiMo ASR → MiMo LLM，:18090）
│  ├─ mock_asr_server.py   假远端 ASR，验口子用（:18095）
│  ├─ run_remote_chain.sh  链路 A 起链（自动拉 ekho）
│  ├─ run_onnx_chain.sh    链路 B 起链（自动拉 piper）
│  ├─ start_ekho_server.sh / start_piper_server.sh / start_mimo_bridge.sh
│  ├─ t_bridge.py          临时验证脚本（t_strict / t_vaddbg3 已随 FSMN 删除）
│  └─ REMOTE_CHAIN.md      板上的原始说明文档（更详细）
├─ models/Fsmn-vad/        FSMN-VAD 权重（524 KB，链路 B 用）
└─ testdata/               两个测试样本（c247_60.wav 60 s、a067.wav）
```

## 板上怎么起

```bash
ASR_REMOTE_URL=http://127.0.0.1:18090/api/v1/asr /funasr-lite
ASR_REMOTE_URL=http://127.0.0.1:18090/api/v1/asr /funasr-onnx
```

`ASR_REMOTE_URL` 唯一必填。TTS 服务没起会自动拉。`/funasr-onnx --help` 看全部环境变量。

采集设备：两份起链脚本各有 `DEV=${DEV:-plughw:MCP01,0}`，`SRC=mic`（默认）时先过一道**卡在位门禁**——从 `DEV` 取卡标识去查 `/proc/asound/cards`，命中不到就打当前卡表并 `exit 3`，**不回落**成 `plughw:0,0`。按短名点名就够：`MCP01` 来自 USB 产品字符串描述符，插在哪个 USB-A 口都一样；会变的是卡 **index**，那才跟 probe 顺序绑定。`SRC=wav` 不进门禁。

链里那侧 `asr_chain.py --device` 默认同样是 `plughw:MCP01,0`；`MicSource` 现在收回 `arecord` 的 stderr（留尾 4000 B），**mic 读到 EOF 且一帧没有 ⇒ 打 `arecord_rc`＋stderr 并 `exit 4`**。改动前 stderr 进 `DEVNULL`、进程 `rc=0`，"设备填错"和"房间安静地说完了话"在 jsonl/state 里完全同形。

```bash
DEV=plughw:0,0 ASR_REMOTE_URL=... /funasr-lite   # 临时换卡
```

接板上自带的 ASR+LLM 桥（`lang/server/archive/mimo_bridge.py`）：

```bash
BG=1 bash asr/start_mimo_bridge.sh
ASR_REMOTE_URL=http://127.0.0.1:18090/api/v1/asr ASR_REMOTE_TIMEOUT=60 /funasr-lite
```

## 这份副本没带什么（以及为什么）

| 没带 | 大小 | 原因 |
|---|---|---|
| `venv/` | 374 MB | aarch64 Linux 的二进制，换机器就是废的，要用得重建 |
| `models/` 里另外 6 个模型 | 2.1 GB | 当前两条链路**一个都不用**本地 ASR 模型（全走远程）；只有 `Fsmn-vad` 是链路 B 的必需件，已带 |
| `out/` | 104 MB | 运行产物（jsonl） |
| `segments/` | 62 MB | 运行产物（段音频） |
| `tts/` | 1.4 MB | 运行产物（合成音频） |
| `logs/`、`__pycache__`、`*.bak-*` | 小 | 可再生的东西 |

那 6 个没带的模型是早期"本地跑 ASR"实验留下的：

```
ContextualParaformer  864 MB     punc_ct-vad_rt-272727  273 MB
punc_ct-272727        274 MB     SenseVoiceSmall        232 MB
Paraformer-large      228 MB     Paraformer-large-online 228 MB
```

要用本地 ASR 或本地标点才需要，`asr/board-fetch-models*.sh` 里有下载脚本。

## 注意：这份副本在 Windows 上跑不起来

拷过来是为了留存 / 对照 / 改代码，**不是 Windows 版**。具体障碍：

- WebRTC VAD 那一套（分段器＋`.so`＋包装）已整体移入 `lang/vad/webrtc/`；`_webrtcvad.cpython-38-aarch64-linux-gnu.so` 是 aarch64 Linux 扩展，Windows 加载不了
- `asr/*.sh`、`funasr-lite`、`funasr-onnx` 是 bash 脚本
- `start_ekho_server.sh` 里那段补 `D:/yzd/...` 软链的补丁是给板上 ekho kit 用的
- 同级 `..\funasr` 才是 Windows 版（`asr_demo.py` / `run_server.py` / 自带 venv，已修好路径可直接跑）

在 Windows 上只有两件事有意义：读代码、改完再传回板上。
