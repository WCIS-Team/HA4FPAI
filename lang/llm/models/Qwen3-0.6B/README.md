# Qwen3-0.6B-Instruct · NPU 迁移交付件

> 交付线目录（D-62，2026-10-18 立项）。"Qwen3-Instruct"＝Qwen/Qwen3-0.6B——Qwen3 家族 0.6B 档的 instruct 系本尊（2504 混合思考版），官方无独立 -Instruct 仓库，真底座是 Qwen3-0.6B-Base。
> SOP 正本：`wiki/PL/MOD/llm/Qwen3-Instruct按Qwen3配方迁移.md`；流程正本与全部实测读数＝本目录 `pipeline.md`；原理级论证与证据分级＝`wiki/PL/MOD/llm/Qwen3按厂商Qwen2配方迁移.md`；操作记录与对照表＝`wiki/PL/MOD/llm/qwen3-0.6b.md`。
> 与 vpu-live 同模式：小件进 git、体积件挡在库外（`repos/.gitignore` 的 `*.onnx`、`*.onnx.data`、`*.raw`、`*.safetensors`、压缩包等）。

## ⚠️ 板侧分词依赖：`tokenizers` ≥ 0.15（实测 0.20.3）

> **板上跑 `text_in`／`text_out`（板侧分词）前必须装这个，否则 tokenizer 加载失败。**
>
> 背景：Qwen3 的 `board/tokenizer.json`（vocab 151,643／merges 151,387）在板上 `tokenizers 0.13.3` 下加载即报
> `data did not match any variant of untagged enum ModelWrapper`（同 ERNIE 当年 #220 那条病），**根因＝0.13.3 太旧**，
> 解析不了这颗 json 的 BPE 模型区。升到 **0.20.3**（2026-11-04 板上实测）后直接可读，7 组样例 ids 与 PC `tokenizers 0.23.3`
> **逐字相同**（`你好，世界`→`[108386, 3837, 99489]`；`2026 年 11 月 4 日`→`[17,15,17,21,74577,…]`；`<|im_start|>user\nHi<|im_end|>`→`[151644,872,198,13048,151645]`）。
>
> 安装（板上，root）：
> ```bash
> python3 -m pip install --no-index --no-deps tokenizers-0.20.3-cp38-cp38-manylinux_2_17_aarch64.manylinux2014_aarch64.whl
> ```
> 轮子正本＝`S:\work\WCIS\workenv\tokwhl\tokenizers-0.20.3-cp38-cp38-manylinux_2_17_aarch64.manylinux2014_aarch64.whl`
> （PyPI 有 cp38 aarch64 轮子的最后几版是 0.15.2／0.19.1／0.20.3；0.21 起无 cp38）。

## 布局

| 子目录 | 内容 | 入库 |
|---|---|---|
| `pipeline.md` | 流程正本：六段流程＋实测读数＋"还没做"清单。10-28 自 `workenv\qwen3\` 入库，并补三处（头部 wiki 路径、§8 路径对照、§9 第 6 条凭据） | 是 |
| `export/` | genai fp16/cuda 导出件 `q3i_fp16_cuda/`（`model.onnx`＋`model.onnx.data`＋`genai_config.json`），源＝校机 `D:\yzd\WCIS\MOD\qwen3` | 否（体积件） |
| `compile/` | 本线现役脚本 **24 件**＝18 `.py`＋4 `.ps1`＋2 `.toml`（10-28 从 12 件并到 24 件；现役正本从此在本目录） | 是 |
| `board/` | **8 件**＝上板件 4（`Q3I-decoder_ZG.{json,raw}`＋`config.json`＋`tokenizer.json`）＋板侧脚本 4（`mem_watch.sh`／`qwen_board.py`／`sercap.ps1`／`ser_probe.ps1`，10-28 并入） | json 与脚本入库、raw 否 |
| `patch/` | **板侧驱动 2 件**：① `qwen3_infer_pad3.py`（23 602 B）＝板侧推理驱动成品件的同源副本，已含 Qwen3 结构（`:222` `n_kv_heads = 8`、`:223` `head_dim = 128`）与 §0 那条"上板必改"的两处 3 元素喂料（`:364`／`:416`）；**chat template 那一处仍是厂商 Qwen2 形（`:469`）**。② `q3_infer_board.py`（10 586 B／`e2364dddefcafb40e8293b9b0ffab75a`，10-28 按她"要补"入库）＝专门写给 Qwen3 的 decode-only 驱动：`:52` 28 层、`:53` 8 KV head、`:54` head_dim 128，`:39`～`:41` 直指板上 `/root/qwen3/` 那三件，`:173`／`:185` 喂 3 元素，`:31` 的 `sys.path.append` 指向 `/root/qwen2/modelzoo_utils`（厂商 Python 运行时，Qwen3 目录下没有这份件），`:201` 打了 chat-template 改写（旧值缺 `im_start` 后的换行与 `im_end` 前的分隔符；这处改写发生在 10-15 17:06，件 mtime 实测 `2026-10-15T17:06:46`，同批的 `patch_tpl.py` 仍在 `workenv\qwen3\board\`；**没与 `export/q3i_fp16_cuda/chat_template.jinja` 对过**）。两件都**没在板上跑过**（板上 `/root/qwen3/` 只有 `q3_probe.py` 与预检日志，`put.log` 里没有驱动件的 put 行、台账无登记行） | 是 |
| `ref/` | 数值基准与读数：`padfix_mask0_25.json`、25 token 链的 `tokens.txt`／`steps.json` | 是（小件） |
| `MANIFEST.md5` | 全目录 md5 清单（vpu-live 姿势） | 是 |

## 说明

- 五段中间件（parsed／optimized／quantized／adapted 四对 json＋raw，各 1.5 GB 级）**不进本目录**：正本在 `S:\apps\llm\agentwork\qwen3\compile\jr_path\Q3I\`（10-28 实测该路径在），复现按 SOP 阶段 2～4。
- 权重快照不回传：留在校机 `D:\yzd\WCIS\MOD\qwen3\Qwen3-0.6B`（ModelScope，`compile/dl_qwen3.py` 可重下）。
- **模型件已经在板上，驱动不在**（10-28 更正，旧断言"从没拷过板"作废）：10-14 18:09–18:13 UTC 那次 SFTP 把三件推到 `/root/qwen3/`，put 行两端 `md5sum` 逐字相同（正本＝`S:\apps\llm\agentwork\qwen3\board\put.log:5`／`:8`／`:11`）——`jr_path/decode/Q3V1-decoder_ZG.json` `28ccd902b8a1edfad07407c893473f63`／2 679 171 B、同名 `.raw` `1081ddd7222dc2cadd5bee8dc27b781f`／1 603 399 624 B（6.1 MB／s、267 s）、`config/qwen3/tokenizer.json` `7875a23badb888afee2869c78859aa81`／11 418 266 B；eMMC 从 8.3 G 空闲降到 6.8 G（66 %→73 %）。**板上名字是 `Q3V1-` 不是本目录的 `Q3I-`**，10-28 逐字节核过对应关系：`.raw` 两边同为 `1081ddd7…`；`.json` 把板上那颗里唯一一处 `Q3V1` 换成 `Q3I` 后与本目录 `board/Q3I-decoder_ZG.json`（`47699b3f…`／2 679 170 B）**逐字节相同** ⇒ 同一张图只差名字串；**但 `tokenizer.json` 两边不是同一份**（板上＝本机 `compile/fmodel/T0/tokenizer.json`＝厂商 clone `qwen2_exported_model_cuda/tokenizer.json`，三者同 md5；本目录留档那颗 `6423133b…`／11 422 654 B 与谁都不等，来源未核 C）。**但驱动从未上板、零 token**：件推完约 26 min 后（10-14 18:32–18:43 UTC）板上就地跑了 `q3_probe.py` 的四档预检，s1／s2／s3 全 `exit=0`，**s3 的 `apply` 实测 75.1 s**、5 s 心跳记到 `CmaFree` 最低 **80 kB**（lazy-load 窗口）与 `MemAvailable` 最低 **5,020 kB**（apply 窗口）⇒ 这颗 `_ZG` 件在 ZG330 上**完成过 NPU 编程**，且四档只走到编程就把 CMA 与整机内存都压到个位数 MB 级；生成那一步从未执行（板上没有 Qwen3 驱动：`put.log` 只有模型三件、台账没有驱动件的登记行），所以"件内数值在 NPU 上对不对"仍未答。四档读数见 `pipeline.md` §7，板侧登记＝台账 `#208`～`#213`。板上真跑出过 token 的是**厂商 Qwen2 那颗件**（10-14，20 token／2.83 tok/s；它的 `jr_path`／`config` 两件已于 10-28 出库，见下面"板上 Qwen2 让位"）。上板前置里"驱动按 Qwen3 结构改造、`token_dynamic_fm` 喂 3 元素"两条已由 `patch/` 两件解决（未验），剩下 CMA／位流 md5 门禁与点火本身——本线不做，指针见 SOP 第六节。
- `Q3I-decoder_ZG.raw` 的 md5 必须等于 `Q3I-decoder_ZG.json` 内的 `params_md5`（SOP 阶段 4 判据）；将来上板传输验收只需重算 `.raw` 的 md5 与本清单对数。**10-30 该 `.raw` 已出库**（见下面 10-30 那条），要复算就拿正本 `S:\apps\llm\agentwork\qwen3\compile\jr_path\Q3V1\Q3V1-decoder_ZG.raw`——两者 md5 逐字相同（`1081ddd7…`）。

## 状态

- **2026-10-18**：目录与 README 建立（D-62）。当时那句"**管线未跑**——本轮执行通道（shell）被 Windows 文件权限挡死"**作废**：`board/`、`export/`、`ref/` 三堆产物与 `pipeline.md` §0 那批 10-14 实测读数都在，管线跑过了。
- **2026-10-28**（她"不装了"，本线收口。原会话那条 03:13 没落地——它绑的执行目录 `S:\work\WCIS\test` 已改名成 `workenv`，本次在 workenv 会话补做）：
  - **阶段 4 判据复算 PASS**：`board/Q3I-decoder_ZG.raw`（1 603 399 624 B）实算 md5 `1081ddd7222dc2cadd5bee8dc27b781f`，与件内唯一一处 `params_md5`、与 10-18 版 MANIFEST 记账值三者逐字相等。
  - **出库 1 件**：`.newline-test.tmp`（1 B，内容＝单个 `0x0A`）已 `send2trash` 进回收站，盘上确认消失。库里的删除记账：这条 `D` **已随 10-28 那笔 `84839b6` 提交**（`git show --name-status 84839b6` 里是 `D Qwen3-0.6B-Instruct/.newline-test.tmp`），本行原写"现在显示为未暂存的 `D`、暂存留到收口那一笔"——那是提交前的状态，现已作废。
  - **MANIFEST 姿势改两处**：① 去掉自指行——文件不可能含自己的 md5，10-18 版第 2 行那个值是失效记账；② 全目录重算＝**45 行覆盖 45 件**（不含 `MANIFEST.md5` 自身；加它盘上 46 件）。10-28 实测该清单 2 675 B、45 行、LF、无 BOM、无自指、无 `.tmp`；`md5sum -c MANIFEST.md5` 报 45 个 OK、0 个不 OK——比 10-18 版 28 行多出的 17 行＝并入的 19 件减掉出库的 1 件、再减掉自指行。**重算一律带 `-t`**（`... | xargs md5sum -t > MANIFEST.md5`）：Git Bash 的 `md5sum` 默认二进制模式，会把每行写成 `<md5> *<路径>`、与本库沿用的两个空格分隔逐行冲突（命令正本在 SOP 阶段 6）。
  - **凭据面扩大（按她批准"原样入库"）**：入库件里 1 件 `board/qwen_board.py` 内嵌板子 root 明文口令。扫描口径＝取 `agentwork/board-ssh.py` 单点那串做子串比对，19 件里命中 1、其余 18 件零命中（第 19 件 `patch/q3_infer_board.py` 单独再扫一遍：口令零命中，私钥／`ghp_`／`github_pat_`／`AKIA`／`xox` 五形零命中，唯一像样的 `token=` 命中是 `:209` 打印 token id 的 `print`）。值本身仍只记 wiki 单点处（D-27 口径，本文不写）；性质＝厂商公开出厂默认口令（D-54 改判）。代价备查：这块板一旦改成非默认口令，该件当场作废。
  - **第 19 件已补入库**：`patch/q3_infer_board.py`＝`workenv\qwen3\board\q3_infer_board.py` 的同源副本（10 586 B／`e2364dddefcafb40e8293b9b0ffab75a`，10-28 两侧实算逐字相等，也与 `MOVE-MANIFEST.md:256` 10-17 那笔记账相等），是两支板侧驱动里唯一带 chat-template 改写的那支——按她 10-28"要补"入库，入库判据从"`pipeline.md` 点名"扩成"点名 ＋ 复现／上板必需"。它进 `MANIFEST.md5` 的那一笔（44→45 行）在 84839b6 之后，与本页这几行改动同笔提交。`patch/qwen3_infer_pad3.py:469` 仍是厂商 Qwen2 模板，两处不是同一件。
  - **未入库的 45 件**＝`pipeline.md` 零点名的探路脚本，按仓规留 `workenv\qwen3\`；那个根被 `.gitignore` 第 57 行整目录排除（`git check-ignore -v workenv/qwen3/pipeline.md` 实测命中 `.gitignore:57:/workenv/`），这些件没有版本控制。账（10-28 上午）：workenv 该根 64 件 − 并入本库 19 件 = 45。**10-28 下午复算该根 69 件**——比 64 多的 5 件不是新脚本，是本轮给台账 `#213` 补的留底（`board\hb_q3_s{0..3}.log` 4 份＋`board\run1_1014_headtail.txt`）；那 5 件同样不入库。
  - **板上 Qwen2 让位（10-28，她"把板子上的 Qwen2 删了，节约空间"）**：`/root/qwen2/` 删掉 `jr_path/`（1.3 G，ZG 件两件）、`config/`（6.8 M）、`run1_1014.log`（451 M，`#197` 登记的失控 traceback 洪泛日志，头尾已先落 `workenv\qwen3\board\run1_1014_headtail.txt`）——三道门禁见 `pipeline.md` §7；保留 `modelzoo_utils/`（172 K，`q3_infer_board.py:31` 吃这个路径）与全部板侧脚本、小日志（`#162`／`#196`／`#182`／`#183`／`#184`／`#197` 登记件，100 KB 级）。`/root/qwen3/` 一件没动。
- **2026-10-30**（她"砍掉"，交付线结案同笔）：
  - **出库 2 件＝两份 1.6 GB 的重复参数体**：`S:\apps\llm\agentwork\qwen3\compile\jr_path\Q3I\Q3I-decoder_ZG.raw` 与本目录 `board/Q3I-decoder_ZG.raw`（两者与正本 `compile\jr_path\Q3V1\Q3V1-decoder_ZG.raw` md5 逐字相等＝`1081ddd7…`／1 603 399 624 B，10-30 重算）⇒ **盘上同一颗只剩正本一份＋板上那份**；`send2trash` 进回收站，枚举到两条、`DeletedFrom` 逐字对得上，2.99 GiB（3,206,799,248 B）要清空回收站才回。库里那份 `.raw` 本来就被 `repos/.gitignore` 的 `*.raw` 挡着，所以 `git status` 不出现删除。**要按 `Q3I` 这名重推板＝把正本 raw 复制改名，不用重编**（`_ZG.json` 内无 `.raw` 路径字段，只有 `params_md5`／`params_bytes`）。
  - **`MANIFEST.md5` 重算 45→44 行**（出库那 1 件 ＋ `README.md`／`pipeline.md` 本次改写）：`md5sum -c MANIFEST.md5` ＝ 44 OK／0 FAILED／0 WARNING。结案读数见 `pipeline.md` §8"10-19 交付线件"那行。
