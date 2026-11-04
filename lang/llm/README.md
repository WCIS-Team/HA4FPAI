# lang/llm —— LLM（大语言模型）NPU 迁移交付件：索引与选型

2026-11-04 统一目录时按「一组件一目录、模型进 `models/`」重排：三颗 NPU 迁移候选（ERNIE-4.5-0.3B /
Qwen2-0.5B / Qwen3-0.6B）收进 `models/`，本页只管**索引与选型结论**，参数面在 [`para.md`](para.md)。

**参数全录在 [para.md](para.md)**（三颗模型的可调项与板端实测）。
**本页只管选型**——现在板上现役是 **ERNIE-4.5-0.3B-PT4icraft430tai**（门 4 已通，0.42 s/步）。

## 一、选型结论（板上现役）

| 模型 | 参数 | 板上状态 | 速度（实测） | 结论 |
|---|---|---:|---|---|
| **ERNIE-4.5-0.3B-PT4icraft430tai** | 360.7M（0.36B），18 层／hidden 1024／16 Q＋2 KV head／head_dim 128／vocab 103,424 | ✅ **门 4 已通**（11-01：`precheck=True`、逐 token 与 PC 对上、62 步到 eos 无泄漏） | **0.42 s/步＝2.38 token/s**（驱动 `dt` 口径；轮级墙钟 0.47 s/步） | **板上现役**，`padfix` 默认开 |
| **Qwen3-0.6B**（Qwen3-0.6B-Instruct） | 0.6B 档 instruct 系本尊（2504 混合思考版） | ⚠️ 模型件已上板，**驱动未上板、零 token**；四档预检 `apply` 75.1 s 压到 CMA 个位数 MB | 未出 token | **待驱动上板**；`tokenizers` ≥ 0.15（实测 0.20.3） |
| **Qwen2-0.5B**（厂商 ZG330 demo） | 厂商 `AIBS/Qwen2_0.5B_zg3.36.1_axi_demo` | ⚠️ **10-28 已从板出库**（省 1.7 G）；第五轮点火跑通过 | 20 token／7.08 s／**2.83 tok/s**（247.71 ms/token，CMA 谷底 8,860 kB） | 参考件（厂商配方基线），不再现役 |

**要点**：
- **只有 ERNIE 是「板上真跑出 token 且通顺」的现役件**；Qwen3 卡在驱动，Qwen2 已出库。
- Qwen3 的 0.6B 与 ERNIE 的 0.36B 不是同量级，别按名字里的数字直接比——**ERNIE 的 0.36B 是 18 层小头数模型**（360,748,032 参数，全 BF16），Qwen3-0.6B 是 0.6B 档 instruct。
- 三颗都是 **ids-in / ids-out 的 NPU 件**（不是 HF 文本接口）：板端只吃 token id、回 id，**分词/解码在主机侧**（ERNIE 用纯标准库 `e41_tok`，Qwen3 用 `tokenizers`）。

## 二、模型清单（`models/`，本机副本不入库）

`models/` 下三颗各一目录，**体积件被 `repos/.gitignore` 挡在库外**（`*.onnx`、`*.onnx.data`、`*.raw`、
`*.safetensors`、压缩包等）——小件与证据进 git，`_ZG.json`/`_ZG.raw` 那类按体积留本机/校机。

| 目录 | 是什么 | 入库 | 正本在哪 |
|---|---|---|---|
| `models/ERNIE-4.5-0.3B-PT4icraft430tai/` | ERNIE 迁移交付件（**现役**）：`pipeline.md`（流程正本 13 节）＋ `board/`（5 件＋4 份日志）＋ `compile/`（门 1～3 现役脚本 58 件）＋ `eval/`（校机实测读数）＋ `MANIFEST.md5` | 小件是 | 编译根 `S:\apps\llm\agentwork\ernie\compile\`（`E41-decoder_ZG.json/.raw`） |
| `models/Qwen3-0.6B/` | Qwen3-0.6B-Instruct 迁移交付件：`pipeline.md`＋`compile/`（24 件）＋`board/`＋`patch/`＋`ref/`＋`MANIFEST.md5` | 小件是 | `S:\apps\llm\agentwork\qwen3\compile\jr_path\Q3I\` |
| `models/Qwen2/` | 厂商 ZG330 demo 完整工作树＋我方 6 行上板改动：`vendor/`（原样）＋`board/`＋`patch/`＋`ref/`＋`MANIFEST.md5` | 92 件 / 37.48 MiB | `S:\apps\llm\agentwork\qwen\repo\` |

## 三、目录（2026-11-04 重排后）

```
lang/llm/
├─ README.md        本页（索引＋选型）
├─ para.md          参数全录（三颗模型的可调项与板端实测）
├─ models/          模型（三颗各一目录，体积件不入库）
│  ├─ ERNIE-4.5-0.3B-PT4icraft430tai/  现役
│  ├─ Qwen3-0.6B/                       待驱动上板
│  └─ Qwen2/                            参考件（已出库）
└─ npuserv/         （板端 NPU 槽位服务，2026-11-04 起并入 lang/server 管辖）
```

## 四、相关

- 参数面：`para.md`；链路接线：`lang/server/backends/llm.py`、`lang/config/server.yaml` 的 `chain.llm`
- 单模型详情：各 `models/<模型>/README.md`（＋`pipeline.md`）
- 板端登记：`wiki/PS/板端我方代码与产物台账.md`（ERNIE §廿五/廿六/廿八/廿九；Qwen2 §十四/廿一；Qwen3 #208–#213）
- 上板要求与候选对照：`wiki/PL/MOD/llm/LLM上板要求与候选模型对照.md`
- 编译主机路由：`wiki/SOP/交叉编译主机路由SOP.md`
