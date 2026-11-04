# para —— tts 全模型可调／可选参数速查

> 收录口径：**只收本仓实测或 config/脚本直读过的参数**（默认值带出处）；没实测过的不编数。
> 每条带「本仓裁定」——哪些随便调、哪些永远别动、哪些是 A/B 前置。出处均为本仓相对路径。
> 重组注意：2026-11-04 起神经系组件在 `sherpa/` 下，VITS 候选在 `sherpa/VITS/`。

---

## 1. 前端 sherpa-onnx（`sherpa/sherpa-mel/`，CPU）

| 参数 | 默认（实测） | 作用 | 本仓裁定 | 出处 |
|---|---|---|---|---|
| **版本** | 必须 **2.0.1** | 1.13.8 的 `debug` 打不出 token，只有句子数 | ★ 升 2.0.1，`pip --target` 装独立目录（`D:\yzd\ttsbench\sh_new`），不动现役 ASR venv | `sherpa/sherpa-mel/README.md` §1 |
| `debug` | False | True 时 stderr 打 tokens 与 final IDs——**取 token 的唯一正路** | 取 ID 时必须 True；正常合成可关 | 同上；`build_exact.py:49` |
| `num_threads` | 1 | ONNX 线程数 | 仓内一律 1（取数口径） | `build_exact.py`、`enable_debug.py` |
| `max_num_sentences` | 1 | 一次合成几句 | 仓内一律 1 | 同上 |
| `sid` | 0 | 说话人 id | baker 单说话人，固定 0 | `build_exact.py:51` |
| `speed` | 1.0 | 整体语速（generate 参数） | 取 token 时 1.0；别用它凑时长 | 同上 |
| `silence_scale` | 0.2（官方默认） | 句间静音压到 20% | 保持默认；要改静音先量它 | `sherpa/sherpa-mel/README.md` §3 |
| `noise_scale`（前端侧同名项） | 1.0 官方默认 | 传给 matcha 的初始值 | 仓内取 token 脚本一律写 0.0 | `build_exact.py:48` |
| `tokens` / `lexicon` / `dict_dir` | 文件指针 | 词表/拼音词典/jieba dict | 指针对，不是数值参数；别手改 tokens.txt（第 1 行空格 token 有坑） | `sherpa/sherpa-mel/README.md` §4、`fix_tokens.py` |

## 2. 声学模型 matcha-icefall-zh-baker（`sherpa/matcha/`，CPU）

| 参数 | 默认（实测） | 作用 | 本仓裁定 | 出处 |
|---|---|---|---|---|
| **`noise_scale` [1]** | 1.0（官方默认） | 采样噪声：0＝确定性，>0 每次不同 | ★ **A/B 对照必须 0**（0.0→两次逐字节相同 corr=1.0；0.667→0.954；1.0→0.920） | `sherpa/matcha/README.md` §1.2 |
| **`length_scale` [1]** | 1.0 | 音素时长倍率 | ★ **永远 1.0 别动**——调 1.86/2.16/2.3 凑时长是拿错参数盖 token 错的坑，2.3 会听成叠字 | 同上 §1.1 |
| `x [N,L] int64`（token 流） | sherpa final IDs 照抄 | 真正决定帧数的输入 | ★ 不自己拼：变调（你=ni2）、标点是 token、add_blank pad=1（n→2n+1）三处全要 | `sherpa/sherpa-mel/README.md` §2 |
| 图内常量（防误改，非可调） | `num_ode_steps=3`、`use_eos_bos=0`、`pad_id=1`、`jieba=1` | metadata 直读 | 改这些＝换模型重导 | `sherpa/matcha/README.md` §0 |
| 采样率 | 22050 Hz | 全神经链口径 | 固定 | 同上 |

## 3. 声码器 vocos（`sherpa/vocos/`，NPU fp16）

| 参数 | 默认（实测） | 作用 | 本仓裁定 | 出处 |
|---|---|---|---|---|
| 输入形状 | `[1,1,600,80]` | mel.T 零填充到 600 帧 | 维度参数：改它＝改图重编五段，别顺手调 | `README.md` §2 数据流 |
| 图外 iSTFT 配方 | `n_fft=1024`、`hop=256`、`center=1`、周期 Hann | mag/cos/sin → 波形（图外做） | ★ 固定配方；与 NPU 段口径绑死，别改 | `stft_verified.py`、`fix_istft.py` |
| 量化档 | **fp16 ✅**（相关 0.969–0.978，19.29 ms）；**int8 ❌**（0.12，`Cos` 算错非"太粗"；`--mix_precision` 救不了：auto 0.885→0.898，灌 CSV 反掉 0.12） | 精度/速度选型 | ★ 只准 fp16；int8 别再试 | `README.md` §4 |
| 采样率 | 22050 Hz | 与 matcha 段一致 | 固定 | 同上 |

## 4. 声码器 HiFi-GAN（`sherpa/HiFi-GAN/`，NPU fp16）

| 参数 | 默认（实测） | 作用 | 本仓裁定 | 出处 |
|---|---|---|---|---|
| 变体选型 | **`hifigan_v2.onnx`（3.75 MB）** | 时域直出波形，无 iSTFT | v2 现役（v1 55.75 MB／v3 5.86 MB 备选）；改写选 `hifigan-prelu/`（62.88 ms）不选 `rewrite-5op/`（182.38 ms，2.90× 差距） | `sherpa/HiFi-GAN/README.md`、主 README §2 |
| 量化档 | **fp16 ✅**（相关 0.998–0.999）；**int8 ❌**（0.57） | 精度选型 | ★ 只准 fp16 | 主 README §4 |
| 输入 | `mel[1,80,T]`（实测口径 T=559 四句） | 与 vocos 同一份 mel | 换声码器不改上游 | 主 README §2 |
| CPU 参考件 | 必须用**原版** `hifigan_v2.onnx` 出 | A/B 基准 | `cpu_reference.py` 的 `MODEL` 指原版，别指改写件 | `sherpa/HiFi-GAN/cpu_reference.py` |

## 5. 单体 VITS（`sherpa/VITS/`，留档不上板）

**本仓没有它们的实测参数面**——只测过内存/RTF/编译（峰值 391–755 MB、常驻 RTF 0.65–0.81、12 次 OOM；icraft 编译段错误/int64 拒绝）。VITS 系教科书参数（noise_scale／noise_scale_w／length_scale）在本仓**零实测**，按"不编数"口径此处不列；要用 Piper 先换板子，见 `sherpa/VITS/piper/README.md` §6。

## 6. ekho 现役整链（`ekho-linux/`，录音拼接）

| 参数 | 默认（实测） | 作用 | 本仓裁定 | 出处 |
|---|---|---|---|---|
| `--samplerate HZ` | **48000** | 输出采样率；`0`＝随音库自身档 | ★ 板 USB 喇叭只认 48 kHz，默认即对；44.1k 旧档走 `*.44k` 原件＋`reg-44k.sh` 锚 | `ekho-linux/README.md` §"This fork" |
| `--channels 1\|2` | **2** | 声道 | 同上，48 kHz/16-bit/2ch 是板上唯一免转换格式 | 同上 |
| `-v/--voice` | Mandarin 音库（136 MB pinyin.voice） | 音色 | 支持的只有普通话；其余语种数据已删（她 11-02 裁） | 同上；[[实时伪tts]] |
| `--english-speed DELTA` | 基础 100 wpm＋delta | 英文语速（espeak-ng 路） | 独立于中文语速，可调；voice 默认 `en-us+f3`（10-17 听感定） | `ekho-linux/README.md` |
| `EKHO_ESPEAK_RATE` / `EKHO_ESPEAK_VOICE` | 环境变量 | 免重编改英文基础速率/voice | 调英文听感走这两个 | 同上 |
| **ekho-server** | 端口 **2046** | `POST /tts`（流式 L16）、`/tts?wait=1`（整 WAV）、`/health`、`/voices` | ASR→TTS 的正牌 server；`--host 127.0.0.1` 收本机 | 同上；`scripts/funasr_bridge.py` 头注 |
| **ekho-worker** | 行协议 stdin/stdout | `!speed/!pitch/!volume/!english-speed/!voice/!info/!ping/!quit`；`--format pcm`；启动一次载库＋预热（`--no-warmup` 可关） | 常驻路线（30–75× 实时）；构建可 `--disable-http-server`／`--disable-worker` 二选一 | `ekho-linux/README.md` §worker |
| `funasr_bridge.py` | 三模式 | 默认＝POST 客户端（`--server`/`--save`/`--stream`）；`listen --bind --port(2047) --save-dir`＝小监听器；`worker --exe`＝直管常驻 | import 侧钩子 `from funasr_bridge import speak`（唯一带下划线的脚本，模块名需要） | `scripts/funasr_bridge.py` 头注 |

## 7. 基准与工具链口径（影响读数的"参数"）

| 项 | 值 | 裁定 | 出处 |
|---|---|---|---|
| `ORT_NUM_THREADS` | 板上现役 2 线程；另一批基准固定 4 | 读数标注线程数，别跨批比 | `bench-cpu/README.md` §4 |
| icraft `--target` | 只能写 `zhuge` | 写 `ZG` 会静默报错埋进度条后 | 主 README §3.1 |
| icraft 第五段 | 必须本机 Windows CLI | 容器缺 `.so` | 主 README §3.2 |
| NPU 取数 | `icraft-run --dump_format HQB --dump_output`，一句一进程 | 禁 `xrt.Session.forward` 连发（对齐坑，非首句全零） | 主 README §3.3 |
| A/B 比对纪律 | 同模型比同模型 | 跨声码器比波形无意义 | 主 README §3.5 |

## 8. 一页红线（背下来）

1. `length_scale` **永远 1.0**；语速问题改 `speed`／`--english-speed`，不碰它。
2. `noise_scale` **对照必须 0**；交付听感用官方默认 1.0（或她定的别的值，没定过）。
3. 取 token **只走 sherpa `debug=True`（2.0.1）**，不自己拼、不用 `text2token` 工具。
4. 量化**只准 fp16**：vocos int8 是 `Cos` 算错（A 级），HiFi-GAN int8 相关 0.57；`mix_precision` 两条都救不了。
5. iSTFT 配方（1024/256/center=1/周期 Hann）与 NPU 图绑死，**别改**。
6. ekho 交付 **48 kHz/2ch**；神经链 **22050 Hz**——两条链的采样率别互相串。
7. sherpa 必须独立目录装 2.0.1，**不动现役 ASR venv**（`build_exact.py` 的 `SUB_PY`＝`WCIS_TTS_PY`→当前解释器）。

## 相关

- 层级与链路：本目录 `README.md`；文档正文 `wiki/PS/Backend/MatchaVocosNPU.md`
- ekho 参数细节：`ekho-linux/README.md`；桥与协议：`ekho-linux/scripts/funasr_bridge.py`
- 决策：`wiki/archive/开发管理/状态源与决策记录.md`（D-1793547016 等）
