# lang/tool —— 语音链路的通用工具

这里放的是 `lang/asr/` 链路**运行时用不到、但值得留着**的工具（2026-11-04 她裁「用不到的移到
lang/tool」）。链路本体（`asr_chain.py`、`asr/backends.py`、`asr/remote_asr.py`、
`asr/tts_*.py`、`asr/start_*.sh`、`asr/run_*.sh`）仍留在
`lang/asr/`，**不在这里**。

## 三条使用前提（先看再动手）

1. **不被链路引用**：这些文件不被 `asr_chain.py` 或任何起链脚本 `import`／调用。链路删掉它们照样跑。
2. **原注释里的 `/root/funasr/...` 是板端路径**：这些脚本都是**照着 113 板写的**，源注释里的绝对路径
   保留原样（当板端路径的说明书）。在 PC 上跑要先按本 README 改路径。
3. **2026-11-04 的 funasr 删除波及了两件**：`run_testset.sh` 与 `run_env.sh` 的默认档引用了已删的
   funasr 后端／探针，**用前必改**（各自小节里列了改哪几行）。其余六件不受影响。

## 速查表

| 文件 | 一句话 | 跑在哪 | 现在能用吗 |
|---|---|---|---|
| `cap.sh` | 内存上限 + 心跳的进程运行器（RLIMIT_AS） | 板端 | ✅ 能，拿来即用 |
| `hb.sh` | 心跳日志器，给硬挂死留现场 | 板端 | ✅ 能，拿来即用 |
| `load_probe.py` | RAM 门禁探针：装一个后端、跑一段音频、报峰值 | 板端 | ⚠️ 要改 1 行路径 |
| `mock_asr_server.py` | 假远端 ASR，验链路口子 | 板/PC 都能 | ✅ 能，纯标准库 |
| `ms_get.py` | ModelScope 文件下载器（断点续传＋体积校验） | 板/PC 都能 | ✅ 能，纯标准库 |
| `ms_sizes.py` | ModelScope 仓库文件盘点（只列不下） | 板/PC 都能 | ✅ 能，纯标准库 |
| `run_testset.sh` | 测试集批跑：一个 wav 一个 wav 仿实时过链，一条一行读数 | 板端 | ❌ 要改 4 处 |
| `run_env.sh` | 逐档环境探针批跑：一档一进程，量装载峰值 | 板端 | ❌ 探针已删，只剩框架 |

---

## cap.sh —— 带内存上限 + 心跳的运行器

**干什么**：给任意命令套两层保护——`RLIMIT_AS` 虚拟内存硬上限（超了分配失败、可捕获，而不是整机
卡死）＋ 可选的心跳日志。

**为什么用 RLIMIT_AS 而不是 cgroup**（2026-10-12 实测）：**这块板子的内核没编译任何 cgroup 控制器**：

```
/proc/cgroups                              只有表头，零行
/sys/fs/cgroup/unified/cgroup.controllers   空
/sys/fs/cgroup/memory                      不存在（v1 也没有）
```

所以 `systemd-run -p MemoryMax=100M` 会被"接受"但**完全不生效**——实测在 100M 限额的 scope 里
成功分配了 400 MB，systemd 245 不报错、静默忽略。没有 cgroup 时，`prlimit --as` 是唯一能把
「无限增长 → 整机卡死」换成「分配失败 → 可捕获异常」的手段。

**两个实测踩过的坑**：

| 坑 | 现象 | 本脚本怎么绕 |
|---|---|---|
| util-linux 2.34 的 `prlimit --as=200M`（带后缀）**直接 segfault** | 纯字节数 `--as=209715200` 正常 | 后缀由脚本自己换算，只给 prlimit 传字节 |
| `RLIMIT_AS` 卡的是**虚拟地址空间**，不是 RSS | 刚 import 完 onnxruntime：`VmSize=207 MB` / `VmRSS=38 MB`（约 5 倍） | `--as` 必须按 **VmSize 峰值**定；第一次用带 `--as 2G` 跑 sizing，从心跳日志读 `tgt_vsz` 峰值再收紧 |

**用法**：

```bash
bash cap.sh --as 700M --tag qwen2 -- python3 foo.py arg
bash cap.sh --tag sweep --interval 10 -- bash -c '...'
bash cap.sh --as 700M --tag run --no-hb -- ./bench.sh
setsid nohup bash cap.sh ... &          # 后台跑，ssh 断开也活着
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--as` | 不设＝不限 | 虚拟内存上限，收「整数＋后缀」（`700M`／`2G`），**不收小数**（`1.5G` 会解析失败 exit 2） |
| `--tag` | 命令名派生 | 心跳日志名，落 `/root/hb/<tag>.log` |
| `--interval` | 5 | 心跳间隔秒 |
| `--no-hb` | 关 | 只限内存、不要心跳 |
| `--` | — | 之后全是待执行命令 |

---

## hb.sh —— 心跳日志器（给「硬挂死」留现场）

**为什么需要它**（2026-10-11 实测逼出来的）：板子整机挂死时，journald 那些还压在内存缓冲里的日志
**一行都没落盘**，事后 `/var/log/journal` 里找不到那次启动的任何记录，无法定位卡在哪。心跳改成
**直接写 SD 卡**、每行自带可判读的现场量，挂死后重新上电读「最后一行」就知道当时是什么状态。

**每行字段**（`key=value`，便于 grep/awk）：

```
ts=<墙钟> mono=<开机秒> load=<load1> avail=<MemAvailable kB> cma=<CmaFree kB>
dstate=<D 状态进程数> top=<rss kB>/<comm> [tgt=<状态> rss=<kB> vsz=<kB> wchan=<..>]
```

判读：**`dstate` 突增＝卡在内核不可中断等待**（多半是 I/O）；`top` 是谁吃了内存；`tgt` 是被监视
进程自己的 rss／vsz／wchan。

**用法**：

```bash
bash hb.sh start <tag> [间隔秒] [pid]    # 后台起，日志 /root/hb/<tag>.log
bash hb.sh stop  <tag>
bash hb.sh status
bash hb.sh tail  <tag> [行数]
```

**落盘说明**：每行是独立的小 write（`>>` 追加），内核 `dirty_expire` 默认 30 s 就刷到卡；硬挂死时
未刷的那几行可能丢，但只要挂死超过 30 s，之前的行都在盘上。可用 `HB_LOGDIR`／`HB_INTERVAL` 覆盖默认。

---

## load_probe.py —— RAM 门禁探针

**干什么**：装载**一个** ASR 后端＋跑一段真音频推理，报这个进程的峰值内存。用来回答「这个档搬上板
会不会把现役链挤死」。

**判据**：她定的是「RAM 占用超过 0.5 G 就不试了」⇒ 拿本进程峰值 **VmHWM**（≈`ru_maxrss`）与
**512 MiB** 比。VmHWM 是「进程自己曾占用的物理内存峰值」，比瞬时 VmRSS 严格（不会因为采样时机好而
漏判）。附带报系统 MemAvailable 前后差＝这块 993 MB 板子上装载后还剩多少余量。

**用法**（板端原写法）：

```bash
/root/funasr/venv/bin/python /root/funasr/asr/load_probe.py \
    --backend funasr-paraformer --model-dir /root/funasr/models/Paraformer-large \
    --wav /root/funasr/testdata/a067.wav --seconds 20
```

| 参数 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `--backend` | ✅ | — | 后端名，走 `asr/backends.py` 的 `build()` |
| `--model-dir` | ✅ | — | 权重目录（`remote-api` 档填 endpoint） |
| `--wav` | ✅ | — | 真音频，**必须给**（不给就不算测过） |
| `--seconds` | | 20 | 截取前多少秒 |
| `--threads` | | 4 | ORT intra-op 线程 |
| `--jsonl` | | 空 | 追加一行 JSON 读数到此文件 |

**⚠️ 移进 tool/ 后的唯一改动点**：第 77 行 `sys.path.insert(0, "/root/funasr/asr")` 是**硬编码板端
路径**。板上照旧能跑；**在 PC 上跑要改成** `lang/asr/asr` 的绝对路径，或改用
`PYTHONPATH=<…>/lang/asr/asr python load_probe.py …`。

**⚠️ 后端名要跟上现状**：`--backend` 现在只认 `slot`／`remote-api`／`sensevoice-sherpa`
（四个 funasr 档已删）；`funasr-paraformer` 这种写法会被 `build()` 以「未知后端」拒掉。

---

## mock_asr_server.py —— 假远端 ASR（验口子）

**干什么**：只为了证明「口子」通，**不做识别**。链路要验证的是三件事：段音频能不能按协议送出去、
往返耗时进不进 `asr_ms`、失败会不会把链带崩——这三件都不需要真模型。所以这里起一个假服务：收
multipart，量出音频时长，回一句带时长的话。

**历史设计**：回文本刻意做成「整句无标点」，好让本地 CT_Transformer 有活干——标点档是否真的串在
ASR 之后，看 jsonl 里 `text` 与 `text_raw` 的差别就能一眼确认。**标点档已随 funasr 删除**，所以这个
用途现在没对象了，但「无标点回包」对观察链路仍然直观。

**用法**（系统 python3 即可，不用 venv）：

```bash
python3 mock_asr_server.py --port 18095 --latency 0.15
curl -s -F "audio=@.../a067.wav" http://127.0.0.1:18095/api/v1/asr
```

端点：`POST /api/v1/asr`（funasr 协议）、`POST /v1/audio/transcriptions`（openai 协议）、`GET /health`。
`--latency` 用来模拟远端网络耗时，配合链路量 `asr_ms` 与 `lag`／`backlog` 的响应。

接链路：

```bash
ASR_REMOTE_URL=http://127.0.0.1:18095/api/v1/asr /funasr-lite
```

---

## ms_get.py —— ModelScope 文件下载器

**干什么**：走 ModelScope 的 resolve 接口下仓库文件，**纯标准库**（不依赖 modelscope 包），带
**断点续传＋体积校验**。板端实测 `www.modelscope.cn:443` 可达、HuggingFace 不可达，所以这套是板上
拿权重的实际通道。

地址形态：`https://www.modelscope.cn/models/<repo>/resolve/master/<path>`

```bash
python3 ms_get.py <仓库> <目标目录> <文件名> [<文件名> ...]

python3 ms_get.py iic/SenseVoiceSmall-onnx /root/funasr/models/SenseVoiceSmall \
    model_quant.onnx config.yaml am.mvn tokens.json
```

**行为**：目标文件体积已与仓库登记一致就**跳过**（所以重跑很便宜）；体积不对则删掉重下；中断后重跑
即续传。

**注**：之前它是配 `board-fetch-models*.sh` 那两支「funasr 取模型脚本」用的，那两支已删；本件本身是
**通用下载器**，下任何 ModelScope 仓库都行，所以留下。

---

## ms_sizes.py —— ModelScope 仓库盘点（只列不下）

**干什么**：在真正下载之前，先问「这个仓库里到底有哪些文件、多大」。只依赖标准库，走
`www.modelscope.cn` 的仓库文件接口。

```bash
python3 ms_sizes.py                # 跑内置候选清单
python3 ms_sizes.py --min 1        # 只列 >=1 MB 的文件
python3 ms_sizes.py iic/SenseVoiceSmall-onnx ...   # 只查指定仓库
```

**判读**：命中（HTTP 200）就把文件逐个列出来；**没命中只记 HTTP 码——404 也是结论**（说明那个仓库
名不存在，别再试了）。内置清单混着 FunASR 官方仓（含 `-onnx` 兄弟仓）与 sherpa-onnx 镜像仓
（`csukuangfj/sherpa-onnx-*`），后者仍然有用：`sensevoice-sherpa` 后端吃的就是那批权重。

---

## run_testset.sh —— 测试集批跑（⚠️ 用前必改 4 处）

**干什么**：板端测试集跑法——`cases.tsv` 逐条**仿实时**过一遍完整链路，一条一行读数。当年用来出
「Paraformer vs SenseVoice」「VAD 静音参数扫档」这些对比数据的就是它。

**流程**：

```
cases.tsv（名字/时长/字节/md5/来源） + 16k/<name>.wav
  ↓ 逐条：
  gate（load1<1.2 且 MemAvailable>AVAIL_MIN 才放行，否则每 20 s 等）
  ↓
  cap.sh --as 1536M --tag <tag> --interval <HBINT> -- <python> asr_chain.py
        --source wav --input 16k/<name>.wav --pace realtime --segmenter $SEG --backend $BE
  ↓
  每件一个 .jsonl ＋ 一个 .state
  ↓
  内嵌 python 汇总一行：段数/asred/段长中位/最长/撞20s上限/rtf_mean/lag_max/backlog_s/
                        空文本/错误/hwm/asr_ms min-p50-p95-max/queue_max/vmswap/majflt/
                        swap_used_max/cma_min/avail_min
```

**旋钮**：

| 变量 | 默认 | 说明 |
|---|---|---|
| `SEG` | `fvad` | 分段器（`fvad`／`rms`）**← 已删，见下** |
| `BE` | `funasr-paraformer` | 后端（`funasr-paraformer`／`funasr-sensevoice`）**← 已删** |
| `VAD_END` | 不传 | 句尾静音毫秒（「长尾的唯一旋钮」；想复现旧形状写 800）**← 参数已删** |
| `AS` | `1536M` | `cap.sh --as`；`AS=off` 才裸跑。**只收整数＋后缀**，`1.5G` 会 exit 2 |
| `AVAIL_MIN` | `300000` | gate 的 MemAvailable 下限（kB） |
| `HBINT` | `10` | 心跳间隔 |
| `TS` / `TS_DIR` / `OUT` | `/root/funasr/testset/cases.tsv` 等 | 清单／音频／输出目录 |
| `RUN_TAG` | 时间戳+SEG+BE | 本次批次名，也是输出文件前缀 |

**⚠️ 现在跑不起来，四处都要改**（都是 funasr 删除的连带）：

| # | 行 | 现在 | 要改成 |
|---|---|---|---|
| 1 | `V=${VENV_PY:-/root/funasr/venv/bin/python}` | venv 已不存在 | `PY=${PY:-/usr/bin/python3}` |
| 2 | `SEG=${SEG:-fvad}` | `fvad` 已删，asr_chain.py 会拒 | `SEG=${SEG:-webrtc}` |
| 3 | `BE=${BE:-funasr-paraformer}` | 该后端已删 | `BE=${BE:-remote-api}`（或 `sensevoice-sherpa`） |
| 4 | `VARG+=(--vad-max-end-sil "$VAD_END")` | 该参数已删 | 换 `--sil-stop-ms`（WebRTC／RMS 档的静音门限） |

**另外**：它依赖的 `cases.tsv` 与 `testset/16k/` **盘上没有**（全 `lang/` 搜过，零命中）——那本来
就在板端。上板重跑前先确认这两个东西还在板上：`/root/funasr/testset/cases.tsv`。

---

## run_env.sh —— 逐档环境探针批跑（⚠️ 探针已删，只剩框架）

**它是什么框架**：一个**纯 bash 的编排器**，没有第三方框架——「**一档一进程**」批量测各后端的装载
内存。

**为什么一档一进程**：`VmHWM` 是**进程级**峰值，同进程连测会把后面档的读数抬高，0.5 G 门禁就没法
逐档判——所以每档 fork 一个干净进程测。

**流程**：

```
bash run_env.sh [档名...]                 # 不给就跑内置 8 档
  ↓ 逐档：
  gate（load1<1.2 且 MemAvailable>AVAIL_MIN(默认 300000 kB) 才放行，否则每 20 s 等）
  ↓
  $V env_probe.py <档名> [--allow-big] [--wav X --seconds N]
  ↓
  每档一行 JSON 追加进 /root/funasr/out/env_probe.jsonl，同时 tee 进 logs/env_probe.log
  ↓
  打印 start/done 时间戳
```

**两个设计点仍值得复用**：

| 点 | 为什么 |
|---|---|
| **gate 闸门** | 和 `run_testset.sh` 同款口径（load1<1.2、MemAvailable 下限）：SBC 上跑测量**必须**先等机器闲，否则读数没有可比性 |
| **`PROBE_WAV`／`PROBE_SECS`** | 给了真 wav 就喂真音频——**VAD 档「装载成功」不算数，要真出段才算环境到位**。这个判据对未来的 sherpa-onnx VAD 档同样成立 |

**环境变量**：`AVAIL_MIN`（默认 300000 kB）、`BIG=1`（允许碰 830 MB 的 contextual 档）、
`PROBE_WAV`／`PROBE_SECS`（默认 20 s）。

**⚠️ 现状**：它调用的探针 `asr/env_probe.py` **已随 funasr 删除**（那 8 档全是 funasr_onnx 模型：
`paraformer`／`paraformer-online`／`sensevoice`／`vad-offline`／`vad-online`／`punc`／`punc-rt`／
`seaco`）。所以现在直接跑会报找不到 `$P`。**留下它的理由**：这套「逐档 + gate + 一行一档」的框架
可以原样套给 sherpa-onnx 的档（`silero`／`ten`／`sensevoice-sherpa`），只要换一支探针。真要用时把
`KEYS` 默认清单和 `$P` 改成新探针即可。

---

## 怎么把这批工具接回链路（真要复用的入口）

| 你想干什么 | 用哪件 | 接到哪 |
|---|---|---|
| 验证链路「送出去／收回来／失败不崩」 | `mock_asr_server.py` + `asr/t_bridge.py` | `ASR_REMOTE_URL=http://127.0.0.1:18095/api/v1/asr` |
| 试一个新后端会不会把板挤死 | `load_probe.py` | 走 `asr/backends.py` 的 `build()` |
| 批量对比两个分段器／两个后端 | `run_testset.sh`（先改 4 处） | `SEG=`／`BE=` |
| 逐档量装载峰值 | `run_env.sh`（先换探针） | `KEYS=`／`$P` |
| 跑长任务又想防整机卡死 | `cap.sh` | 包在链路命令前面 |
| 怕硬挂死丢现场 | `hb.sh`（`cap.sh` 默认已带） | `hb.sh start <tag> <间隔> <pid>` |
| 下一个新权重 | 先 `ms_sizes.py` 看有哪几件多大 → 再 `ms_get.py` | — |

约定：这里的东西**不承诺紧跟链路接口**。链路改了参数名／删了后端，这里可能滞后——用前扫一眼
`asr_chain.py --help` 和本 README 的「用前必改」段落。
