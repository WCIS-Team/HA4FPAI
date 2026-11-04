# Qwen2-0.5B（厂商 ZG330 demo ＋ 我方上板改动）

厂商 ModelScope demo `AIBS/Qwen2_0.5B_zg3.36.1_axi_demo` 的完整工作树，加上我方为把它跑在悟净 FMQL30TAI676H（ZG330）上所改的 6 行代码、编译与上板取证件。

本目录 2026-11-04 从 `S:\apps\llm\agentwork\qwen\` 还原而来。原来那份工作树一直没进库，只有 `*.raw` 等大件按体积类留在 `agentwork`。

## 0. 边界：这里是什么、不是什么

| | |
|---|---|
| **是** | 厂商 demo 的可读、可重编、可重推副本：ONNX 导出件、编译配方、我方驱动、差异、取证日志 |
| **不是** | 板上现役。**2026-10-28 已按她"把板子上的 Qwen2 删了，节约空间"从 `/root/qwen2` 出库**：删掉 `jr_path/`（1.3 G）、`config/`（6.8 M）与 472 MB 的失控日志后，`/root/qwen2` 从 1.7 G 降到 496 K（台账 §廿一 末）。板上只留驱动、`modelzoo_utils/`、bisect/mem_watch 与日志 |
| **不是** | 能直接 `pip install` 的完整模型包。**厂商只给了 LFS 指针**：`vendor/config/Qwen2-0___5B-Instruct/model.safetensors` 是 134 B 的指针（`size 988,097,824`），真权重从未拉到本机（`agentwork/qwen/lfs_pull.log` 0 字节）。推理走的是 `fmodel/` 的 ONNX＋`.onnx.data`，不需要 safetensors |

## 1. 来源与时间线

| 时间（北京） | 事 | 证据 |
|---|---|---|
| 2026-10-11 19:17–19:54 | 本机 clone 厂商 demo（`HEAD 664a4c4`），`model.onnx.data` 走 LFS 拉下来 1.27 GB | `vendor/` 的 mtime；`agentwork/qwen/tree.json`、`meta.json` |
| 10-11 19:54:44–20:04 | 本机 Windows `C:\Icraft\CLI v3.39.0` 跑五段 `icraft compile`（parse→optimize→quantize→adapt→generate），零 fatal／零 error，产出落 `vendor/../jr_path/decode/`（**未随副本落库**，见 §6） | `ref/compile.out`、`ref/compile.err`、`agentwork/qwen/logs/compile.pid` |
| 10-11 20:09–20:17 | 板上装 whl（`icraft` 3.33.1→3.39.0、`tokenizers` 0.13.3）＋ SFTP 推 1.3 GB 到 `/root/qwen2` | `ref/deploy.log:11-25`、`:44-64`、`:118-120` |
| 10-11 20:17 | 第一次点火整机挂死（这块板没有 `/dev/watchdog`），journald 一行没落盘 | 台账 §十四 |
| 10-12 | 写 `qwen_bisect.py/.sh` 把"起跑即死"拆成 4 步单进程 | 台账 #182 |
| 10-14 20:33–21:57 | 第五轮点火**跑通**：20 token／7.08 s／2.83 tok/s／247.71 ms per token；CMA 谷底 8,860 kB | 台账 #197 |
| 10-28 | 板上出库 | 台账 §廿一 末 |

厂商自述适配 **iCraft 3.36.1**（`vendor/README.md:56,62,66,79`）；我方实际用的是 host CLI **3.39.0** 与板上 whl **3.39.0**。这是本目录里最重要的一条口径差：**图上 `_ZG` 件由 3.39.0 生成，不是厂商那份 3.36.1 的产物**。

## 2. 目录布局

| 目录 | 件数 | 字节 | 是什么 |
|---|---|---|---|
| `vendor/` | 76 | 1,295,649,575 | 厂商工作树原样（除 `.git`／`jr_path`／`__pycache__`）。含 `fmodel/qwen2_exported_model_cuda/`（`model.onnx` 145,342 ＋ `model.onnx.data` 1,266,188,288）、`config/Qwen2-0___5B-Instruct/`、`modelzoo_utils/`、原版驱动 `qwen2_infer_decode_only_axi.py`（23,547／`501fe406…`）、`Qwen2-0.5B_decoder.toml`、`README.md`、`image.png` |
| `board/` | 9 | 9,266,494 | 真上板那一套：`_ZG.json`、两支驱动、toml、分词器＋config、`qwen_bisect.*`、`mem_watch.sh`（逐件 md5 见 §4） |
| `patch/` | 6 | 2,277 | 我方覆盖厂商的两件 `modelzoo_utils` ＋ 4 份 unified diff（见 §3） |
| `ref/` | 4 | 8,157,997 | `deploy.log`（14,470）、`compile.out`（690,341）、`compile.err`（1,567）、`whl/tokenizers-0.13.3-…aarch64.whl`（7,451,619） |
| `README.md` | 1 | 11,933 | 本文件 |
| 合计 | 96 | 1,313,088,276 | 见 `MANIFEST.md5` |

`vendor/.icraft/`（1.6 MB）与 `vendor/hardopLog/Qwen2-0.5B-decoder/access_byte.log`（303 B）是**我方 10-11 那次编译在厂商工作树里落下的 CLI 日志**，为保持工作树原样一起带过来，不是厂商交付物。证据：厂商 clone 里 `git status --short` 恰好只列 `.icraft/`、`hardopLog/`、`jr_path/` 这三个未跟踪目录（都是 10-11 那次编译新建的），跟踪面里没有它们。

## 3. 与厂商 demo 的全部代码差异（3 处、6 行）

| # | 文件 | 改动 | 为什么 |
|---|---|---|---|
| 1 | `board/qwen2_infer_decode_only_axi.py`（#162） | 删 `from pyrtutils.calctime_utils import *` 一行 | `calctime_detail` 在脚本里只出现在注释（`:373`／`:423`）＝死 import。不删就得在板上装 pandas；板只有 993 MB，`matplotlib` 首次 import 还要建字体缓存 |
| 2 | `board/qwen2_infer_decode_only_axi_pad3.py`（#196） | 改 2 行：`token_dynamic_fm` 从 2 个值补成 3 个值（`:364` prompt 循环、`:416` decode 步骤） | **ZG 件把 `token_dynamic_fm` 声明成 fp32[1,3]＝12 B，厂商驱动只喂 8 B**，板上真跑撞 `hardop.cpp:46 host_chunk byte_size:8 is diff from plddr_chunk byte_size:12`；本机 Host 后端不校验输入宽度，所以 PC 上永远看不见。第 3 个元素图里不读（`[1,1,1]` 与 `[1,1,0]` 的 logits 逐位相同） |
| 3 | `patch/modelzoo_utils/pyrtutils/__init__.py`＋`utils/tools.py` | 删 1＋2 行 import（`.calctime_utils`／`pandas`／`matplotlib.pyplot`） | 同 #1 的因：这三样在该文件里没被使用（`tools.py` 只有 `is_arm()`／`tname()`） |

行数算式：1（删）＋2（改）＋1＋2（删）＝**6 行**，跨 3 个文件。逐行 diff 在 `patch/`：

- `patch/qwen2_infer_162.diff`（vendor → #162，1 个 hunk）
- `patch/qwen2_infer_pad3_196.diff`（#162 → #196，2 个 hunk）
- `patch/modelzoo_pyrtutils_init.diff`、`patch/modelzoo_utils_tools.diff`（各 1 个 hunk）

## 4. 上板件对照（台账 `wiki/PS/板端我方代码与产物台账.md` §十四）

| 库内路径 | 板上路径 | 字节 | md5 | 台账 |
|---|---|---|---|---|
| `board/Qwen2-0.5B-decoder_ZG.json` | `/root/qwen2/jr_path/decode/`（10-28 已删） | 2,183,817 | `24bf0b6b3d5990056fc66229a88f1020` | #160 |
| （未落库，见 §6）`Qwen2-0.5B-decoder_ZG.raw` | 同上（10-28 已删） | 1,314,012,420 | `2b107ec676de2b4dc35b772402b25b92` | #161 |
| `board/qwen2_infer_decode_only_axi.py` | `qwen2_infer_decode_only_axi.py` | 23,508 | `4a11305537edfcaefd801dfbe41626c3` | #162 |
| `patch/modelzoo_utils/pyrtutils/`（2 件）＋ `vendor/modelzoo_utils/`（其余 12 件逐字节同厂商） | `modelzoo_utils/` | `__init__.py` 97／`utils/tools.py` 365 | `c6d5ad12…`／`77540af7…` | #163 |
| `board/tokenizer.json`＋`board/config.json` | `config/Qwen2-0___5B-Instruct/`（10-28 已删） | 7,028,015／659 | `1c74fd33…`／`ac22e242…` | #164 |
| `board/Qwen2-0.5B_decoder.toml` | `Qwen2-0.5B_decoder.toml` | 1,060 | `7e7bdf8d4a4acfc519373e1233b08ad8` | #165 |
| `board/qwen_bisect.py`＋`.sh` | `qwen_bisect.py`＋`.sh` | 3,695／1,399 | `4f9c70af…`／`fce7edf9…` | #182 |
| `board/mem_watch.sh` | `mem_watch.sh` | 792 | `f84ffd9539d9828ba3cd87de50c62948` | #183 |
| `board/qwen2_infer_decode_only_axi_pad3.py` | **板上叫 `qwen2_infer_pad3.py`** | 23,549 | `597eeb3b164311f0882ff32e814f2b26` | #196 |

- 前四列的上板侧 md5 全部能在 `ref/deploy.log` 里逐条对上（`:46`／`:47`／`:118`／`:119-120`／`:48`／`:49`／`:45`）。
- `#162` 与 `#196` 是**两支驱动并存**：`#162`＝厂商原版减一行 import（能加载但会在板上撞 12 B 报错），`#196`＝真正跑通的那支。
- 台账 #163 写"13 支 .py"，而 `ref/deploy.log:50-64` 实际推了 **14 支 .py ＋ 1 支 .md**（多出 `calctime_utils.py`）——件数口径以 `deploy.log` 为准，台账那格待改。

## 5. 怎么重跑

**(a) 重编**（只能本机 Windows CLI；**绝不在 113 板上编译**，993 MB 内存会被挤死）

```powershell
cd S:\work\WCIS\repos\HA4FPAI\lang\llm\Qwen2\vendor
icraft compile Qwen2-0.5B_decoder.toml        # 五段，10-11 那次 9 分 20 秒
```

产出落 `vendor/jr_path/decode/`。判据：`Qwen2-0.5B-decoder_ZG.json` md5 ＝ `24bf0b6b…`、`_ZG.raw` ＝ `2b107ec6…`（同输入同 CLI 版本应复现；跨版本不保证）。`Qwen2-0.5B_decoder.toml` 里 `max_prompt_length=32`、`max_tokens_length=512`、`target='zhuge'`、`qdtype='fp16'`。

**(b) 上板**（10-28 之后板上没有了，要再跑得先推回 1.3 GB，≈3.6 min @6 MB/s）

推 `board/` 全套 ＋ §6 的 `_ZG.raw`，落到 `/root/qwen2/`，目录形状按 §4 的表。

**(c) 跑**

```bash
cd /root/qwen2 && printf '%s\n' '你是谁' '' | python3 qwen2_infer_pad3.py
```

末尾空行是退出通道（`main()` 循环读 stdin，EOF 会刷 `EOFError` 刷屏——10-14 那次 472 MB 日志就是这么来的）。`modelzoo_utils/` 是相对路径引入，**必须在 `/root/qwen2` 里跑**。

判据（台账 #197，10-14 实测）：`First generated token: 104198`、共 20 token、`Total time: 7.08 s`、2.83 tok/s、247.71 ms/token、CMA 谷底 8,860 kB。

**(d) 起跑前硬门禁**

- 位流含 NPU：`live.log` 里出现 `Device Name: zg330aiu`（NPU 是 PS→PL 唯一 AXI 端点，位流没它时一摸 AXI 窗口就整机楔死）
- 板子没被别人占：`ssh deb-h61 'cat /home/user/Desktop/113.txt'` 要是 `off`
- CMA 单一池 256 MiB：`udma` 开机静态占 128 MiB，只剩约 128 MiB；**不能与视频链同窗**。空载 CMA 已见底也能起（11-03 实测 `CmaFree` 3,968 kB 起跑照通），但先跑短探针

## 6. 未随副本落库的件（都在本机 `S:\apps\llm\agentwork\qwen\repo\`）

| 件 | 字节 | md5 | 不落库的理由 |
|---|---|---|---|
| `jr_path/decode/Qwen2-0.5B-decoder_ZG.raw` | 1,314,012,420 | `2b107ec676de2b4dc35b772402b25b92` | `repos/.gitignore` 的 `*.raw`（D-62 体积类）＋能按 §5(a) 重编 |
| `…_parsed.raw`／`…_optimized.raw`／`…_quantized.raw`／`…_adapted.raw`／`…-prefill_parsed.raw` | 1,263,614,652／1,263,639,180／1,263,639,180／1,263,639,180／1,260,542,652 | 未算 | 同上；四段中间体只在重编时才有用 |
| 上述 6 颗 `.raw` 合计 | **7,629,087,264（7.11 GiB）** | — | — |
| 5 颗阶段 `.json`（parsed／optimized／quantized／adapted／prefill_parsed） | 1,036,616／896,544／902,954／1,927,968／1,040,259＝**5,804,341** | 未算 | 只有终态 `_ZG.json` 是要部署的图（已在 `board/`）；中间段由 toml 重编得到 |
| `vendor/.git`（厂商 clone 元数据，1.2 G） | — | — | 从 ModelScope 重新 clone 即可 |
| `model.safetensors` 真权重 | 988,097,824（指针里写的） | `130282af0dfa9fe5840737cc49a0d339d06075f83c5a315c3372c9a0740d0b96`（LFS oid） | 从未拉下来；推理用不到 |

`jr_path/decode/` 全目录 = 6 颗 `.raw`（7,629,087,264）＋ 6 颗 `.json`（7,988,158）＝ 7,637,075,422 B。

## 7. 这份副本怎么核

```bash
cd S:/work/WCIS/repos/HA4FPAI/lang/llm/Qwen2
md5sum -c MANIFEST.md5     # 或 git bash 里 md5sum，96 件全 OK
```

2026-11-04 还原时的实测：96 件 / 1,313,088,276 B。其中 91 件是从源复制过来的，逐件 src↔dst md5 **91/91 全等**；另 5 件是我方新写／新生成的（`README.md` ＋ `patch/` 4 份 diff），无源可比。

入库口径：可跟踪 92 件 / **39,303,098 B（37.48 MiB）**，最大单件 11,418,266 B（`vendor/fmodel/…/tokenizer.json`），远低于 GitHub 100 MiB 硬限。被 `repos/.gitignore` 挡回的 4 件合计 1,273,785,383 B（1.19 GiB）：`*.onnx`（145,342）、`*.onnx.data`（1,266,188,288）、`*.whl`（7,451,619）、`*.safetensors`（134，厂商 LFS 指针）。它们在本机盘上，换机器要靠 §5(a) 重编或从 LFS 重拉。

**状态：本目录 2026-11-04 只落盘，未 `git add`、未 commit、未 push。**

## 相关

- 板端登记：`wiki/PS/板端我方代码与产物台账.md` §十四（#160–#165／#182–#184／#196／#197）、§廿一 末（10-28 出库）
- 同类布局参照：`repos/HA4FPAI/lang/llm/Qwen3-0.6B/`（`README.md`＋`pipeline.md`＋`MANIFEST.md5`）；ERNIE 在 `repos/HA4FPAI/lang/llm/ERNIE-4.5-0.3B-PT4icraft430tai/`
- 驱动喂料契约与 12 B 那条坑：`repos/HA4FPAI/lang/llm/Qwen3-0.6B/pipeline.md`
- 编译主机路由：`wiki/SOP/交叉编译主机路由SOP.md`
