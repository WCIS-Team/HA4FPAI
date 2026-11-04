# Qwen3-0.6B → FMSH 30TAI（ZG330）：全流程可复现包

> 这一份是**流程正本**：从 HF 权重到能在板子上跑的 ZG 件，每一步在哪个主机、敲什么命令、吃什么产物、吐什么产物、
> 实测多少时间与字节数，按顺序写在这里。`README.md` 是分脚本说明（保留不动）；原理级论证与证据分级在 wiki
> `S:\work\WCIS\wiki\PL\MOD\llm\Qwen3按厂商Qwen2配方迁移.md`；操作记录与对照表在 wiki
> `S:\work\WCIS\wiki\PL\MOD\llm\qwen3-0.6b.md`。三者不重复：这里只管"照做就能复现"。
> （10-28 修：上面两条原写作 `wiki\md\技术文档\`，那个目录已不存在——`ProgrammableLogic/`→`PL/` 改名后现役位置即此，实测 `ls` 过。）

## 0. 现在到哪一步了（10-14）

| 段 | 状态 | 证据 |
|---|---|---|
| 导出 → 改写 → 五段编译 → ZG 件 | 全通 | `icraft/Q3V1/Q3V1-decoder_ZG.{json,raw}`，`_ZG.raw` 的 md5 与件自带 `params_md5` 逐字相等（A） |
| PC 单步前向＝数值正确性 | 通 | 与校机 HF fp32/eager 基准，同口径 teacher-force 43 步 corr **0.999922~0.999994**、argmax 41/43（A） |
| 修正后整链 | **件本身没病**：35 步里 icraft 与 HF 同口径 **argmax 35/35 逐个相同**（A） | `compile/gen_fixed35/steps.json` vs `compile/fix_p1b1.json` |
| 生成质量 | **Qwen3 在厂商口径下出乱码**：HF 侧同一模型、把零槽去掉就通顺，留着就退化成 `99321` 无限重复。**同一条路编出来的厂商 Qwen2 件在同一口径下出正确答案**（A）⇒ 这是 Qwen3 特有的病，不是流程的病。**10-14 已在图上修掉，见下一行与 4b** | `compile/fix_texts.txt`、`compile/q2_chat_text.txt` |
| ZG 件上板 | **10-14 已跑通（用厂商 Qwen2 件）**：四步 bisect STEP1～4 全 OK、整驱动出 20 个 token、`2.83 tokens/s`／`247.71 ms/token`、答案通顺。死因确认＝**那四次挂死时板上位流没有 NPU**（`openDevice` 摸 `0x4000_0000` 把互连楔住、零 printk、只能断电）；换成厂商原版 `aca4612b…` 后 STEP1 **1.4 s 就过、网络串口全程没断** ⇒ 归因从推断升成实测。门禁还是 `md5sum /mnt/boot/BOOT.bin`（`aca4612b…`＝可跑；`fd15bb85…`／`0f4f00e1…`／`611ea706…`＝没 NPU 别跑），`fpga0/state` 那条 10-13 已作废。**Qwen3 那颗 `_ZG` 件已拷上板、且已过一次 NPU 编程，但零 token**——10-28 更正两处：① 本行原写"仍未上板（1.6 GB 没推过）"是错的，推送发生在 10-14 18:09–18:13（板钟），三件两侧 md5 逐字相同（读数见 §7 那张板上件表）；② 本行原写"从未在板上跑过"也不准——件推完约 26 min 后板上就地跑了 `q3_probe.py` 四档预检，s1／s2／s3 全 `exit=0`，**s3 的 `apply` 实测 75.1 s**（读数见 §7 的预检块）。没做的是把驱动 `patch/q3_infer_board.py`（28 层／8 KV head／head_dim 128 已改好、`token_dynamic_fm` 已喂 3 元素）推上去，所以生成从未执行 ⇒ "件内数值在 NPU 上对不对"仍未答 | 10-14 实测 A：板上 `/root/qwen2/bisect.log` 五行齐＋`run2_1014.log`；台账 `#196`／`#197`；机理两条 A 级既有记录见第 7 节。拷板读数 A：`S:\apps\llm\agentwork\qwen3\board\put.log:5`／`:8`／`:11`（put 行含两端 md5）与 `:19`～`:24`（板上 `ls`）；预检读数 A：板上 `/root/qwen3/{s1,s2,s3}.log`，本机留底 `S:\apps\llm\agentwork\qwen3\board\board_s{0,1,2,3}.log`（与板上逐字节同 md5），台账 `#208`～`#213` |
| **上板必改的一处（厂商驱动的 bug）** | **`token_dynamic_fm` 要喂 3 个 fp32**：ZG 件把 v_id 1 声明成 `[1,3]`（12 B），厂商驱动 `:364`／`:416` 只喂 2 个（8 B）⇒ 板上第一次真跑直接抛 `hardop.cpp:46 … host_chunk byte_size:8 is diff from plddr_chunk byte_size:12`。本机 Host 后端**不查输入宽度**，所以这条只有上板才暴露。修法＝两处各补一个图里不读的 `0`（第 3 个元素 10-12 已实测图内不消费）⇒ 成品件 `/root/qwen2/qwen2_infer_pad3.py`（23,549 B／`597eeb3b…`）。**Qwen3 上板要照同一处改** | 10-14 实测 A（异常原文在板上 `run1_1014.log:60-61`；改完 `run2_1014.log` 跑通） |
| 板上内存／CMA 实况（以前全是 D，现在有数） | `lazyLoadParamsFromFile` 读 1.22 GiB 权重 **64.4 s、读完 RSS 只 546 MB**（VmSize 1.88 GB）⇒ **不整颗驻 RAM**；`initSession`＋`apply` **132.1 s、RSS 654 MB**；**ZG 运行时吃 CMA**：`CmaFree` 起跑 130,784 kB → apply 时 104,580 → 一轮真推理最低 **8,860 kB**（≈122 MB），`MemAvailable` 最低 386,740 kB、零 OOM ⇒ **跑 LLM 前必须先看 `CmaFree`，且不能与另一条会话的直播链同窗** | 10-14 实测 A（板上 `bisect.log`＋`/root/hb/q2_*.log`、`q2run.log`、`q2p3.log` 心跳） |
| 零 pad 的图级补丁 | **已做通并编译出件（10-14）**：手改 adapted IR 插 Slice/Slice/Concat 把 mask 的 slot 0 关成 `-inf`，零新常量、`.raw` 不动；PC Host 前向 25 个生成 id 与校机 HF `mask0` 基准**逐个全等**；`generate` 出的补丁件把这三颗折成 NPU HardOp（2174→2177） | 10-14 实测 A：dump v_id 37923＝`[-inf,0,-inf×510]`、`compile/gen_mask0/tokens.txt` vs `padfix_mask0_testset.json`、HardOp 类名计数差；**件内数值只有上板才能验**（本机无前向 ZG 的模拟器） |

## 1. 主机分工

| 主机 | 干什么 | 约束 |
|---|---|---|
| 校机 `ssh school-guest` | HF 权重快照、ONNX 导出、HF fp32/eager 基准 | 共享机，只写 `D:\yzd\WCIS\MOD\qwen3`，不做系统级安装；起活前先查她的 ASR 批次在不在跑（`tasklist /FI "IMAGENAME eq python.exe" /FO CSV /NH`） |
| 本机（有 icraft CLI） | 结构改写、五段编译、Host 前向、所有比对 | `C:\Icraft\CLI v3.39.0`；Host 前向稳态 4.2 GB／瞬时峰 6.0 GB ⇒ 起跑前 `AvailPhys` 要 ≥6 GB（10-12 实测 15.5 GB 空闲） |
| 板子 192.168.1.113（`112↔113` 会漂，认 host key） | 只做运行：加载 ZG 件、前向 | **禁止在板上编译**；不动另一条会话的进程；系统 `python3` 禁动 |

两机之间传文件一律 tar over ssh：

```
tar -cf - <文件> | ssh school-guest "tar -xf - -C D:\yzd\WCIS\MOD\qwen3"
ssh school-guest "tar -cf - -C D:\yzd\WCIS\MOD\qwen3 <文件或目录>" | tar -xf - -C S:/apps/llm/agentwork/qwen3/compile
```

板子侧：`python S:/apps/llm/agentwork/board-ssh.py 192.168.1.113 "<cmd>"`（每条命令 60 s 上限）；
长命令用 `board/qwen_board.py "<cmd>" <秒>`；传文件
`MSYS_NO_PATHCONV=1 python board-put.py 192.168.1.113 <本地> <远程>`——不带那个环境变量时
Git Bash 会把 `/root/...` 改写成 `C:/Program Files/Git/root/...`（10-12 踩过）。

## 2. 阶段 1 导出（校机）

```
ssh school-guest "cd /d D:\yzd\WCIS\MOD\qwen3 && venv\Scripts\python.exe -m onnxruntime_genai.models.builder ^
  -i D:\yzd\WCIS\MOD\qwen3\Qwen3-0.6B -o D:\yzd\WCIS\MOD\qwen3\qwen3_fp16_cuda -p fp16 -e cuda -c D:\yzd\WCIS\MOD\qwen3\cache_dir ^
  --extra_options fuse_qk_norm_gqa=false"
```

- **`fuse_qk_norm_gqa=false` 必须带**，否则 Qwen3 的 per-head QK-Norm 被折进 GQA，icraft 侧没有对应算子。
- `-p fp16 -e cpu` 会退化成 2125 节点的 `MultiHeadAttention`＋显式 `RotaryEmbedding`＋多出 `position_ids` 输入，icraft 过不去。
- 环境实测：`venv` ＝ Python 3.12.15、`transformers 5.19.0`、`torch 2.14.1+cpu`、onnxruntime-genai；装包顺序与坑在 `setup-school.ps1`。
- `genai_config.json`：vocab 151936／28 层／16 头／8 KV 头／head_size 128／hidden 1024。
- 导出日志存 `logs/export_a.log`、`logs/export_b.log`。
- **10-19 那次重导（`compile/fmodel/q3i_fp16_cuda`，248,431 B／`450ada9d…`）比这份多 1,232 B，差在 opset 与 KV 轴、不在图**（10-30 实测，A）：
  producer 两份逐字相同＝`onnxruntime-genai 0.17.1+83de55a`；新的 ir10／opset22，旧的导出时也是 ir10 但 opset21（`model.onnx.ir10.bak` 为证）；
  ir 号与 opset 号各占 1 字节 ⇒ **把 ir/opset 对齐后文件差仍是 1,232 B，这 1,232 B 全部在图 IO 的类型描述里**＝112 条 KV 张量
  （56 进 `past_key_values.*`＋56 出 `present.*`，28 层×K/V）的第四轴从 `dim_value: 128` 变成 `dim_param: "kv_cache_dim"`，每条 +11 B（11×112＝1,232，与文件差对平）；
  520 节点逐条属性 **0 处不同**、256 初始化器 **0 处不同**、`.data` 六个 8 MiB 抽样刀 **0 处不等**。
  ⇒ 现役那颗的 KV 是**烘死的定长** `@fp(16)[1,8,512,128]`（第 7 节板上那颗直读），新导出这一版把它改成了符号轴；**符号轴 icraft 吃不吃、会不会让 KV 变成长度可变＝没测过**，这就是"它从没进过编译"留下的唯一真问题。脚本与读数＝`S:\work\WCIS\workenv\q3i_probe\{diff_proto,diff_bytes,diff_attr,cmp_data}.py`。

## 3. 阶段 2 取到本机 ＋ 两处结构改写（本机，顺序不能反）

```
ssh school-guest "tar -cf - -C D:\yzd\WCIS\MOD\qwen3 qwen3_fp16_cuda" | tar -xf - -C S:/apps/llm/agentwork/qwen3/compile/fmodel
cd S:/apps/llm/agentwork/qwen3
python meta_variant.py --dst q3_o17sln --opset 17 --sln-ms
python detranspose_embed.py --src compile/fmodel/q3_o17sln --dst compile/fmodel/q3_v1
```

- `meta_variant.py`：ir 10→8、`ai.onnx` opset→17、把 QK-Norm 的 `LayerNormalization` 移进 `com.microsoft` 域；`.data` 走硬链接不复制。
- `detranspose_embed.py`：`[1024,151936]` 的 embedding 转置成 `[151936,1024]` 的初始化器并删掉那个 fp16 `Transpose`
  ——这是 icraft `Unknown dtype 10` 的单点解法，代价是多存 311,164,928 B（与厂商同形）。
- **绝不就地追加 `.data`**：前面阶段的数据文件是硬链接，写穿会毁原件
  （`q3_ir10`／`q3_o17sln`／`q3_slnms`／`qwen3_exported` 四份 `model.onnx.data` 就是同一个 inode，1,202,585,600 B 只占一次盘）。

## 4. 阶段 3 五段编译（本机，cwd 必须是 `compile/`）

```
cd S:/apps/llm/agentwork/qwen3
python mk_parse_toml.py --name Q3V1 --fm q3_v1
cd compile
"C:/Icraft/CLI v3.39.0/bin/icraft.exe" parse    Q3V1_parse.toml
"C:/Icraft/CLI v3.39.0/bin/icraft.exe" optimize Q3V1_parse.toml
"C:/Icraft/CLI v3.39.0/bin/icraft.exe" quantize Q3V1_parse.toml
"C:/Icraft/CLI v3.39.0/bin/icraft.exe" adapt    Q3V1_parse.toml
"C:/Icraft/CLI v3.39.0/bin/icraft.exe" generate Q3V1_parse.toml --log_path Q3V1_gen_log
```

- 前四段各约 50 s；`generate` 实测 342 s（日志里 `Timer CodeGenFunc`），2235 算子里 HardOp 2174。
- 判成败看日志里有没有 `stepTo faild`／`fatal`，**别只看 rc**（icraft fatal 时父进程仍可能 exit 0）。
- 产物在 `icraft/Q3V1/`（＝ `compile/jr_path/Q3V1/` 的 junction）：`parsed`／`optimized`／`quantized`／`adapted`／`_ZG` 各一对，
  外加 `Q3V1-prefill_parsed.{json,raw}`（prefill 网络只做到 parse，后面四段没编）。
- `_ZG.raw` md5 ＝ 件自带 `params_md5`（A，本机实算）。**10-14 起这份件是打过 mask 补丁的**：`params_md5 = 1081ddd7222dc2cadd5bee8dc27b781f`、
  raw 1,603,399,624 B；未打补丁的原件（`params_md5 = ec18ebf7fa830694f6232ba50b9dbd5a`、raw 1,603,398,820 B）留在
  `Q3V1-decoder_ZG.unpatched.{json,raw}`，要回退就 `cp` 回来。

## 4b. 阶段 3b mask 补丁：手改 adapted IR，只重跑 `generate`（本机，10-14）

厂商口径把 slot 0 留成全零 KV 而 mask 仍开着它，Qwen3 因此退化成单 token 循环（第 0 节、第 6 节判定 5）。
驱动侧填零槽两条路已废，剩下的唯一修法是在图上把 slot 0 关掉。`patch_mask0.py` 做的事：

```
M  = CausalMask 输出 v_id 180  fp32[1,1,512]        实测 [0, 0, -inf×510]
X  = Slice(M, begin[0,0,511] end[1,1,512])  → [1,1,1]     = M[511] = -inf
T  = Slice(M, begin[0,0,  1] end[1,1,512])  → [1,1,511]   = M[1:]，槽位序号不变
M' = Concat([X, T], axis=2)                 → [1,1,512]   = [-inf, M1, M2, …]
op_id 4533（Reshape）的输入 v_id 180 改接 M'
```

不加 `Params`、不动 `.raw`（`params_bytes`/`params_md5` 在 adapted 侧逐字未变），所以只重跑 `generate` 一段。

```
cd S:/apps/llm/agentwork/qwen3
# 1) 生成两份补丁图（第 3 个参数＝新算子的 compile_target，缺省 @hostt）
python patch_mask0.py compile/jr_path/Q3V1/Q3V1-decoder_adapted.json ^
  compile/jr_path/Q3V1/Q3V1-decoder_adapted_mask0.json                       # @hostt：只用于本机语义验收
python patch_mask0.py compile/jr_path/Q3V1/Q3V1-decoder_adapted.json ^
  compile/jr_path/Q3V1/Q3V1-decoder_adapted_mask0zg.json "@zhuget(330)"       # 上板用这份
# 2) 语义验收：单步把掩码和中间值读回来（cwd 必须与 --work 同一层，路径按 --work 相对 cwd）
cd compile
python ../host_gen.py --json jr_path/Q3V1/Q3V1-decoder_adapted_mask0.json ^
  --raw jr_path/Q3V1/Q3V1-decoder_adapted.raw --work probe_mask0zg ^
  --prompt 151644 --max-new 0 --pos-offset 1 --extra-dump 180,37921,37922,37923
# 3) 25 token 链（gen_fixed.ps1 里的 PROMPT 是写死的 11-token chat 前缀＝"你是谁？"，与 padfix 测试集同题）
powershell -ExecutionPolicy Bypass -File gen_fixed.ps1 -MaxNew 25 -Work gen_mask0 ^
  -Json jr_path/Q3V1/Q3V1-decoder_adapted_mask0.json
# 4) 出件（约 342 s；跑之前把现役 _ZG 备份，它会被覆写）
cp -p jr_path/Q3V1/Q3V1-decoder_ZG.raw jr_path/Q3V1/Q3V1-decoder_ZG.unpatched.raw
cp -p jr_path/Q3V1/Q3V1-decoder_ZG.json jr_path/Q3V1/Q3V1-decoder_ZG.unpatched.json
"C:/Icraft/CLI v3.39.0/bin/icraft.exe" generate Q3V1_gen_mask0zg.toml --log_path Q3V1_gen_mask0zg_log
```

- dump 的读法：`<work>/step0/run/Q3V1-decoder/ftmpSFB/<v_id>.ftmp`，SFB 一律是 fp32 裸字节，
  `np.fromfile(p, dtype='<f4')` 直接读；180＝原掩码、37921＝`X`、37922＝`T`、37923＝`M'`。

- **三颗新算子的 `compile_target` 必须是 `@zhuget(330)`**，与图内既有的 113 颗 Slice／56 颗 Concat 同标；`generate` 把它们折进
  HardOp 流（实测 2174→2177，`SliceNode` 113→115、`ConcatNode` 56→57，且没有算子被删）。标 `@hostt` 的那份
  （`_adapted_mask0.json`＋`Q3V1_gen_mask0.toml`）本机 Host 后端能跑、但板端 `libicraft_hostbackend.so` 里没有 Slice/Concat 的
  host 转发符号（同文件里 CausalMaskNode/ReshapeNode/CopyNode 各 5 处、SliceNode/ConcatNode 0 处，10-14 字节扫 B 级），
  所以板侧走不通，只留作本机语义验证用。
- 语义验收（本机，实测 A）：`host_gen.py … --extra-dump "180,37921,37922,37923"` 单步 dump，M′ 读回 `[-inf, 0, -inf×510]`；
  25 token 链 `powershell -ExecutionPolicy Bypass -File gen_fixed.ps1 -MaxNew 25 -Work gen_mask0 -Json jr_path/Q3V1/Q3V1-decoder_adapted_mask0.json`
  ＝ **与校机 HF `padfix.py --variant mask0` 的 25 个 id 逐个全等**，解出
  `好的，用户问我是谁。我需要先确认用户的问题意图。用户可能是在测试我的回答能力，`；`icraft-run` peakWS 6,024 MB、单步 40.5~44.0 s。
- 两份变体在 Host 前向下单步 logits **逐位相同**（argmax 14582，maxabs 0.0）⇒ retarget 不改语义，只改后端归属。
- 已知边界：`M[511]` 只在 `e1 < 511` 时是 `-inf`；cache 写满那一步（`e1 = 511`）slot 0 会重新张开。那一步零槽里仍是真 token 的
  位置号，影响仅最后一格，且与厂商 512 上限本身同级，未单独测。


## 5. 阶段 4 PC 前向出 token（本机）

```
cd S:/apps/llm/agentwork/qwen3/compile
python ../host_gen.py --json jr_path/Q3V1/Q3V1-decoder_adapted.json ^
  --raw jr_path/Q3V1/Q3V1-decoder_adapted.raw ^
  --work gen_fixed35 --prompt 151644,872,198,105043,100165,11319,151645,198,151644,77091,198 ^
  --max-new 24 --pos-offset 1 --dump-format SFB
```

`gen_fixed.ps1` 是同一条命令的带采样版本：每 1 s 记 `icraft-run` 与 python 的 WS／PeakWS／Private 到 `<work>_rss.csv`，
退出时清掉残留的 `icraft-run`（10-12 实跑 35 步，peakWS 6,025 MB，40.9 s/步，产物 5.9 GB）。

喂料契约（A，定版）：

- v_id 0 `input_ids` `@fp(32)[1,1]`；v_id 1 `token_dynamic_fm` `@fp(32)[1,3]`＝
  ［本步新 token 数恒 1，**RoPE 位置＝KV 写槽号＝mask 有效长度**，第三个值图上没有消费者］；v_id 2.. 是 56× past KV `@fp(16)[1,8,512,128]`。
- 元素 1 取 0 会挂在 `op_id 4525 ReshapeNode`（期望 2048 拿到 1048576）⇒ **首 token 必须从 1 起、slot 0 永久留全零 pad**，
  所以 `--pos-offset` 默认就是 1。
- `inputs.txt` 里必须写绝对路径；`--dump_ftmp` 只导请求的张量（整图那档约 4.4 GB/步，收窄后约 170 MB/步）。
- **SFB 一律拓宽成 fp32**（logits 607,744 B＝151936×4，KV 2,097,152 B＝524288×4）⇒ 回喂要显式 fp32→fp16，不能逐字节拷；
  拓宽无损（实测回写 maxdiff 0.0）。
- **prompt 阶段的 argmax 必须丢掉**：`--prompt` 的 11 个 token 逐个喂（和厂商驱动一样），
  只有**最后一个** prompt token 那步的 argmax 才是第一个生成 token。10-12 之前 `host_gen.py` 每步都 `seq.append(nxt)`，
  于是第 i 步（i<11）的 argmax 被当成第 11+i 个输入喂回去——输入流被打乱，输出成乱码。`ref_hf.py --simulate` 的贪心分支同一个病，两处都已改成
  `if i >= len(prompt)-1: append`。这条 bug 与上一版的"退化归因"混在一起，见第 6 节末。

## 6. 阶段 5 数值校验与口径判定（校机基准 ↔ 本机比对）

```
# 校机：按 icraft 的喂入序列 teacher-force（--teacher 关键，否则两边 step0 因 fp16 噪声分叉）
ssh school-guest "cd /d D:\yzd\WCIS\MOD\qwen3 && venv\Scripts\python.exe ref_hf.py --model D:\yzd\WCIS\MOD\qwen3\Qwen3-0.6B ^
  --simulate <逗号序列> --teacher --dump-logits D:\yzd\WCIS\MOD\qwen3\logits --out D:\yzd\WCIS\MOD\qwen3\ref_teach.json"
# 本机：逐步对齐表
python cmp_chain.py            # 43 步：argmax 是否同、corr、meanabs、topk 重合
python cmp_logits.py --icraft compile/gen_chain2/step1/logits.npy --icraft-format npy ^
  --hf compile/reflogits_sim/logits_teach_step1.npy
# 口径实验（零槽数 × 位置基 的 2×2）＋修正后的三条自由生成
python variants_run.ps1
python fixcheck_run.ps1        # 出 fix_chain.json / fix_p1b1.json / fix_p0b0.json
```

`ref_hf.py` 的模式：`--prefixes`（整段一次过）、`--zeropast token:pos`（单 token＋n 个零槽）、
`--chain`（HF 整段重算的贪心，等价于标准 `generate`）、
`--simulate [--teacher] [--pad-slots N] [--pos-base B]`（复刻导出件口径）、`--show-text`（把最终序列解码成文本，写进 json）。

**三条判定（都是 A）**：

1. `--simulate --pad-slots 0 --pos-base 0` 第 10 步的 top5 与 `--chain` 整段重算的 top5 **逐字相同**
   ⇒ 逐 token 带缓存这条路本身与整段重算等价，喂法没有系统性偏差。
2. 固定 `--pad-slots` 时 `--pos-base` **完全不改变结果**（p1b1≡p1b0、p0b0≡p0b1，整条序列逐 token 相同）
   ⇒ 那个 +1 偏移不是旋钮，位置与写槽一起平移是自我抵消的。
3. 修正驱动之后，同一个 11-token chat 前缀跑 25~35 步自由生成：
   `--chain` 与 `--pad-slots 0` 出的是通顺中文（"好的，用户问我是谁。我需要先确认用户的问题意图…"），
   `--pad-slots 1`（＝厂商口径）从第一个生成 token 起就锁死在 `99321`＝"往"，25 步全是同一个 token。
   而 icraft 在同口径下 35 步 argmax 与 HF **逐个相同** ⇒ **件是忠实的，坏在"零槽被 mask 打开"这件事本身**。
   文本与序列：`compile/fix_texts.txt`、`compile/fix_{chain,p1b1,p0b0}.json`、`compile/gen_fixed35/steps.json`。

机理（B）：零 K ⇒ 该槽分数恒 0、零 V ⇒ 分子不贡献，只给分母加一项 e^0＝1，
于是注意力输出整体被乘 α＝Σe^z/(Σe^z＋1)。Qwen3 有 per-head QK-Norm，注意力分数被压在 O(1) 量级，
这项 "1" 就不再可忽略，28 层串联进残差流后直接把分布带偏；Qwen2 没有 QK-Norm，分数动辄十几，e^0 无所谓。
这条也解释了为什么厂商那套在 Qwen2 上是"能用"的。

4. **第四判定（A，10-12 补测）：那个口径在 Qwen2 身上真的没事——厂商件在厂商口径下出了教科书答案。**
   拿厂商自己的 `Qwen2-0.5B-decoder_adapted.{json,raw}`（`qwen2_vendor/repo/jr_path/decode/`）喂本机 Host 前向，
   同一个 11-token chat 前缀、同样 `--pos-offset 1`（＝slot 0 永久零 pad 且被 mask 打开），11＋16＝27 步：

   ```
   python decode_ids.py --steps compile/q2_chat/steps.json ^
     --tokenizer qwen2_vendor/repo/config/Qwen2-0___5B-Instruct/tokenizer.json --prompt-len 11
   # TEXT(fed tail) = '我是来自阿里云的超大规模语言模型，我叫通义千问'
   ```

   16 个生成 token 逐个不同、run-length 全是 1（对照 Qwen3 同口径的 `往往往…`×24）。
    ⇒ **零 pad 不是"编译流程的病"，是 Qwen3 特有的病**：同一条导出→改写→五段编译→Host 前向的路，
   Qwen2 出正确回答、Qwen3 出一字循环，两边都已排除驱动 bug。上面那条 α 机理因此从 B 升到 **B→A 的旁证**
   （同一约定下换模型就换结果，唯一系统性差异就是 per-head QK-Norm）。
   实测数：Qwen2 单步 28~32 s（Qwen3 40.9 s），`icraft-run` peakWS **5,169 MB**（Qwen3 6,025 MB）。
   跑法＝`gen_fixed.ps1 -Work q2_chat -MaxNew 16 -Json <厂商 adapted.json> -Raw <厂商 adapted.raw>`。

5. **第五判定（A，10-13）：驱动侧"往零槽里填东西"实测死透；能救的只有"把 slot 0 从 mask 里关掉"。**
   `padfix.py`（校机 torch 2.14.1+cpu／transformers 5.19.0，fp32＋eager，缓存每步由脚本自己重建、不信 HF 内部）
   在同一个 11-token chat 前缀上跑五种口径各 24 步，产物 `compile/padfix_chat24.json`：

   | 口径 | 结果 |
   |---|---|
   | vendored＝零 pad＋mask 打开 | `往`×25，与 `fix_p1b1.json` 尾部 25 个 id 逐个相同 ⇒ 替身有效，这是对照组 |
   | copy1＝slot 0 填 slot 1 的 K/V 副本 | `واروال…` 循环，distinct 2 |
   | copylast＝slot 0 填最新槽 | 第一个生成位就是 stop token，0 个 token |
   | mask0＝零槽留着但 mask 关掉 index 0 | 通顺中文 |
   | zero＝不留 pad、0 基 | 通顺中文 |

   **mask0 与 zero 的 25 个生成 id 逐个相同** ⇒ "关掉第 0 槽" ≡ "重导成 0 基"，且 RoPE 那个 +1 偏移无关，
   与判定 2 自洽。换 10 条 prompt 的测试集：mask0 十条全通顺、每条 25 token、distinct 21~24、maxrun 全 1
   （`compile/padfix_mask0_testset.json`）；vendored 同测试集 3 条直接 0 token、3 条一字循环 maxrun=25、
   其余 distinct 2~4（`compile/padfix_vend_ts.json`）。这批 prompt 走裸 chat 模板、没 disable thinking，
   所以答案都以"思考"开头；只用来判"有没有退化成一字循环"。

   补丁点已定位（A）：编译件里 mask 只有一个张量 ＝ `CausalMask`（op_id 48，名 `attn_causal_mask`，
   `compile_target=@hostt`）的输出 v_id 180 `@fp(32)[1,1,512]`。`dump_mask.py` 单步实跑 47 s 把它 dump 出来，
   值 ＝ `[0, 0, -inf × 510]`（step 0、`tdf=[1,1,0]`，`compile/maskprobe/`）⇒ slot 0 与 slot 1 都开着，关闭值是 **-inf**。
   下游 Cast→Reshape→Expand 后 28 层各自 **Add** 到分数上再 Softmax ⇒ 加性掩码，所以"关掉 index 0"＝把那个 0.0 换成 -inf。
   但这个 op 能配的只有 `prefill_max=512`／`fill_value=-inf`（icraft 3.39.0 xir docs，`CausalMask(input,
   config_dynamic, prefill_max, fill_value)`），**没有"跳过第 0 槽"的旋钮** ⇒ 只能改图：在 v_id 180 之后插一个
   `Add` 常数（index 0＝-inf、其余 0；0＋(-inf)＝-inf，已闭槽 -inf＋0 仍是 -inf）。
   **手改 icraft IR json 能不能过校验、能不能接着重编，没做、没验（D）。**

## 7. 阶段 6 上板（板子）

板上现成件（厂商 Qwen2-0.5B）：**10-28 起只剩驱动与运行时**——模型件 `jr_path/decode/Qwen2-0.5B-decoder_ZG.{json,raw}`（2,183,817＋1,314,012,420 B）
与 `config/Qwen2-0___5B-Instruct/{config.json,tokenizer.json}` 已按她"节约空间"出库（正本在本机 `S:\apps\llm\agentwork\qwen\repo\`，重推 1.3 GB ≈3.6 min @6 MB/s）；
留下 `qwen2_infer_decode_only_axi.py`、`qwen2_infer_pad3.py`、`modelzoo_utils/pyrtutils/`、`qwen_bisect.*`、`mem_watch.sh` 与全部小日志，`/root/qwen2` 从 1.7 G 降到 **496 K**。
板侧 python 3.8.10 ＋ icraft ＋ tokenizers 0.13.3 ＋ numpy 1.23.0；
`from icraft import xir,xrt,host_backend,buyibackend,zg330backend` 与 `pyrtutils.*` 全部 import 通（A，10-12 实测）。
**驱动必须从 `/root/qwen2` 里跑**（`sys.path.append("./modelzoo_utils")` 是相对路径）——`patch/q3_infer_board.py:31` 用的也是这条路径（写成绝对 `/root/qwen2/modelzoo_utils`），这就是 10-28 没把 `modelzoo_utils/` 删掉的原因。

板上 Qwen3 件（10-14 18:09–18:13 UTC 推送，A：`S:\apps\llm\agentwork\qwen3\board\put.log:5`／`:8`／`:11` 的 put 行＋`:19`～`:24` 的板上 `ls`）：

```
/root/qwen3/jr_path/decode/Q3V1-decoder_ZG.json   2 679 171 B  28ccd902b8a1edfad07407c893473f63
/root/qwen3/jr_path/decode/Q3V1-decoder_ZG.raw   1603399624 B  1081ddd7222dc2cadd5bee8dc27b781f  (6.1 MB/s, 267 s)
/root/qwen3/config/qwen3/tokenizer.json          11 418 266 B  7875a23badb888afee2869c78859aa81
/root/qwen3/q3_probe.py                              4 359 B   9f5d4857cb8b4fd80dc786455e313b7d
/root/qwen3/{s0,s1,s2,s3}.log                    3 491/3 635/15 411/15 566 B
```

**板上这颗与本目录 `board/` 留档那颗的对应关系（10-28 逐字节核过，A）**：`.raw` 两边同为 `1081ddd7…`／1 603 399 624 B；
`.json` 差 1 B——把板上那颗里唯一一处 `Q3V1`（全文只出现 1 次）替换成 `Q3I`，结果与本目录 `board/Q3I-decoder_ZG.json`（`47699b3f…`／2 679 170 B）**逐字节相同** ⇒ 同一张图，只差网络名字串。
**10-30 追到根**：之所以"同一张图"，是因为交付线喂进编译的那三份 ONNX 本来就是探索线那三件的字节（不是巧合，见第 8 节"10-19 交付线件"那行）——两颗 `_ZG.raw` 的 md5 也当场重算过，逐字相同。
**`tokenizer.json` 两边不是同一份**：板上＝`7875a23b…`／11 418 266 B（＝本机 `compile/fmodel/T0/tokenizer.json`＝厂商 clone `qwen2_exported_model_cuda/tokenizer.json`，三者同 md5），
本目录留档＝`6423133b…`／11 422 654 B，与本机 `export/q3i_fp16_cuda/tokenizer.json`（`0fa489df…`／11 422 650 B）也不同；本机各根扫不到同尺寸件 ⇒ **留档那颗的来源未核（C）**。


点火 bisect（把"起跑就整机挂死"拆成 4 步，每步独立进程，结论写进 eMMC 的 `bisect.log`）：

```
cd /root/qwen2 && cap.sh --as 2G --tag qb_s1 --interval 1 -- python3 /root/qwen2/qwen_bisect.py 1   # openDevice
cd /root/qwen2 && cap.sh --as 2G --tag qb_s2 --interval 1 -- python3 /root/qwen2/qwen_bisect.py 2   # CreateFromJsonFile 建图
cd /root/qwen2 && cap.sh --as 2G --tag qb_s3 --interval 1 -- python3 /root/qwen2/qwen_bisect.py 3   # lazyLoadParamsFromFile 读 1.22 GiB 权重
cd /root/qwen2 && cap.sh --as 2G --tag qb_s4 --interval 1 -- python3 /root/qwen2/qwen_bisect.py 4   # initSession + apply（真正给 NPU 编程）
```

配套：`board/mem_watch.sh`（1 s 心跳，把 MemFree／CmaFree／Swap 与进程 VmRSS 追加到 eMMC，硬断电后仍在；
已推到 `/root/qwen2/mem_watch.sh`，md5 `f84ffd9539d9828ba3cd87de50c62948`）、
`board/sercap.ps1`（长时间占 COM6 抓串口 printk——板子挂死时唯一能看到内核还说话的通道，跑完记得放端口）、
`board/ser_probe.ps1`（两秒钟的死机判据：开 COM6→发回车→读字节数；`bytes=0` 就是硬挂死。
依据是台账 §十四 那条已核事实——本板 `getty@ttyPS0` 确实在起，所以串口静默可单独当死机判据；有 `login:` 就说明只是网络挂了）。

**Qwen2-0.5B 上板这一条，电来了照抄（命令都在板上，除断电与换位流外不需要她动手）**：

```
python S:/apps/llm/agentwork/board-ssh.py 192.168.1.113 "uptime; free -m | head -3; ps -eo pid,comm,rss --sort=-rss | head -5"
# 第一条门禁（10-13 加，取代旧的 fpga0/state）：板上位流必须含 NPU，否则 STEP1 必挂
python S:/apps/llm/agentwork/board-ssh.py 192.168.1.113 "findmnt -rn -S /dev/mmcblk0p1; md5sum /mnt/boot/BOOT.bin /mnt/boot/BOOT.bak*"
#   只认 md5：aca4612b… ＝ 厂商原版、含 NPU ⇒ 可以点火（**10-14 实测现装就是这颗，起跑前现场复核过**）
#   **10-14 补的坑**：本次开机 `/dev/mmcblk0p1` **没自动挂**，而 `/mnt/boot` 目录里那 `AA.bin`／`BOOT_NEW.bin` 是**根文件系统里的真文件、不是分区内容**
#     ⇒ "看不到 BOOT.bin"≠位流没了。门禁要读 md5 得先 `mount -t vfat -o ro /dev/mmcblk0p1 /mnt/boot`，读完 `umount` 还原（**用 ro，别 rw**——另一条会话的发布脚本会 rw 挂它）
#   fd15bb85…／0f4f00e1…／611ea706… ＝ 我方那几份纯视频位流、都不含 NPU ⇒ 停手，先按下面"恢复办法"换回
MSYS_NO_PATHCONV=1 python S:/apps/llm/agentwork/board-put.py 192.168.1.113 \
  S:/apps/llm/agentwork/qwen3/board/mem_watch.sh /root/qwen2/mem_watch.sh
# 心跳的三个参数依次是 日志路径／目标进程名匹配串／采样次数（10-13 实测：位置参数写错就落到 cwd 里建个名叫 tag 的文件）
python S:/apps/llm/agentwork/board/qwen_board.py "cd /root/qwen2 && setsid sh /root/qwen2/mem_watch.sh /root/qwen2/mem_watch.log qwen_bisect 420 >/dev/null 2>&1 </dev/null &" 25
# 下面两行在板子 shell 里跑（PuTTY COM6 或 ssh），stdout 一律落板端文件、不进管道
cd /root/qwen2 && timeout 180 cap.sh --as 2G --tag q2_s1 --interval 1 -- python3 /root/qwen2/qwen_bisect.py 1 >> /root/qwen2/s1.log 2>&1
cd /root/qwen2 && cap.sh --as 3G --tag q2p3 --interval 5 -- bash -c "printf '%s\n' '你是谁' '' | python3 qwen2_infer_pad3.py" > /root/qwen2/run2.log 2>&1
#   **驱动必须用 `qwen2_infer_pad3.py`（台账 #196），厂商那份 `qwen2_infer_decode_only_axi.py` 一喂输入就抛 InternalError**：
#     ZG 件 v_id 1 是 fp32[1,3]＝12 B，厂商只喂 2 个＝8 B ⇒ `hardop.cpp:46 host_chunk byte_size:8 is diff from plddr_chunk byte_size:12`
#   **stdin 循环遇到 EOF 不退出、每圈抛一次 traceback**：喂的最后一段必须是空行（`main()` 里 `input()` 拿到空串才是退出通道），
#     否则日志会被刷爆——10-14 那次 `run1_1014.log` 刷到 **472 MB／951 万行**
```

点火前先开串口抓取（`board/sercap.ps1 -Seconds 420 -Out ...`）——挂死时 journald 和 dmesg 都读不到，串口是唯一能看到内核还说话的通道。
判据：`bisect.log` 走到 STEP4 且 `run1.log` 里有 `token` 行 ⇒ 点火通；
STEP1 就挂 ⇒ **先查位流有没有 NPU（上面那条门禁），不要归因给"找厂商"**——10-13 已确认换回 `BOOT.bak` 就能把 NPU 带回来。
每条命令 60 s 上限（`board-ssh.py`），整驱动 150 token ≈ 30 s 但要留余量，超时用 `board/qwen_board.py`。
**板上禁止编译、系统 python3 禁动、不动另一条会话的进程、不代她 poweroff**——这四条是硬指令。

**当前死点（A，10-12 02:0x UTC）——10-14 已解除，见本节末"10-14 实跑结果"**：STEP1
`openDevice("zg330","192.168.125.143",mmu_mode=True,cuda_mode=False)` 一执行，网络与串口同时零输出、ping 不通、
串口回车无回显，`bisect.log` 停在 `STEP0 OK`，只能断电。同签名第三次（10-11 20:17 整驱动点火；
10-05 `sdi_probe` 开 `axi://zg330aiu?npu=0x40000000` 做 ICore 握手）。今天板子是空的：load 0.00、`MemAvailable` 747 MB、
CMA 剩 61 MB、dmesg 零条 OOM ⇒ 不是内存挤死，是这条调用本身把机器带走。

**10-14 实跑结果（A，板上文件为证，台账 `#196`／`#197`）＝点火通了，板子第一次出 token**。位流起跑前现场复核＝`aca4612b…`（含 NPU）：

```
STEP0 OK 2.4s    222920 kB  47640 kB  47640 kB   # 只 import
STEP1 OK 1.4s    386992 kB  47864 kB            # openDevice —— 10-12 挂死的那一步，这次 1.4 s 过
STEP2 OK 4.3s    598924 kB  68560 kB            # CreateFromJsonFile 建图
STEP3 OK 64.4s  1882140 kB 546768 kB 556616 kB  # lazyLoadParamsFromFile 读 1.22 GiB：64.4 s，RSS 只 546 MB ⇒ 不整颗驻 RAM
STEP4 OK 132.1s 2155380 kB 654876 kB 673848 kB  # initSession + apply（给 NPU 编程）＝132.1 s
```

（末三列＝`VmSize VmRSS VmHWM`，脚本 `:52` 的口径。）整驱动第一轮抛 `InternalError`（喂料宽度，见上面 `#196` 那条），
补成 3 元素后第二轮通：`First generated token: 104198`、`[生成完成，共生成 20 个token]`、`Total time: 7.08 s`、
`Average output speed: 2.83 tokens/s`、`Average decode time: 247.71 ms`、`Answer: 我是阿里云开发的超大规模语言模型，我叫通义千问。`
⇒ **上面那条"STEP1 就挂 ⇒ 先查位流有没有 NPU"现在是被实测证实的一条**，不是推断。
**吞吐与厂商 README 的 4.68 tokens/s／199 ms 不齐**（2.83／247.71）——厂商那颗数零条件说明，只并列、不作回退证据（B）。
**CMA 第一次有数**：`CmaFree` 起跑 130,784 kB → STEP1~3 全程不动 → STEP4 编程时 104,580 → 一轮真推理最低 **8,860 kB**（≈122 MB），
`MemAvailable` 最低 386,740 kB、零 OOM ⇒ **跑 LLM 前先看 `CmaFree`，且不与另一条会话的直播链同窗**（直播链也吃 CMA）。

**10-14 同一晚的 Qwen3 件预检（A；板上 `/root/qwen3/{s0,s1,s2,s3}.log`＋`q3_probe.py`，10-28 逐份读出并补登记，台账 `#208`～`#213`）**：
件推完 26 min 后就地跑了 `q3_probe.py` 的四档（0＝只建图＋读权重、1＝＋`openDevice`、2＝＋`Session.Create(init)`、3＝＋`apply`），一律套 `cap.sh --as 3G`：

```
s0 18:32:37→18:33:57 exit=1  TypeError: 'int' object is not callable @ q3_probe.py:45   # 脚本自身的 bug（这一跑用的是修正前的脚本，见下一段）
s1 18:34:54→18:36:13 exit=0  openDevice 过：VmRSS 468,256 kB，CmaFree 130,776 全程不动
s2 18:36:55→18:38:32 exit=0  session_create 过（日志末 "skip apply (Q3STEP=2)"），VmSize 2,531,836 kB／VmRSS 488,788 kB，MemAvailable 277,064
s3 18:39:13→18:43:47 exit=0  **apply 75.1 s**：VmHWM 699,536 kB／VmRSS 637,280 kB，阶段末 CmaFree 33,456
```

（**s0 那行按的是修正前的脚本**：traceback 打印的源码行是 `print("params_bytes=%d" % net.params_bytes() ...)`，而 10-28 板上与本机这份同 md5（`9f5d4857…`／4,359 B）的件里 `:45` 已是 `pb = getattr(net, "params_bytes", None)`、`:46` 带 `callable(pb)` 保护 ⇒ 现件里那行报错语句已不存在。修正落在 s0（18:32:37）与 s1（18:34:54）之间，具体时刻没取证——本机这份 mtime 是 10-15 02:39（＝18:39 UTC，比 s1 还晚），更像事后从板上拉回的副本而不是写作时刻。s1～s3 三份日志第 16 行都打出 `params_bytes=1603399624`，与 `.raw` 字节数相等。）

（起止都取 `cap.sh` 自己打的 `start`／`cap: exit=` 两行原文。前四行的 `CmaFree`／`MemAvailable` 是 `[MEM]` 阶段边界采样；**极值只有 5 s 心跳看得见**——`/root/hb/q3_s3.log` 逐行：18:39:34 还是 130,532，18:39:39 →104,396、18:39:44 →**220**、18:39:49 →**80 kB（全档最低）**，随后 18:39:55／18:40:00 各 116、18:40:05 120，18:40:10 回到 128,656 ——这段低窗正落在 lazy-load 里；apply 窗 18:42:09 `MemAvailable` **只剩 5,020 kB**、同窗 `CmaFree` 最低 2,440。s0～s2 同样跑 lazy-load，`CmaFree` 却恒 130,776 一动不动 ⇒ **这 130 MB 是谁占的没取证**（当场 dmesg 已随重启冲掉），别写成"ZG 运行时的固有开销"，台账 `#213` 同记。本机复算这份极值的留底＝`workenv\qwen3\board\hb_q3_s{0..3}.log`，与板上差在抬头与 CR，见台账 `#213`。）

⇒ **我方 Qwen3 那颗 `_ZG` 件在 ZG330 上完成过 NPU 编程**；`lazyLoadParamsFromFile` 读 1.6 GB 时阶段末 VmRSS 只 464,376～545,556 kB，与上面 Qwen2 那条"不整颗驻 RAM"同形。**`apply` 75.1 s 与 Qwen2 的 132.1 s 不能直接比长短**——那颗 132.1 s 是 `initSession`＋`apply` 合计，这里两步是分开的两条读数。
**但一个 token 都没出过**：板上没有 Qwen3 驱动（`put.log` 只有模型三件、台账没有驱动登记行），生成从未执行 ⇒ "件内数值在 NPU 上对不对"这条**仍未答**。

**10-28 板上空间腾挪（她"把板子上的 Qwen2 删了，节约空间"）**：`/root/qwen2/` 删掉 `jr_path/`（1.3 G）＋`config/`（6.8 M）＋`run1_1014.log`（451 M），
该目录从 1.7 G 降到 **496 K**，`/` 空闲 4.7 G → **6.4 G**（81 % → 74 %）；`/root/qwen3/` 一件没动（现场复核 json `28ccd902…`／tokenizer `7875a23b…` 不变、raw 仍是 1 603 399 624 B）。
**删前三道门禁**：① `113.txt`＝`off`；② 板上六件 `md5sum` 与本机正本**逐字相等**（`24bf0b6b…`／`2b107ec6…`／`1c74fd33…`／`ac22e242…`／`7e7bdf8d…`／`c6d5ad12…`）⇒ 删掉的每个字节盘上都有第二份；③ 451 MB 日志的头 40 KB＋尾 2 KB 先拉回本机留底 `workenv\qwen3\board\run1_1014_headtail.txt`（42,666 B／`73e58a8c168983c35fc81d663a572c88`）。
**保留**：`modelzoo_utils/`（172 K——`patch/q3_infer_board.py:31` 的 `sys.path.append` 正指这里，删了 Qwen3 驱动就 import 不到）、两支 Qwen2 驱动、`qwen_bisect.*`、`mem_watch.sh` 与 `bisect.log`／`s1~s4_1014.log`／`run2_1014.log`／`mem_watch.log`（台账 `#162`／`#163`／`#182`／`#183`／`#196`／`#197` 登记件，合计 100 KB 级）。
**从此板上要再跑厂商 Qwen2 那条**，得先把 `#160`／`#161`／`#164` 三件从本机 `S:\apps\llm\agentwork\qwen\repo\` 重推（1.3 GB ≈ 3.6 min @6 MB/s）。

**再点火之前的门禁（10-14 已执行、并已被实测证实）**：`openDevice` 走的两个地址 `0x40000000`／`0x80000000` 是 PL 侧 AXI 窗口
（10-05 strace 实测各 mmap 16 MiB，另加 `/dev/udmabuf0` 128 MiB）。PL 里没有位流或位流状态不对时，
访问这类窗口会让互连总线挂住——正是"零 printk、只能断电"的形态（**机理 10-14 升成 A**：同一支 `qwen_bisect.py 1`，
位流不含 NPU 时四次整机楔死，换成 `aca4612b…` 后 1.4 s 通过、串口全程有话）。所以冷启动后先读**不碰 PL 窗口**的状态：

```
cat /sys/class/fpga_manager/fpga0/state; cat /sys/class/fpga_manager/fpga0/status
dmesg | grep -i -E 'fpga|devcfg|pcap'
```

出厂 `uEnv.txt` 里那条 `loadbit` 指向 ext4 分区、`fatload` 读不到（见 wiki `PL位流重载通道失效.md`），
三条运行时重载通道全部失效、连厂商自己的位流都刷不进去 ⇒ **位流从哪来、还能不能回来，是要问厂商的问题，不是我们能改的**。
（**10-13 更正：这句错了**——"运行时重载"三条确实全坏，但 10-10 已经走通第四条＝离线改写 SD 卡引导镜像里的位流分区
（wiki `BOOTbin位流替换手术.md`），而厂商原版就在卡上 `BOOT.bak`、本地还有副本 `board_BOOT.bin`（MD5 `aca4612b…`）。
**换回去不需要厂商**，代价是这会动另一条会话在 PL 里的现场 ⇒ 由她定。命令见下面"恢复办法"那段。）
判据（**10-13 作废**）：`state` 不是 `operating` 就别跑 STEP1，跑一次就再断一次电。

**10-13 第四次点火：门禁全过，照样挂 ⇒ 上面那条判据不成立，死点已归因**。起跑前实测
`fpga0/state`＝`operating`、`status` 节点不存在、`dmesg | grep -iE 'fpga|devcfg|pcap'` 只有开机那两行注册信息、
板子空闲（load 0.03、`MemAvailable` 743 MB、**`CmaFree` 130 MB**、dmesg 零条 OOM）——STEP1 一执行仍旧
网络＋串口同时零输出、ping 全不通。`operating` 只表示"PL 里有设计在跑"，**不表示跑的是哪份设计**，所以它当不了门禁。

真因（**A**，两份记录拼出来的，不是我推的）：
① wiki `位流构建与SDI信号检测.md` §4.2 已实测 **"NPU 是 PS 通往 PL 的唯一 AXI 端点"**——位流里没有 NPU，
`0x4000_0000` 整个窗口就没有从设备，访问它＝互连挂住＝零 printk、只能断电（当时就是用 `sdi_probe` 量出来的）。
② 10-10 的 BOOT.bin 手术（`BOOTbin位流替换手术.md`）把**不含 NPU 的纯视频位流**灌进了 `/mnt/boot/BOOT.bin`，
而 §6.8.10（10-12 21:15）白纸黑字记着"**该位流未上板，板上 `BOOT.bin` 仍是上一份（MD5 `fd15bb85…`）**"——
那份新位流 `ai7030_lite_pd1` 同样不含 NPU（§6.8.1 加 DDR 状态导出通道，就是为了绕开编不出来的 NPU）。
⇒ 10-11／10-12／10-13 三次 Qwen 点火挂死与 10-10 的 `sdi_probe` 挂死是**同一件事**，四次全在那次手术之后，
"三次唯一共同点＝第一次摸 PL 窗口"这条观察现在有了机制。
**板上位流没 NPU ⇒ `openDevice` 永远挂，Qwen 这一条在那颗位流下不可能跑通**，与内存、CMA、驱动写法都无关。
**10-14 复核（A）**：现装已经**不是**那颗了——`md5sum /mnt/boot/BOOT.bin`＝`aca4612b566738350a8c5edac0db1929`＝厂商原版（含 NPU），
10-13 17:02:46 UTC 由另一条会话板上的发布脚本 `boot-restores/20261013T170246Z-8766769e/restore.py` 换回，`receipt.json`＋sha256 闭环齐全，
我方没有下手 ⇒ **"先换回位流才能点火"这一步已经不需要做了，门禁现在是过的**；但换回来之后**一次都没重新点过火**，所以"能跑通"仍然没有证据。

**恢复办法（不需要厂商、不需要 JTAG，这条路 10-10 已经走过一次并验证到 FSBL；10-13 17:02 那条会话已经替我们走过第二次）**：把厂商原版引导镜像换回去——

```
mount -t vfat -o rw /dev/mmcblk0p1 /mnt/boot        # 重启后 /mnt/boot 是空目录，必须先挂；10-14 实测本次开机它已挂着且是 ro，只读就别动
md5sum /mnt/boot/BOOT.bin /mnt/boot/BOOT.bak*       # 10-14 实测现装＝aca4612b…（厂商原版，含 NPU）⇒ 这一步已经是完成态
cp /mnt/boot/BOOT.bak /mnt/boot/BOOT.bin && sync    # 必须显式 sync，FAT 有写缓存
# 重启后串口应出现：Download the PL bitstream is done at the ~617 ms
```

卡上有 `BOOT.bak`／`BOOT.bak2` 两份回滚件，本地另有原件副本 `S:\apps\llm\agentwork\board_BOOT.bin`
（7,023,040 B，MD5 `aca4612b566738350a8c5edac0db1929`）。换回厂商位流后 NPU 就在了，而 SDI 探测**不回退**
（10-05 在厂商位流下 `sdi_probe` 读到过 `0x88 fetch_cnt=140`，A），代价是丢掉我方那条 DDR 状态导出通道
——**这条交换 10-13 17:02 已经生效了一次**（现装＝厂商原版），`/root/sdi_stat.py` 实测打印 `PL status writer ABSENT: magic=0x00000000`，
它只在 15:30～17:02 那 1.5 h 里可用过（读数留档＝台账 §十七 `#186`）。
**再换回我方那颗＝改掉 PL 里跑的设计＝另一条会话的实验现场 ⇒ 由她定，我不擅自动手。**

**本轮取证比前三次齐**（下次断电重启后先读这两份，都在 eMMC）：bisect 的 stdout 落 `/root/qwen2/s1.log`
（不再走管道＝还了 §七 那条方法认账），`/root/qwen2/mem_watch.log` 是挂死前最后 1 s 粒度的
`MemFree`／`MemAvailable`／`CmaFree`／`VmRSS`；串口 `board/serial_step1_1013.log` 20:37:45→20:44:45 **零行**。
页缓存默认 5 s 回写 ⇒ 最后约 5 s 的采样可能丢，这是这套方法的已知边界。

## 8. 产物清单（这个文件夹里都有什么）

> **本节相对路径的根（10-28 注）**：写作时（10-14）这个根＝`S:\apps\llm\agentwork\qwen3\`，下面 `onnx/`、`icraft/`、
> `pc_runs/`、`numeric/`、`logs/`、`archive/`、`compile/` 都按它解析。10-28 实测该根仍在，那十二个目录一件不缺，
> `qwen2_vendor` 仍是通向 `S:\apps\llm\agentwork\qwen\` 的 junction——**数据树没动过位置，本节路径照旧能用**。
> 动过的是**脚本**：复现现役正本自 10-28 起在本库根 `S:\work\WCIS\repos\Qwen3-0.6B-Instruct\` 的
> `compile/`（24 件）、`board/`（板侧脚本 4 件＋上板件 4 件）、`patch/`（板侧驱动 2 件）三处；
> 旧根 `S:\work\WCIS\workenv\qwen3\` 的 64 件 10-28 当场**未删**：复制那一步 18 件与本库逐字节全等，
> 但这份记录入库后又补了头部路径、本节对照与 §9 第 6 条，所以它自己两处的 md5 从此不同——**以本库为正本**。
> **同日按她的"要补"再入第 19 件**＝`board/q3_infer_board.py` → `patch/q3_infer_board.py`（复制后 md5 与源件逐字相等 `e2364dddefcafb40e8293b9b0ffab75a`／10 586 B）。
> 其余 **45 件**（64 − 19）是没被本文点名的探路件，按仓规留在 workenv 不入库。
> 体积件不进库（`repos/.gitignore` 的 `*.raw`／`*.onnx`／`*.onnx.data`／`*.safetensors`），要按上面 agentwork 绝对路径去取。

| 阶段 | 路径（junction 已建，旧路径照旧可用） | 字节 | 说明 |
|---|---|---|---|
| 导出原件 | `onnx/qwen3_exported/` | onnx 247,199＋data 1,202,585,600 | fp16/cuda、opset21；**这一格原写 ir10，10-30 更正：盘上这份 `model.onnx` 实测 ir＝8／`07caf0e3…`**，ir10 原文逐字节留了两份＝同目录 `model.onnx.ir10.bak` 与 `q3_ir10/model.onnx`（都 247,199／`bc4b36a2…`）。⇒ 导出后有人在原地把 ir 号改小过，**那条命令行没留档**（会写 ir 号的只有 `meta_variant.py --ir`，而它默认写 `--dst`，就地覆盖得把 `--dst` 指回本目录＝B 级推测）；ir 号 8 与 10 各占 1 字节，换号不改文件大小。`.data` 与下面两份同 inode（`1970324837030514`／nlink=4＝`qwen3_exported`＋`q3_ir10`＋`q3_o17sln`＋`q3_slnms` 共用一份 1.2 GB） |
| 改写 1 | `onnx/q3_o17sln/` | **248,054**＋1,202,585,600（本行原写"同上"，10-30 更正） | ir8＋opset17＋SlnMS 换域。＋855 B 全部来自换域，与 opset 无关：只换域不改 opset 的 `q3_slnms` 同为 248,054，只改 ir 的 `q3_ir10` 仍是 247,199 ⇒ opset 21→17 自身 0 B |
| 改写变体 | `compile/fmodel/q3_ir10`、`q3_slnms` | 同上 | 拆开验证两个改写各自的必要性 |
| **改写定稿** | `onnx/q3_v1/` | 247,970＋**1,513,750,528** | 去 Transpose，唯一进编译的那份 |
| **10-19 交付线件** | `compile/fmodel/q3i_{exported,o17sln,v1}`＋`compile/jr_path/Q3I/Q3I-decoder_{parsed,optimized,quantized,adapted,ZG}.{json,raw}` | ONNX 247,199／248,054／247,970；`_ZG` 2,679,170＋1,603,399,624 | **10-30 查证结案：交付线这三份 ONNX＝上面探索线那三件的字节**（md5 逐一对平 `07caf0e3`／`05cb26f2`／`0665e4e0`；反证＝把 `meta_variant.py --src` 指到新导出的 `q3i_fp16_cuda` 跑出 249,286／`8ee9e248`，与盘上那份对不上）⇒ **它编的不是自己新导出的那张图**。后果：`Q3I-decoder_ZG.raw`＝`Q3V1-decoder_ZG.raw`＝板上那颗 `1081ddd7…`（10-30 两边重算 md5，逐字相同），两颗 `_ZG.json` 只差 offset 202 起的网络名 `Q3I-decoder` vs `Q3V1-decoder`——与第 7 节 `:271`～`:272` 那条"同一张图、只差名字串"同源，那边是板上对留档，这边是两条线之间。真正从没进过 icraft 的只有新导出的 `q3i_fp16_cuda`（多的 1,232 B＝第 2 节末条那个 KV 轴符号化）。混搭细节：`q3i_exported` 是 10-12 那份的**字节副本**（同 md5、inode 不同、nlink=1），`q3i_o17sln/model.onnx.data` 则是从 `q3i_fp16_cuda` 硬链接来的（inode `1688849860359123`／nlink=2）⇒ **图形来自旧导出、数据壳来自新导出**；两份权重实测 6 刀＋6 刀 0 处不等，所以这个混搭不影响数值。取证脚本＝`S:\work\WCIS\workenv\q3i_probe\`（5 件，按仓规不入库）。**10-30 出库（她"砍掉"）**：本行与上行那两份 1.6 GB 的 `Q3I-decoder_ZG.raw`（`compile\jr_path\Q3I\` ＋ `repos\...\board\`）已 `send2trash` 进回收站，枚举到两条、`DeletedFrom` 逐字对得上；**盘上现在只剩一份物理 1.6 GB＝正本 `compile\jr_path\Q3V1\Q3V1-decoder_ZG.raw`（md5 全等）＋板上那颗**，3,206,799,248 B（≈2.99 GiB）要等回收站清空才回。两颗 `Q3I-decoder_ZG.json` 都留着（库里那份是 git 跟踪件）。⇒ 以后要按 `Q3I` 这名重推板：**把正本 raw 复制改名即可，不用重编**——`_ZG.json` 里没有任何指向 `.raw` 的路径字段（直读只有 `params_md5`＝`1081ddd7…` 与 `params_bytes`＝1 603 399 624），运行时按文件名对上。`repos\...\MANIFEST.md5` 同笔重算 **45→44 行**（`md5sum -c`＝44 OK／0 FAILED） |
| 编译中间件 | `icraft/Q3V1/Q3V1-decoder_{parsed,optimized,quantized,adapted}.*` | raw 各 1.53 GB | PC 前向吃 `adapted` |
| **上板件** | `icraft/Q3V1/Q3V1-decoder_ZG.{json,raw}` | 2,679,171＋**1,603,399,624** | 10-14 起＝打过 mask 补丁的那份，`params_md5 = 1081ddd7…` 与 raw 实算 md5 相等（A）；**10-14 18:09–18:13 已拷板**（本行原写"从没拷过板"，10-28 更正）：板上 `/root/qwen3/jr_path/decode/`＝json `28ccd902b8a1edfad07407c893473f63`／raw `1081ddd7222dc2cadd5bee8dc27b781f`，两侧 md5 逐字相同，raw 用 267 s／6.1 MB/s；同批还推了 `/root/qwen3/config/qwen3/tokenizer.json`＝11 418 266 B／`7875a23badb888afee2869c78859aa81`。读数出处＝`S:\apps\llm\agentwork\qwen3\board\put.log:5`／`:8`／`:11` 与 `:19`～`:24`（本行原引 `workenv\安装Qwen-0.6b_2026-10-28_11-14.md:173004`／`:173007`／`:173010`，10-28 复核那三行是空行、引用作废）。**这颗件 10-14 在板上过过 `apply`（75.1 s，见上面预检块），但生成从未执行**——板上没有 Qwen3 驱动 ⇒ "件内数值在 NPU 上对不对"仍未答 |
| 上板件原件（未补丁） | `icraft/Q3V1/Q3V1-decoder_ZG.unpatched.{json,raw}` | 2,676,018＋1,603,398,820 | 回退用，`params_md5 = ec18ebf7…` |
| **mask 补丁本体** | `patch_mask0.py`＋`compile/jr_path/Q3V1/Q3V1-decoder_adapted_mask0zg.json`（2,548,383，上板编译入口）、`…_mask0.json`（2,548,365，本机语义验证）＋`compile/Q3V1_gen_mask0zg.toml`、`Q3V1_gen_mask0.toml` | — | 第 4b 节；`patch_mask0.py <in.json> <out.json> [compile_target]` |
| 补丁验收件 | `compile/gen_mask0/`（36 步链，6.0 GB）、`compile/gen_mask0_rss.csv`、`probe_mask0/`＋`compile/probe_mask0zg/`（各 171 MB 单步 dump） | — | 25/25 逐 id 全等；`host_gen.py --extra-dump` 是这轮加的开关 |
| 板端 host 算子取证 | `so_probe.py`、`so_ctx.py`（在校机对 `D:\yzd\WCIS\crosswin\.kitwork\onchip\rootfs` 的 aarch64 库跑） | — | 结论见 4b 的 `@zhuget` 那条（B 级：字节扫符号） |
| prefill | `icraft/Q3V1/Q3V1-prefill_parsed.*` | 1,505,119,988＋1,286,056 | 只 parse，未编 |
| 旧 43 步链 | `pc_runs/gen_chat32/` | 7.2 GB | **喂入序是坏驱动产出的**，只当历史证据 |
| 修正链 | `compile/gen_fixed35/` | 5.9 GB | 修好驱动后重跑，35 步，与 HF 同口径 35/35 |
| 基准 logits | `numeric/logits_teach43/` | 26 MB | 43×151936 fp32，teacher-force |
| 口径实验 | `compile/var_p{0,1}_b{0,1}.json`、`fix_{chain,p1b1,p0b0}.json`、`fix_texts.txt`、`hf_chain32.log`、`ref_teach43.json` | — | 2×2 与修正后的三条自由生成 |
| **Qwen2 对照** | `compile/q2_chat/`、`compile/q2_chat_text.txt`、`compile/q2_chat_rss.csv` | **540 MB**（`du -sh` 实算） | 厂商件＋厂商口径出"我是来自阿里云的超大规模语言模型，我叫通义千问"（第 6 节判定 4） |
| 内存曲线 | `compile/rss_chat32.csv`、`gen_fixed35_rss.csv` | 140 KB＋ | 1 s 采样 |
| 板侧脚本 | `board/{mem_watch.sh,qwen_board.py,sercap.ps1,ser_probe.ps1}` | — | 见第 7 节 |
| **厂商 Qwen2-0.5B 全套** | `qwen2_vendor/`（＝`S:\apps\llm\agentwork\qwen` 的 junction） | `repo/jr_path/decode/` 7.4 GB、`repo/fmodel/` 1.27 GB | 厂商 clone（未改）＋本机编译的五段＋`patch/` 三处死 import 补丁＋`qwen_bisect.{py,sh}`＋`board-qwen-deploy.py`。**板上那份就是从这里推的**；`Qwen2-0.5B-decoder_adapted.*` 也能被本机 Host 前向吃（10-12 用来做零 pad 对照） |
| 解码工具 | `decode_ids.py` | — | 用 `compile/fmodel/T0/tokenizer.json`（Qwen3 词表）把 `steps.json`／`tokens.txt` 解成文本，含 run-length 一眼看出一字循环 |
| **零槽口径扫描** | `padfix.py`＋`compile/padfix_chat24.json`、`padfix_mask0_testset.json`、`padfix_vend_ts.json` | 30 KB | 校机跑的 HF fp32 替身，五种 slot-0 口径（第 6 节判定 5）；脚本本体在 `D:\yzd\WCIS\MOD\qwen3\padfix.py` |
| **mask 补丁点定位** | `dump_mask.py`＋`compile/maskprobe/`、`masktrace.py`、`fmtrace.py` | 2 KB dump＋feed | `dump_mask.py` 单步 dump 出 v_id 180 的真实值 `[0,0,-inf×510]`；`masktrace.py` 看 ONNX 侧、`fmtrace.py` 看 icraft IR 侧（`--trace 180`／`--op CausalMask`） |
| 日志 | `logs/`、`compile/*.log` | — | 导出／改写／编译／前向的原始输出 |
| 探路废件 | `archive/` | 30 MB＋ | probe-*、analyze*、bwtest.bin、被作废的 rss_sample.ps1 等；只归档不删 |

`onnx/`、`icraft/`、`pc_runs/`、`numeric/` 是目录 junction，指回 `compile/` 下的原位；删 junction 不影响原件，也不省盘（本来就是一份数据）。

## 9. 还没做／下一步

1. **零 pad 的绕行方案：候选 d 已做完并出件（10-14，第 4b 节）**——手改 adapted IR 插 Slice/Slice/Concat 关掉 slot 0，
   只重跑 `generate`，不用重导 ONNX、不用动 `.raw`。候选 b（驱动侧填零槽）、候选 c（prefill 吞 prompt）已作废，
   候选 a（0 基重导出）与 d 数值等价但要重编五段，已无必要。
   剩下的不是"能不能编"，是**能不能上板验**：件内数值只有 ZG 后端跑起来才算数，而点火要先过第 7 节的位流门禁。
2. ZG 件的数值仍**一点没验**——本机没有能前向的 ZG 模拟器（`mock://zg330` 不前向，`run_llm.exe` 是 WL170 件）。
   **10-14 补一条**：这条现在有了对照基准——厂商 Qwen2 的 `_ZG` 件已在板上跑通并出通顺中文（第 7 节），
   所以"ZG 件能不能跑"不再是问题，剩下的只是"**我方这颗 Qwen3 `_ZG` 件的数值对不对**"。
3. 板子那一步：**10-14 已跑通（厂商 Qwen2）**，上面那句"剩下的唯一待决是**要不要点火**"已经她当场批准并执行完。
   现在这条待决换成**Qwen3 上板要不要做**，它有四件事要先解决。**10-28 按板上实况与库内件重核：①②③已做完，只剩④与点火**——
   ① 那颗 1.6 GB `_ZG.raw` **10-14 18:09–18:13（板钟）已拷上板**（本行原写"从没拷过板"，10-28 更正；三件两侧 md5 逐字相同，推完 eMMC 26 G 盘剩 6.8 G 空闲＝73 % 已用）；
   ② 驱动**已按 Qwen3 结构改完**＝本库 `patch/q3_infer_board.py`（`:52` 28 层、`:53` 8 KV head、`:54` head_dim 128、`:39`～`:41` 指板上 `/root/qwen3/` 那三件、`:31` 复用板上 `/root/qwen2/modelzoo_utils`），10-15 17:06 写完，**但没推上板、也没跑过**；
   ③ **喂料宽度那处同一件里已补成 3 元素**（`:173` `[[1, i+1, 0]]`、`:185` `[[1, seq_pos, 0]]`，`#196` 那条对两颗件都成立），
   ④ 起跑前先看 `CmaFree`（Qwen3 的 KV 是 Qwen2 的 ~3.7 倍，一轮推理吃的 CMA 只会更多，而板上 `CmaTotal` 只有 262,144 kB）。
   **10-28 补一条实测**：这条已经从推测变成有数的硬约束——s3 那一档 5 s 心跳记到 **`CmaFree` 最低 80 kB**（lazy-load 窗口，130,532 → 220 → 80 → 18:40:10 回到 128,656）与
   **`MemAvailable` 最低 5,020 kB**（apply 窗口 18:42:09，同窗 `CmaFree` 最低 2,440）。⇒ **四档预检只走到 `apply` 就已经把 CMA 池和整机内存同时压到个位数 MB 级**，
   而板上空载时 `CmaFree` 本来就只有约 130 MB（`CmaTotal` 262,144 kB，池子一半在空载时就不 free）——**点火那一窗必须先读 `CmaFree`、确认另一条会话没在跑，并预备断电**；
   这 130 MB 的归属当场没取证（见 §7 预检块那条 D），不能当成运行时的固有开销来算预算。
   **另一条会话的直播链同窗在跑时会占 CMA ⇒ 两条不能同时点。**
4. opset 21 只去 Transpose 能否过 parse（决定"图必须 ≤17"还是"必须改结构"）——任务 #8 还挂着。
5. 上板驱动侧的内存账：Qwen3 驱动三份 KV 拷贝 ≈170 MiB＋import 46.5 MiB＋tokenizer ≈30 MiB ＝ **≈245 MiB 匿名页**
   （Qwen2 那份 ≈95 MiB）；板子 993 MB 里 256 MB 是 CMA，`MemAvailable` 不能当起跑门禁。详见 wiki `qwen3-0.6b.md` 第七节。
   **10-14 实测校准**：Qwen2 真跑一轮 `MemAvailable` 最低 386,740 kB、零 OOM、进程 RSS 峰值 ≈743 MB（`VmHWM` 673 MB）
   ⇒ 这套匿名页账成立、`lazyLoadParamsFromFile` 确实不整颗驻 RAM；**但"ZG 运行时吃 CMA ≈122 MB"是这套账里原本没有的一项（D→A）**，
   以后上板门禁要从"看 `MemAvailable`"改成"先看 `CmaFree`"。
6. 真实凭据（板子 root 口令）只写在 `S:\apps\llm\agentwork\board-ssh.py`／`board-put.py` 一处，别外传、别复制进文档。
   **10-28 补一条本库的例外**：`board/qwen_board.py` 内嵌同一串明文，按她"原样入库"的批准留在库里
   （扫描口径＝取 `board-ssh.py` 单点那串做子串比对，本次入库 18 件命中 1 件、其余 17 件零命中；**第 19 件 `patch/q3_infer_board.py` 单独再扫＝零命中**，它不内嵌口令）。
   该值的性质按 D-54 已改判＝厂商公开出厂默认口令、与 wiki 单点那条同值，不另开记录处；
   代价写在这儿备查：这块板一旦改成非默认口令，该件当场作废。口令值本文不写。
