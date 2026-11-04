# lang/server —— 语音对话服务的编排层

一个**常驻、有端口、等别人来打**的语音服务（`lang/asr/` 是链路：拉流式跑完退出，没端口）。
2026-11-04 她定：把三条链放在一个服务里管，**各级组件都要可替换**，**共用 env（如 onnxrt）**，
配置统一在 `lang/config/`（一组件一 YAML，按文件名序深合并）。

---

## 一、三条链与组件契约

```
音频 ──▶ ① sar 前处理/重采样 ──▶ vad 切段 ──▶ kws 唤醒 ──▶ asr 转文本
                                                          │
                            ② llm 对话 ◀──────────────────┘
                                 │
                            ③ 分词器切句 ──▶ tts 逐块合成 ──▶ 合成（拼接）──▶ SAR（输出重采样）──▶ wav
```

| 级 | 契约（输入 → 输出） | 现役档 | 说明 |
|---|---|---|---|
| `sar` | (samples, rate) → (samples, rate) | `auto` / `linear` / `decimate3` | **前处理/重采样段**（她定）。48k→16k 走 decimate3-mavg3（HA4FPAI 实测正解），其余方向线性 |
| `vad` | samples → [seg, ...] | `none` / `sherpa` / `silero` / `webrtc` / `rms` | 说话段切分，**在 kws 之后跑**（她 2026-11-04 定）；`sherpa`＝`sherpa_onnx.Vad`（板上 wheel 才有）；`silero` 直跑 `vad/models/silero_vad_v5.onnx`（raw-ORT＝第二运行时，默认被 `single_runtime` 禁）；`webrtc` 复用 `vad/webrtc/webrtcseg.py`（`.so` 只有 aarch64 ⇒ 仅板上）；`rms` 纯标准库 |
| `kws` | samples → (hit, tag) | `passthrough` / `sherpa` | **先于 VAD**（她 2026-11-04 定：先唤醒门控再切段）。`passthrough`＝留空占位（恒放行，**默认**）；`sherpa`＝复用 `lang/kws/models/` 的 int8 副本，参数照 `kws/para.md` |
| `asr` | [seg] → (text, [meta]) | `sherpa` / `remote-api` / `echo` | `sherpa`＝本地流式 zipformer（移植 `voice_kit.AsrSession`）；`remote-api`＝**外部 ASR API**（multipart/openai/json 三协议，形状同 `lang/asr/asr/remote_asr.py`，失败只记 meta 不断链） |
| `router` | (text, session) → {action, reply} | `none` / `klar` / `echo` | **ASR 之后**交给 klar 的 NLP 决定：`llm`（照常进 LLM）／`fixed`（固定回应，跳过 LLM）／`verify`（**还没实装：留空并跳过**）。`klar` 档 HTTP 调 klar NLU，失败一律回落 llm |
| `llm` | (text, session) → (reply, meta) | `openai` / `npuserv` / `echo` | 云 API（mimo 调用形状）＋板上 NPU 槽位（`lang/npuserv/`，**server 管辖、留空、不接板端推理**）；失败降级回 asr 文本 |
| `tokenizer` | text → [chunk, ...] | `tts_split` / `punct` / `window` | **长句切短**（她定）：LLM 输出的长句切块后再进 TTS，**压 TTS 峰值 RSS**。三档实现都在 `lang/split/`；默认 `tts_split`＝标点优先＋**严格 20 字上限**（超了就强制切开，不丢字） |
| `tts` | chunk → (samples, rate) | `sherpa` / `http` / `edge` / `sine` | `sherpa`＝本地 matcha+vocos（`kind=vits` 可切 piper 兜底）；`http`＝**外部 TTS 服务**；`edge`＝**Edge 在线中文**（`lang/tts/models/edge/`，用 miniaudio 解 mp3，需联网） |

**链尾两环（她定）**：分词器切块后**逐块**合成 → **合成**（多段拼接，段间 80 ms 静音）→
**SAR**（统一重采样到 `server.out_rate`，默认 48000）→ wav。

**组件可替换**：档位注册表在 `backends/__init__.py`；`lang/config/server.yaml` 的 `chain` 节选 active 档，
`<stage>_profiles.<档名>` 给参数。运行时 `GET /components` 可查每级的 active 与可选项。

---

## 二、共用 env：**只有一份 onnx 运行时**（sherpa-onnx 自带 ORT）

- 她 2026-11-04 口径：**server 启动就把 sherpa-onnx runtime 拉起来**，VAD/KWS/ASR/TTS 的 onnx 模型
  全在**同一个 env**（同一份 sherpa-onnx + 它捆的 ORT）里跑，省资源。
- 实现：`core.SharedEnv.sherpa()` 是全进程唯一的 import 点；四个 sherpa 档（`vad/kws/asr/tts: sherpa`）
  都从它取运行时。`Chain.warm()` 在启动时（`app.py` / `wyoming_gateway.py`）先拉 runtime 再装 active
  的 sherpa 档；`--no-warm` 可跳过。
- **pip 的 `onnxruntime` 是第二份运行时**（实测 `import sherpa_onnx` 不会把 `onnxruntime` 带进
  `sys.modules`）⇒ 默认 `env.single_runtime: true` **禁止** `shared.ort()`。要 raw-ORT 的
  `vad: silero` 档必须显式 `env.single_runtime: false`（或 `allow_pip_onnxruntime: true`）。
- **另一条硬边界**：**NPU 槽位（npuserv）必须独立 worker 进程**——CMA 池随进程退出才归还、
  一次一模型、apply 50–132 s ⇒ LLM 那一级只能 HTTP 编排，不能进本进程。
- 实测（本机 py3.13）：`warm()` 5.8 s 装好 ASR（zh-int8 153 MB），`sherpa_onnx=True`、
  `onnxruntime=False`；`tests/smoke.py` 有 4 条断言钉住这条（37/37）。

---

## 三、端点

### HTTP 面（`app.py`，py3.8/3.13）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/chain` | 主口：音频进，全链跑。`?format=wav` 回裸 wav，否则 JSON（含 `audio_b64`） |
| POST | `/api/v1/asr` | funasr 兼容（字段 `audio`，回 `{"code":0,"text":...}`）——板上 `remote_asr` / 旧 `mimo_bridge` 客户端原样能指过来；**默认纯 ASR**，`?llm=1` 才带 LLM |
| POST | `/v1/audio/transcriptions` | OpenAI 兼容（字段 `file`）→ `{"text":...}` |
| POST | `/llm` | `{"text":...}` 只跑 LLM（`?tts=1` 连 TTS） |
| POST | `/tts` | `{"text":...}` 走 分词器→TTS→合成→SAR；默认回 wav，`?format=json` 回 b64 |
| POST | `/reset` | 清 LLM 多轮上下文（`?session=` 指定一个，不给＝清全部） |
| GET | `/health` `/stats` `/components` `/` | 健康／计数／档位清单／用法 |

公共 query 参数：`llm=0|1`、`tts=0|1`、`out_rate=<Hz>`、`session=<id>`、`format=wav|json`。
**临时换 TTS 档**：`?tts_tier=edge`（或直接 `?tts=edge`）——请求级覆盖 `chain.tts`，档名见 `/components`。

回包关键字段：`asr_text`（识别）、`text`（LLM 回复，LLM 挂了就等于 asr_text）、
`chunks`（分词器切出来的块）、`audio_b64` / `audio_rate` / `audio_ms`、
`stages_ms`（各级耗时）、`stages_error`（某级失败但链没断）、`wake`（KWS 门控结果）。

### Wyoming 面（`wyoming_gateway.py`，取代 HA4FPAI `voice_gateway`）

同进程起三个 TCP 服务，**HA 原生 Wyoming 集成零改动**：`wake :10300`、`asr :10301`、`tts :10302`。
底层走同一套 `chain.stage(...)` 后端，配置全从 `lang/config/server.yaml` 的 `server.*_port` 读。

> ⚠️ Wyoming 是分块推流，而后端契约是"整段"⇒ 本版把 chunk 累积到 `AudioStop` 再一次性出
> `Transcript`/`Detection`，**不支持部分转写**（要真流式得给后端加流式接口，另立）。
> `wyoming` 包本机未装，这个文件只在板上/HA 侧跑。

---

## 四、配置（`lang/config/`，一组件一 YAML）

| 文件 | 管什么 |
|---|---|
| `server.yaml` | 本服务：`server`（HTTP/Wyoming 端口、out_rate）/`audio`/`chain`（选档）/`env`（共用 onnxrt） |
| `split.yaml` | 分词器三档参数 `tokenizer_profiles.tts_split`（max_chars 20/strict 1）/`punct`/`window`；三档实现在 `lang/split/` |
| `sar.yaml` | 音频前处理/重采样段 |
| `sherpa-onnx.yaml` | sherpa 家族共享设置（`models_root`/provider/线程） |
| `vad.yaml` / `kws.yaml` / `asr.yaml` / `tts.yaml` | 各自 `*_profiles`——**只放本地档**（sherpa 等） |
| `online.yaml` | **所有在线/外部 API 的 API 与密钥来源**：`online.asr`/`online.llm`/`online.tts`/`online.mimo` |
| `klar.yaml` | klar NLU/HA 接入 ＋ 本地板端 `llm_profiles.npuserv` |

> **本地 vs 在线**：`asr.yaml`/`tts.yaml` 只放本地档；`chain.asr: remote-api`、`chain.llm: openai`、`chain.tts: http` 时，后端读 `online.yaml` 的 `online.asr/llm/tts`（`Config.online(kind)`）。密钥只记 env 名/keyfile 路径，**不写字面值**（D-27）。

- 加载器 `cfg.py`：**优先读目录**（按文件名序深合并，支持任意层级嵌套）；没有目录时回退旧的单文件 `lang/config.yaml`。`LANG_CONFIG_DIR` 可指定目录。
- **不写密钥**（D-27）：API key 只从环境变量/`KEYFILE` 来。
- **环境变量优先**：`ASR_REMOTE_*`（asr）、`MIMO_*`（llm）、`TTS_*`（tts）、`BIND`/`PORT`（服务）。
- 默认档全是**自检档**（`echo`/`sine`），空机起服务即可全链跑通；生产档（`sherpa`/`remote-api`/`openai`/`npuserv`/`http`）按档切。

---

## 五、起法与自检

```bash
cd repos/HA4FPAI/lang/server
python -X utf8 app.py --check          # 打印装配（哪级用哪档）就退出
python -X utf8 app.py                   # 起 HTTP 服务（默认 0.0.0.0:18091），**启动即 warm**
python -X utf8 app.py --no-warm         # 不预热 sherpa runtime（省启动时间，首个请求才装载）
python -X utf8 wyoming_gateway.py       # 起 Wyoming 三端口（板上/装了 wyoming 的机器）
python -X utf8 tests/smoke.py           # 31 项契约自检（自检档，不依赖模型）
python -X utf8 tests/tiers.py           # 真档装载实测（rms/silero/webrtc/sherpa-vad/asr/tts/kws）
python -X utf8 tests/test_fsm.py        # 对话状态机单测 16 项
```

端口 **18091**（18090=mimo_bridge、5002=piper、2046=ekho、18080=npuserv、10300-10302=HA4FPAI，都避开）。

`smoke` 覆盖：config 定位与各节解析、wav 进出、48k→16k decimate3、线性重采样、拼接+输出重采样、
立体声混缩、分词器三档（`tts_split` 严格 20 字上限 / `punct` / `window`／注册表登记／`split.yaml` 生效）、
全链（echo asr → echo llm → tts_split → sine tts → 48k 输出）、KWS 门控短路、`llm=0` 降级、
空音频拒绝、HTTP 九条路径。**全绿**（本机 py3.13.11，2026-11-04 当日 49/49——计数随并发会话新增用例浮动）。

`tiers` 实测（本机 py3.13，用 `lang/kws/models/.../test_wavs/zh_*.wav` 真实中文语音）：
`rms` ✅ 切 1 段；`silero` ✅ 真实语音切 2–4 段（2.88–6.21 s）；`sherpa` ✅ 真命中
（zh_3→法国、zh_4→女儿、zh_5→落实、zh_6→见面会）；`webrtc` ⚠️ 本机缺 `_webrtcvad` 扩展
（`pylite/` 里只有 aarch64 版 `.so`），**只能板上验**。

---

## 六、与 HA4FPAI 的关系（2026-11-04 改判：**本服务取代其语音栈，HA 退为上层接口**）

| 能力 | 归属 |
|---|---|
| 语音链路（VAD/KWS/ASR/TTS 模型与三 Wyoming 口） | **已整体搬进本服务**；HA4FPAI 的 `voice_kit/voice_common/voice_gateway/klar_asr/klar_tts/klar_wake/speak/klar_llm/wyoming_probe/bench_resampler`＋`klar-voice.json` 已移入 `HA4FPAI/archiver/ha4fpai-voice/` 并从 `HA4FPAI/boards/` 删除（git 历史可回溯） |
| LLM | HA4FPAI 原本没有（klar 是确定性 NLU）；本服务提供 `openai`（云）/`npuserv`（NPU 槽位，留空） |
| 上层 | `HA4FPAI` 保留 `klar_executor`（意图执行）/`console`/`door_unlock`/`voiceprint`/`make_registry`，通过 HTTP 或 Wyoming 调本服务 |
| 配置 | `klar-voice.json` 的内容已并入 `lang/config/`（`vad`/`asr`/`kws`/`tts`/`server` 等 YAML） |

> ⚠️ 遗留：`HA4FPAI/deploy/klar-voice-*.service` 仍指旧文件，需改为指向 `wyoming_gateway.py` 或退役（详见 `HA4FPAI/archiver/ha4fpai-voice/README.md`）。

---

## 七、还缺什么（登记在案的缺口）

1. **多数本地档未上板**：本机 py3.13 实测 `vad/rms`、`vad/silero`、`kws/sherpa` 通过（真实中文语音命中）；`vad/sherpa`、`asr/sherpa`、`tts/sherpa` 因**本机没有板端模型/wheel**未跑（报错已明说缺什么）——**板上才是最终判据**。
2. **`webrtc` 档只能板上验**：`pylite/` 里 `_webrtcvad*.so` 是 aarch64；且其 `webrtcvad.py` 的 `import pkg_resources` 在 setuptools≥81 已失效（本档已内置绕过，但走 `webrtcseg.py` 原路径的板上需核 setuptools 版本）。
3. **Wyoming 本版不支持部分转写**：`wyoming_gateway.py` 把 chunk 累积到 `AudioStop` 再出结果；真流式要给后端加流式接口（另立）。
4. **npuserv 的 ids 桥**：ERNIE 是 ids-in/ids-out（`capabilities.text_in=false`），要跑它需先补 **tokenizer ↔ ids** 桥（板端 `Qwen3-0.6B/board/tokenizer.json` 可用）。**"NPU 调用留空"即指这一层未接**。
5. **对话状态机**：`fsm.py` 是纯逻辑状态机（16 项单测过）。原「未接实时循环」已结：**2026-11-04 由 `loop.py` 接上**——一麦一喇叭、自动匹配 default（`arecord`/`aplay`，纯标准库＋alsa-utils），能量门定端点、`barge-in` 掐播报、超时走 `fsm.tick()`、**无麦不退出每 10 s 重探**；板上 `--simulate` 实测迁移史 `wake→speech_end→asr_done→llm_done→tts_done` 全真、全链无错（见 D-1793768465）。`app.py`/`wyoming_gateway.py` 两个请求驱动面仍不经过 fsm（一次请求＝半轮，设计如此）。
6. **单请求内串行**：各级 `stage.lock` 串行（ORT session 不保证线程安全），并发压测未做。
7. **TTS 段间接缝**：80 ms 静音是省事做法，长句多块接缝听感未测。
8. ~~**板上未部署**：全链只在 PC 自检；py3.8 兼容靠「纯标准库 + 无 3.9+ 语法」保证，没在板上跑过~~ ⇒ **已结（2026-11-04，D-1793767689/D-1793768465）**：`/mnt/data/lang` 板上部署完成，py3.8 `py_compile` 全过，HTTP＋Wyoming 双面实测，三 systemd 单元常驻。
9. **deploy 未改**：HA4FPAI 的 systemd 单元仍指旧语音文件（见 §六 遗留）。

---

## 八、目录

```
server/
  app.py             HTTP 面（stdlib，py3.8/3.13 通跑）
  wyoming_gateway.py Wyoming 面（wake/asr/tts 三端口，取代 voice_gateway）
  core.py            组件契约、SharedEnv、链编排（Chain.run_audio / run_text）
  cfg.py             配置加载（`lang/config/` 目录深合并，支持任意层级嵌套）
  fsm.py             对话状态机骨架（idle/listening/thinking/speaking/error）
  loop.py            fsm 驱动的真实语音循环（一麦/一喇叭自动匹配；--check/--simulate/--oneshot/--run）
  PITFALLS.md        踩过的坑（sherpa abort / segfault / pkg_resources / CMA 等）
  backends/
    __init__.py      档位注册表 REGISTRY（加后端＝在这里登记）
    audioio.py       音频进出与重采样（纯标准库）
    front.py         sar / vad（sherpa·silero·webrtc·rms，复用 lang/vad）/ kws（sherpa，复用 lang/kws）
    asr.py           asr 档（sherpa 本地流式 / remote-api 外部 API / echo）
    llm.py           llm 档（openai / npuserv / echo）
    text.py          **薄壳**：把 lang/split/ 的三档（tts_split / punct / window）转出去，别在这里加代码
    tts.py           tts 档（sherpa 本地 matcha·vits / http 外部服务 / sine）
  tests/smoke.py     契约自检（自检档，不依赖模型；并行会话已扩到 33 项）
  tests/tiers.py     真档装载实测（rms/silero/webrtc/sherpa-vad/asr/tts/kws）
  tests/test_fsm.py  对话状态机单测（16 项）
  tests/smoke_board.sh  板端语音验收（从 HA4FPAI/deploy/smoke.sh 移交）
  tools/wyoming_probe.py  Wyoming 验收客户端（从 HA4FPAI 移交）
  deploy/            板上 systemd 单元（lang-server-voice / lang-server-http）＋装法
  # npuserv/ → lang/npuserv/（与 asr/kws/tts 平级；NPU 槽位监督，仍由本服务管辖；2026-11-04 从 lang/llm/ 移入）
  archive/           归档参考（mimo_bridge / piper_tts_server / server_app / webui）
  # archive/ha4fpai-voice/ 已移到 HA4FPAI/archiver/ha4fpai-voice/（2026-11-04）
```

> TTS 的 **edge** 档实现在 **`lang/tts/models/edge/`**：`edge_tts_client.py`（edge-tts 出 mp3 → **miniaudio** 解 PCM，~0.1 MB，不必装 ffmpeg）、
> `edge_tts_server.py`（可选常驻 HTTP 服务，默认 :5003）、`README.md`。
