# tts4fpai

FMSH ZG330（悟净／FMQL30TAI676H，4×Cortex-A53 @1 GHz、993 MB RAM）上的**中文 TTS 部署与 NPU 下沉**的全部可复现内容：模型改写脚本、icraft 五段编译脚本、板端取数与合成脚本、逐算子读数。

**文档正文不在这里。** 叙述性结论、决策与取舍留在文档库：
`wiki/PS/Backend/MatchaVocosNPU.md`、`wiki/PL/MOD/modellist/TTS同量级候选进NPU横评.md`、
`wiki/PS/Backend/mods/audio/ARM CPU TTS性能测试.md`。本仓库只放**能重跑的东西**。

> **2026-11-04 目录重组**：神经 TTS 全部脚本收进 `sherpa/` 子目录（两个 VITS 失败候选收进
> `sherpa/VITS/`），本级只剩 `bench-cpu/`、`ekho-linux/` 与 `sherpa/`；本页所有路径已按新布局写。

---

## 1. 层级关系（先看这张图）

一条现代神经 TTS **不是一个模型**，而是**两段**：

```text
文本 ──[ 前端 ]──> token ──[ 声学模型 ]──> mel ──[ 声码器 ]──> 波形
        ↑                ↑                    ↑
    sherpa-mel        matcha            vocos / HiFi-GAN
    （CPU，本仓 sherpa/sherpa-mel/）  （CPU）  （★ 声码器层，可整体下沉 NPU）
```

对照另外两条路线：

```text
文本 ──[ 单体端到端模型 ]──> 波形        ← Piper / Kokoro / 各种 VITS（sherpa/VITS/）
文本 ──[ 录音拼接 ]────────> 波形        ← ekho（ekho-linux/）
```

### 关键判读：谁和谁是同一层

| 目录 | 是什么 | 在链路里的位置 | 放哪跑 | 现状 |
|---|---|---|---|---|
| `sherpa/sherpa-mel/` | **前端**：文本→token，以及"mel 从哪来"的全部调查 | 第 1 段 | CPU | ✅ 用 sherpa 自己的 debug 打印 token |
| `sherpa/matcha/` | **声学模型**（icefall matcha-icefall-zh-baker）：token→mel | 第 2 段 | CPU | ⚠️ 能跑但**上不了 NPU**（算子面/量化），留在 CPU |
| `sherpa/vocos/` | **声码器**：mel→mag/cos/sin（频域），iSTFT 在图外 | 第 3 段 | **NPU** | ✅ fp16 可用（19.29 ms，与 CPU 相关 0.969–0.978） |
| `sherpa/HiFi-GAN/` | **声码器**：mel→波形（时域直出，无 cos/sin、无 iSTFT） | 第 3 段 | **NPU** | ✅ fp16 可用（**PRelu 版 62.88 ms**，相关 0.998–0.999） |

> **`vocos/` 与 `HiFi-GAN/` 是同一层（声码器）的两个可互换实现**——都吃同一份 `mel[1,80,T]`、
> 都吐同一段音频，选中哪个只影响音色与速度，不改变链路形状。
>
> **`sherpa/VITS/piper/`、`sherpa/VITS/vits-others/` 不是"另一个声码器"，是"整个 TTS"**——它们把前端+声学+声码器
> 打包成一个 ONNX，中间张量不落地，**因此切不开、也就无法只把声码器那段搬到 NPU**。
> 这是"Piper 在这块板上跑不动、而 matcha+vocos 跑得动"的结构性原因。

| 目录 | 是什么 | 为什么单独列 |
|---|---|---|
| `sherpa/VITS/piper/` | 单体 VITS TTS（zh_CN-huayan 等） | 板端**跑得动但放不下**：常驻标准 RTF **0.65–0.81（快于实时 23–54%）**，但峰值 391–755 MB ⇒ **12 次 OOM 击杀** |
| `sherpa/VITS/vits-others/` | 其余 VITS 候选（sherpa-onnx-vits-zh-ll、aishell3、melo、eula） | 全部在 icraft 编译阶段失败，见该目录 |
| `ekho-linux/` | 录音拼接式 TTS | **现役交付路线**（RTF 0.49、RSS ~20 MB），代码在本仓 `ekho-linux/`（fork 自 hgneng/ekho，含 ekho-server） |
| `bench-cpu/` | 板端 CPU-only 基准（espeak-ng / Piper / Kokoro） | 上面所有"跑不动"结论的读数来源 |

### 模型清单（按目录）＋组件拼装口径

**组件怎么拼**：神经 TTS = **前端 + 声学模型 + 声码器** 三件，目录按组件切（全在 `sherpa/` 下）；
`vocos` 与 `HiFi-GAN` 是声码器同层的两个可互换实现（二选一，吃同一份 `mel[1,80,T]`）；
`sherpa/VITS/` 是"整链打成一个模型"的候选（切不开，全败）；`ekho-linux/` 是不走神经链的现役整链；
`bench-cpu/` 是给上面所有结论出读数的基准。**权重不进仓**（§2.6），下表汇总点名实测过的模型；
死因与判据在各目录 README 与 wiki 横评页，不在这里复制：

| 组件 | 目录 | 模型 | 大小 | 状态 |
|---|---|---|---:|---|
| 前端 | `sherpa/sherpa-mel/` | sherpa-onnx **2.0.1**（Python 包，非权重） | — | ✅ `debug=True` 打 token |
| 声学模型 | `sherpa/matcha/` | `matcha-icefall-zh-baker`（`model-steps-3.onnx`） | 75.6 MB | ⚠️ 留 CPU（`rc=71`） |
| 声学模型（同族） | `sherpa/matcha/` | `matcha-icefall-zh-en` | — | ❌ 同缺陷算子，不上 NPU |
| 声码器（频域） | `sherpa/vocos/` | `vocos-22khz-univ.onnx` | 53.9 MB | ✅ NPU fp16；int8 死 |
| 声码器（时域） | `sherpa/HiFi-GAN/` | `hifigan_v2.onnx`（另有 v1 55.75 MB / v3 5.86 MB） | 3.75 MB | ✅ NPU fp16（PRelu 版 62.88 ms） |
| 单体 VITS | `sherpa/VITS/piper/` | `vits-piper-zh_CN-{huayan,chaowen,xiao_ya}-medium`；CPU 基准另测 `piper_en_US-lessac-medium` | 各 63.2 MB | ❌ 板上 12 次 OOM；icraft 段错误 |
| 单体 VITS | `sherpa/VITS/vits-others/` | `sherpa-onnx-vits-zh-ll`、`vits-icefall-zh-aishell3`、`vits-melo-tts-zh_en`、`vits-zh-hf-eula` | 30–170 MB | ❌ icraft 编译全败 |
| 基准引擎 | `bench-cpu/` | eSpeak NG / Piper / **Kokoro-82M**（zh_only） | — | 读数正本在 wiki |
| 拼接 TTS（现役） | `ekho-linux/` | ekho 引擎 + 48 kHz 拼接音库（英文走 espeak-ng） | — | ✅ RTF 0.49、RSS ~20 MB |

---

## 2. 端到端数据流（实测口径）

```text
"你好，我是你的语音助手。"
  │
  ├─[1] sherpa-onnx 2.0.1 前端（CPU）────────────────────────┐
  │      debug=True 打印出 final IDs (25)：                    │
  │      [1,1116,1,597,1,2,1,1737,1,1518,1,1117,1,314,        │
  │       1,1872,1,1845,1,1993,1,1522,1,3,1]                  │
  │      （ni2 不是 ni3 —— 变调；`,` `.` 也是 token；          │
  │        每个 token 之间插 pad=1）                            │
  ├─[2] matcha-icefall-zh-baker（CPU）─────────────────────────┤
  │      noise_scale=0, length_scale=1.0（默认，别动）          │
  │      → mel[80, T]     T=236 → 2.74 s                       │
  ├─[3a] vocos（NPU fp16）───────────────────────────────────┤
  │      输入 [1,1,600,80]（mel.T 零填充）                      │
  │      → mag/cos/sin [513,600] ×3                           │
  │      → 图外周期 Hann iSTFT（n_fft=1024, hop=256, center=1） │
  └─[3b] HiFi-GAN v2（NPU fp16）──────────────────────────────┘
         同一输入 → 60416 样本时域波形，**不需要 iSTFT**
```

**耗时实测**（板上，同一输入 `[1,80,559]` / 四句真实中文）：

| 段 | 位置 | 耗时 |
|---|---|---:|
| matcha | CPU | 1730／4504／12211 ms（短/中/长句） |
| **vocos fp16** | **NPU** | **19.29 ms**（CPU 是 2487 ms ⇒ **129×**） |
| **HiFi-GAN v2 fp16（PRelu 版）** | **NPU** | **62.88 ms**（旧 5 算子版 182.38 ms ⇒ **2.90×**） |
| **只搬 vocos 后整链** | CPU+NPU | 相对实时 **0.716–0.777**（不改则 1.107–1.173，慢于实时） |

### 2.4 实测组合与结果（她 2026-11-04 登记）

| 组合 | 前端 | 声学 | 声码器 | 位置 | 结果 |
|---|---|---|---|---|---|
| **mel+matcha+vocos** | sherpa-mel | matcha | vocos fp16 | CPU+NPU | ✅ **成功上板（唯一跑通的神经组合）**：vocos **19.29 ms**（CPU 2487 ms ⇒ 129×）；整链相对实时 **0.716–0.777**（改前 1.107–1.173，慢于实时） |
| **mel+matcha+HiFi-GAN v2** | sherpa-mel | matcha | HiFi-GAN v2 fp16（PRelu 版） | CPU+NPU | ✅ 声码器可换：**62.88 ms**（旧 5 算子版 182.38 ms ⇒ 2.90×）；整链未跑 |
| mel+vits / mel+ekho | sherpa-mel | — | — | — | ❌ **不存在**：vits 是整链模型（自带前端+声学+声码器，不吃外部 mel），ekho 是录音拼接；ggradio 不给选 |
| piper（单体 VITS） | — | — | — | CPU | ⚠️ 跑得动但**放不下**：RTF 0.65–0.81，常驻 RSS 超板内存 |
| **ekho（现役交付）** | — | — | — | CPU | ✅ **RTF 0.49、RSS ~20 MB**——不走神经链，整链录音拼接 |

> matcha 在 CPU 上的代价（同输入四句）：**1730 / 4504 / 12211 ms**（短/中/长句）——所以"成功上板"的关键是把 **vocos 声码器搬 NPU**，见上表。

---

## 2.5 路径约定（**复跑前必读**）

仓里的脚本**保留了它们实际运行时的路径**——这是故意的，便于对证据。
换机器跑之前，改**文件顶部那几行**即可，逻辑不用动。

| 变量 | 在哪个脚本里 | 指向什么 | 当时的机器 |
|---|---|---|---|
| `BASE` / `D` | `sherpa/sherpa-mel/*.py`、`sherpa/matcha/token/*.py` | `matcha-icefall-zh-baker` 模型目录 | **校机**（`D:\yzd\ttsbench\models`） |
| `OUT` / `SD` | `sherpa/sherpa-mel/build_exact.py`、`sherpa/matcha/token/exact_ids.py` | 产出的 mel / wav 落地目录 | 校机 |
| `MODEL` | `sherpa/HiFi-GAN/cpu_reference.py` | 原版 `hifigan_v2.onnx` | 校机 |
| `PYTHONPATH` | `sherpa/sherpa-mel/build_exact.py` | 装 sherpa **2.0.1** 的独立目录 | 校机（`D:\yzd\ttsbench\sh_new`） |
| `SUB_PY` | `sherpa/sherpa-mel/build_exact.py` | 跑 helper 的**子进程解释器**（env `WCIS_TTS_PY` 可覆盖，默认用当前解释器；原硬编码借校机 ASR venv 的 python，只借不装不 import） | 校机 |
| `/mnt/s/work/WCIS` | `sherpa/vocos/build_container.sh`、`sherpa/HiFi-GAN/*/{build_container,parse,quantize_int8}.sh` | **容器里**看到的仓路径 | 本机 podman `zgbuild` |
| `/root/vocos_run` | `sherpa/HiFi-GAN/*/board_test.sh`、`sherpa/vocos/cli_exact.sh` | 板上的工作目录 | **板端** 192.168.1.113 |

**三台机的分工**（改路径前先清楚自己要在哪台跑）：

| 跑什么 | 在哪 | 备注 |
|---|---|---|
| sherpa 取 token / 出 mel / 出 CPU 参考 | **校机**（`ssh school-guest`，Win11 虚拟机） | 有 onnxruntime；sherpa 2.0.1 用 `pip --target` 装独立目录 |
| icraft 五段之前四段 | **本机 podman** `zgbuild` | 必须加 `MSYS_NO_PATHCONV=1` |
| icraft 第五段 `icraft-generate` | **本机 Windows CLI** | `C:\Icraft\CLI v3.39.0\bin\icraft-generate.exe`；容器里会报缺 `.so` |
| 上板跑 + 取数 | **板端** `root@192.168.1.113` | 禁止在板上编译 |

> 18 个脚本带校机路径、4 个带容器路径、若干带板端路径，全部是"实际跑过的那条命令"。
> 想在别处复跑：**改路径，别改逻辑**——逻辑是验证过的。

## 2.6 模型从哪来（**ONNX 不进仓**，体积太大）

仓里只有脚本与文档。跑之前要自己准备这几件，放到 §2.5 表里对应 `BASE` 目录下：

| 文件 | 大小 | 从哪来 |
|---|---:|---|
| `matcha-icefall-zh-baker/model-steps-3.onnx` ＋ `tokens.txt` ＋ `lexicon.txt` ＋ `dict/` | 75.6 MB | sherpa-onnx 官方模型发布（icefall baker-zh） |
| `vocos-22khz-univ.onnx` | 53.9 MB | sherpa-onnx 官方 vocoder 发布 |
| `hifigan_v2.onnx` | 3.75 MB | 同上（v2 变体；v1=55.75 MB、v3=5.86 MB） |
| `sherpa-onnx` **2.0.1**（Python 包） | — | `pip install --target <独立目录> --upgrade sherpa-onnx`（**1.13.8 打不出 token**） |

**编译产物也不进仓**（`*_ZG.json/.raw` 各 4–31 MB）：按各目录的 `build_container.sh` + `gen_local.bat`
五段重编即可，**约 1 分钟**（generate 段 25.4 s）。

> ⚠️ 下载模型请用官方工具／官方发布页，别用随意镜像。

## 3. 五个通用坑（每个目录都会踩到，先看这里）

### 3.1 `icraft-parse --target` 只能写 `zhuge`

写 `ZG` / `Zg` 会报 `Zg is not a valid target`，但那行异常埋在进度条后面，很容易被当成"跑过了"。

### 3.2 第五段 `icraft-generate` **必须在本机 Windows CLI**

容器里 `icraft-generate` 报 `libicraft_zhuge_codegen.so does not exist`。前四段
（parse→optimize→quantize→adapt）容器里都能跑。本机 CLI：
`C:\Icraft\CLI v3.39.0\bin\icraft-generate.exe`。

### 3.3 NPU 取数**只能走 CLI**，禁用 Python `xrt.Session.forward`

同一进程连续调 `forward` 会**首句正常、其余全零**（本轮 4 句里 3 句 `rms=0.0000`）。
根因：复用输出缓冲且未保证 4096 字节物理对齐（`Check failed: (phy_addr % 4096 == 0)`）。
正确做法：`icraft-run ... --dump_format HQB --dump_output --log_path <dir>`，**一句一次进程**。

### 3.4 板端磁盘会被 dump 撑满，症状极具欺骗性

`--dump_output` 一轮就能堆 GB 级（4 句 × 2 模型 ≈ 4 GB），根分区只有 26 GB。满了之后：

| 症状 | 真相 |
|---|---|
| python 脚本**无输出**且 `rc=0` | 写不进去 |
| `icraft-run` 报 `rc=1` **但日志为空** | 同上 |
| **scp 传上去的脚本全是 NUL 字节**（`od -c` 才看得出） | 磁盘满时 scp 建了文件写不进内容 |

**脚本"静默无输出"时第一件事查 `df -h`。** 取完数立刻删 dump。

### 3.5 比对纪律：A/B 必须同模型比同模型

`vocos-NPU` 只跟 `vocos-CPU` 比，`hifigan-NPU` 只跟 `hifigan-CPU` 比。
**跨声码器比波形毫无意义**（相位互不相干，结果≈0 甚至为负）。
真正的精度差是"正相关但偏低"（如 vocos int8 的 0.186）；**负值是两个不相干信号的随机波动**。

---

## 4. 量化口径：int8 死、fp16 活

| 模型 | int8 | fp16 |
|---|---|---|
| vocos | ❌ 波形相关 0.12（相位全毁） | ✅ 0.969–0.978 |
| HiFi-GAN v2 | ❌ 0.57 | ✅ 0.998–0.999 |

**vocos int8 的根因**（A 级，**别简写成"角度量化太粗"**）：

1. 诸葛的 `CosNode`/`SinNode` 打印 `ZhugeQuantParams Not Implemented! Using Default Imeplementation!`，
   角度张量的 scale 被写成常数 `0.49766457`（声明 `0.45043343`，差 **10.4%**），
   板上角度输出只剩 **256 级**（±63.69、步长 0.4975）。
2. **更致命的是 `Cos` 的输出与输入角度脱钩**：板端 cos 输出对"真实角度→圆周 256 步"的命中率只有
   **0.4%–4.9%**（≈1/256 随机水平），同一 int8 code 分箱内 x 有 **36 个不同值**
   ⇒ **板端的 `Cos` 根本不是角度的函数**。

> **实测否证了"角度溢出/太粗"这个解释**：按板端规格在**真实角度**上模拟 int8
> （`round(a/0.45043343)` 后 clip ±127），`cos/sin` 仍有 **0.9645 / 0.9711**；
> 折到 [-π,π] 或允许环绕更是 **0.9916**。⇒ **准确说法是「`Cos` 算错了」。**

**`--mix_precision` 救不了**（auto 0.885→0.898，cos/sin 未动；自定义 CSV 灌 Cos/Sin fp16 反而掉到 0.12）。
**把三角函数搬出图外也救不了**（角度作第 4 个 fp32 输出，板上仍是 256 级、`cos` 相关 0.1060）。
**唯一解是整模型 fp16**：不用校准集，代价是体积翻倍、硬件段 2.02×。

---

## 5. 复跑一条完整链路

```bash
# 0) 容器（已现成，勿重建）
podman machine start && podman start zgbuild
MSYS_NO_PATHCONV=1 podman exec zgbuild bash -c '<命令>'   # ← 不加 MSYS_NO_PATHCONV，/mnt/s 会被 Git Bash 改写

# 1) 取 token（校机，sherpa 2.0.1 装到独立目录，不动现役 ASR venv）
python sherpa/sherpa-mel/build_exact.py

# 2) 五段编译（以 HiFi-GAN 为例）
bash sherpa/HiFi-GAN/hifigan-prelu/build_container.sh   # 1-4 段，容器内
cmd //c sherpa\HiFi-GAN\hifigan-prelu\gen_local.bat     # 第 5 段，本机 Windows

# 3) 上板 + 取数
bash sherpa/HiFi-GAN/hifigan-prelu/board_test.sh        # 板上执行

# 4) 判据
#    数值等价：改写后 vs 原版 onnx（CPU）→ 必须 corr=1.000000 / max|d|=0
#    上板保真：NPU vs 同模型 CPU → 必须 ≥0.95（实测 0.998–0.999）
#    性能：icraft-run --log_time 的 hard 时间
```

---

## 6. 目录索引

```
tts4fpai/                          （2026-11-04 重组后）
├── README.md                      ← 本页（层级 + 模型清单 + 通用坑 + 量化口径）
├── para.md                        ← 全模型可调／可选参数速查（默认值＋本仓裁定＋出处）
├── bench-cpu/                     ← 板端 CPU-only 基准
├── ekho-linux/                    ← ekho 引擎源码仓（现役，fork 自 hgneng/ekho；含 ekho-server/流式/桥）
├── models/edge/                   ← Edge 在线中文 TTS（edge-tts + miniaudio 解 mp3；无需 ffmpeg）
└── sherpa/                        ← 神经 TTS 全家（sherpa-onnx 系）
    ├── sherpa-mel/                ← 前端 / mel 来源
    │   ├── README.md
    │   └── SHERPA_FRONTEND_SOURCE.md   ← 前端源码正本（读 C++ 得来）
    ├── matcha/                    ← 声学模型（CPU）
    │   ├── README.md
    │   └── token/                 ← token 流调查（帧数对不上时看这里）
    ├── vocos/                     ← 声码器（NPU，频域）
    │   ├── README.md
    │   ├── int8/                  ← ❌ 不可用
    │   └── fp16/                  ← ✅ 当前可用
    ├── HiFi-GAN/                  ← 声码器（NPU，时域）
    │   ├── README.md
    │   ├── cpu_reference.py       ← 出 CPU 参考（必须用原版 onnx）
    │   ├── conv1d_to_2d.py        ← Conv1d→Conv2d（两版共用）
    │   ├── rewrite-5op/           ← 旧改写（LeakyRelu→5 算子）182.38 ms
    │   └── hifigan-prelu/         ← ★ 新改写（PRelu）62.88 ms
    └── VITS/                      ← 单体 VITS 失败候选留档
        ├── piper/                 ← 板上跑得动但放不下（OOM）
        └── vits-others/           ← icraft 编译全失败
```

---

## 7. 相关

- 文档正文：`wiki/PS/Backend/MatchaVocosNPU.md`
- 候选横评：`wiki/PL/MOD/modellist/TTS同量级候选进NPU横评.md`
- CPU 读数：`wiki/PS/Backend/mods/audio/ARM CPU TTS性能测试.md`
- 决策：`wiki/archive/开发管理/状态源与决策记录.md`（`D-1793547016`、`D-1793547866`、`D-1793588347`）
- 算子面：`wiki/PL/MOD/modellist/iCraft算子支持矩阵.md`
- ekho 代码：`repos/HA4FPAI/lang/tts/ekho-linux`