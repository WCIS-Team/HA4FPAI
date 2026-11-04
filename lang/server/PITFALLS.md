# lang/server 踩过的坑（PITFALLS）

> 2026-11-04 起累积。每条都是**实测撞到**的，不是推测；格式：现象 → 根因 → 绕法/判据。
> 目的：换机器、上板、接新模型时不必重踩。

## 一、sherpa-onnx 相关（最容易把进程直接搞死）

| # | 现象 | 根因 | 绕法 / 判据 |
|---|---|---|---|
| 1 | 起服务后**进程直接没了**，stderr 一行 `InitKeywords:310 Open keywords file failed` | sherpa `KeywordSpotter` 在 keywords 文件打不开时是 **abort 整个进程**，不是抛 Python 异常 | **构造前逐个 `os.path.isfile` 预检**（tokens/encoder/decoder/joiner/keywords_file），缺就抛 ValueError。见 `backends/front.py:KwsSherpa` |
| 2 | `KwsSherpa` 找不到 `keywords.txt` | zh-en 3M 模型包根目录**没有** keywords.txt，词表在 `test_wavs/keywords.txt`（根目录那个 `keywords_raw.txt` 是空的原料） | 自动回退：先找 `<model_dir>/keywords.txt`，再找 `<model_dir>/test_wavs/keywords.txt`；config 里显式给的相对路径找不到也回退 |
| 3 | 装载 matcha TTS 时 **segfault** | 把 matcha **声学模型**当 vocoder 传进去，C++ 侧 `Unsupported model_type: matcha-tts` | matcha **必须**外挂独立 vocoder（vocos 等）；`tts_profiles.sherpa.vocoder` 必填且文件要在 |
| 4 | 装载中文 piper/vits 时 **进程 abort** | 中文 piper 必须带 lexicon 构造，否则 C++ 侧 abort（不是异常） | 先"带 lexicon"试，失败再退"不带"；`tts_profiles.sherpa.lexicon` 指到 lexicon.txt |
| 5 | `sherpa_onnx.Vad` 在本机 `hasattr` 为 False | **本机 pip 版 sherpa-onnx 2.0.1 的 Windows 轮子没暴露 `Vad` 类**；只有板上那份 wheel 有 | `vad: sherpa` 只板上可用；本机用 `vad: silero`（raw-ORT）或 `vad: rms` |
| 6 | KWS 命中后连报同一词 | 没有 `reset_stream` | 每次命中后必须 `reset_stream` |
| 7 | 末尾字漏识别 | 流式 transducer 尾部 blank 没冲出来 | 段尾补 `tail_paddings_s`（默认 0.66 s）静音；给 0 就可能漏 |

## 二、webrtc / 资源相关

| # | 现象 | 根因 | 绕法 / 判据 |
|---|---|---|---|
| 8 | `ModuleNotFoundError: pkg_resources`（import webrtcvad 时） | `lang/vad/webrtc/pylite/webrtcvad.py` 顶部 `import pkg_resources`，**setuptools≥81 已移除该模块**（本机 82） | 本档内置绕过：直接 `import _webrtcvad` 用 C 扩展，版本号那行与我们无关（`front.py:VadWebrtc`） |
| 9 | `ModuleNotFoundError: _webrtcvad` | `pylite/` 里只有 **aarch64** 的 `.so`，Windows 没有 | 本档只能板上验；本机用 silero/rms。真要在本机跑，需另装 win 版 `webrtcvad`（未做） |
| 10 | 两套 onnx 运行时同时存在 ⇒ 白占内存（板上 993 MB 很吃紧） | **pip 的 `onnxruntime` 与 sherpa-onnx 轮子里捆的 ORT 是两份不同的库**；实测 `import sherpa_onnx` **不会**把 `onnxruntime` 带进 `sys.modules` | **单运行时**：`SharedEnv.sherpa()` 是唯一 import 点，四个 sherpa 档共用；`Chain.warm()` 启动即热（本机 5.8 s 装 ASR）；默认 `env.single_runtime: true` 让 `shared.ort()` 直接报错——要 `vad: silero`(raw-ORT) 必须显式 `single_runtime: false`。判据：warm 后 `onnxruntime in sys.modules == False` |
| 11 | 板上装第二个 NPU 模型失败 / CMA 见底 | NPU 槽位的 **CMA 池随 worker 进程退出才归还**，且一次只能一个模型；`apply()` 要 50–132 s | `npuserv` 必须**独立 worker 进程**（死了才释放）；本服务只 HTTP 编排，不把它塞进本进程；加载前看 `/status` 的 `CmaFree` |

## 三、音频与协议

| # | 现象 | 根因 | 绕法 / 判据 |
|---|---|---|---|
| 12 | 48k 麦克风输入的识别很差 | 模型要 16k，库不替你重采样 | 降采样用 **decimate3-mavg3**（与 31 抽 sinc 识别结果一致、快 11–14×）；升采样（22050→48000）用 **linear**（频谱相关 0.9997，polyphase 在 A55 慢 107×） |
| 13 | Wyoming ASR 客户端收不到"部分转写" | 本服务后端契约是**整段**，Wyoming 是分块推流 | `wyoming_gateway.py` 把 chunk 累积到 `AudioStop` 再一次性出 `Transcript`（**本版不支持部分转写**，已在文件头注明） |
| 14 | Wyoming `Describe` 的 Info 校验失败 | wyoming 1.10.2 要求每个 Artifact 带 `attribution` | info() 里每个模型/voice 都带 attribution（照 `HA4FPAI/boards/voice_common.py` 的 `SHERPA_ATTR`） |
| 15 | 板上"没声音/录不到" | 板子 `/dev/snd` 只有 timer，**无麦克风、无声卡** | 输入只能走文件/HTTP/Wyoming 客户端；输出落 wav（等喇叭方案） |

## 四、本仓实现层

| # | 现象 | 根因 | 绕法 / 判据 |
|---|---|---|---|
| 16 | config 里 `sherpa:` 下面一堆键被压平到父节 | 初版迷你 yaml 解析器只支持**一层**嵌套，而 `asr_profiles.sherpa.encoder` 是两层 | `cfg.parse` 改成**缩进栈**支持任意层级（`cfg.py`） |
| 17 | `sherpa:   # 注释` 这行没开成子节 | 行内注释被当成标量值 | `_clean` 里"整行值是注释 ⇒ 返回空串"，才按开子节处理 |
| 18 | KWS 档选了 `sherpa` 却报"keywords_file 不在" | config 显式写 `keywords_file: keywords.txt` 指向了不存在的根目录文件 | 显式相对路径不存在时回退到自动查找（坑 2） |
| 19 | `repos/` 里我的文件未提交、也不敢提交 | 工作区压着另一条会话的 `lang/` 重组（200+ 改动未提交） | 按仓库规矩：**夹着别人的提交就停下报告、不推**；只把文件放到位 |
| 20 | 配置从单文件 `lang/config.yaml` 拆成 `lang/config/` 后，旧读者（`archive/mimo_bridge.py`）读不到 mimo 节 | 它按 `config.yaml` 向上查找；目录布局它不认识 | 拆出的 `mimo` 节放进了 `online.yaml` 的 `online.mimo`（值与原默认相同）；mimo_bridge 现在走内置默认 + `MIMO_*` env。`lang/kws/para.md`、`lang/asr/para.md` 仍写旧路径（另一条会话的页，未替它改）——以 `lang/config/` 为准 |
| 21 | 外部（在线）API 的 endpoint/key 散在 `asr.yaml`/`tts.yaml`/`klar.yaml`/archive 里，换在线服务要改多处 | 本地档与在线档混在同一文件 | 收敛到 **`online.yaml`** 的 `online.asr/llm/tts/mimo`；`asr.yaml`/`tts.yaml` 只留本地档；后端用 `Config.online(kind)` 读，密钥只记 env 名/`keyfile`（D-27，不写字面值） |
| 22 | Edge TTS 拿到的音频**不是 wav**；且边缘设备不该为转码背 ffmpeg（211 MB） | 微软 Edge 朗读端点固定返回 mp3；ffmpeg 太大 | `lang/tts/models/edge/edge_tts_client.py` 做**可插拔解码链**，默认 **miniaudio**（~0.1 MB，实测与 ffmpeg 帧数一致 81792@24k、更快 86ms vs 328ms）。板上 `pip install miniaudio`（或 `apt install mpg123`）即可完全不装 ffmpeg。另：Edge 是在线服务要联网（`online.edge.proxy`），无需 key |
| 23 | **真起服务时每个请求都 500**（自检却全绿） | `app.main()` 里 `_CHAIN = Chain(cfg)` 漏了 `global _CHAIN` ⇒ Handler 读到的模块级 `_CHAIN` 永远是 `None`。smoke 的 HTTP 测试是**手工注入** `app_mod._CHAIN`，抓不到这个 | 已修：`main()` 加 `global _CHAIN`。判据＝**真起进程** `curl /health` 返回 200（`ok=True`），不能只靠自检 |
| 24 | Edge 在线档**长文本被静默丢掉尾巴**（不报错、不抛异常，只是音频变短） | 实测（edge-tts 7.3.1，2026-11-04，探针 `workenv/tts-limit-probe/`）：单次 `Communicate` 的音频累计上限 **600 s = 3,600,000 B**（≈**2,827 汉字**，每字≈0.21 s）。2800 字 → 594.3 s 完整；3000/3200/6400 字 → **都恰好 600.0 s**，超出的字直接没念。库自己按 4096 字节切请求，切完仍在同一个 session 里累计，所以顶穿的是这个 600 s 天花板 | 靠**切块**躲开：`tokenizer_profiles.tts_split.max_chars: 20`（占上限 0.7%）。代码里有 `MAX_CHARS_TRIPWIRE=1000`，超过就 stderr 告警。**别为了减请求数把 `max_chars` 抬进 2800 字区间**——那是丢字，不是变慢 |
| 25 | UI 里出现「mel+vits」「mel+ekho」这类组合，选了必失败 | **结构上不存在**：vits/piper 是整链模型（自带前端+声学+声码器，不吃外部 mel）；ekho 是录音拼接，压根没有 mel 输入 | 只有**前端选空**时才允许选一体 TTS；前端非空时只给 声学(matcha)×声码器(vocos/HiFi-GAN)。ggradio 前端联动禁用＋后端 `tts_combo_items` 再拒（HTTP 400），两道都留 |

## 五、迁移遗留（待办）

- ~~`HA4FPAI/deploy/` 的 `klar-voice-*.service` 仍指旧文件~~ **已处理（2026-11-04）**：四个语音单元退掉、搬到 `HA4FPAI/archiver/ha4fpai-voice/deploy/`；语音改由 `lang/server/deploy/lang-server-voice.service` 起（她定"语音不再 HA 起"）。
- `HA4FPAI` 的 `docs/` 仍按旧架构描述语音链路（voice_gateway 在 boards/），后续以本服务为准。
