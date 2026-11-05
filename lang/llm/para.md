# lang/llm 可调参数全录（三颗 NPU 迁移件）

**口径**：本页只收**实测或源码直读过**的参数（默认值带出处），没实测过的不编数。三颗模型都是
**ids-in / ids-out 的 NPU 件**（板端只吃 token id、回 id），所以参数面分两层——**编译期**（五段
`icraft compile` 的 toml，决定图的形状）与**运行期**（板端驱动的可调项）。选型见 [`README.md`](README.md)。

> **本机 ≠ 板端**：板端速度（s/步、tok/s）全部是 192.168.1.113（FMQL30TAI676H／ZG330）实测；
> 编译只能在本机 Windows CLI（`C:\Icraft\CLI v3.39.0`），**绝不在板上编译**（993 MB 会被挤死）。

---

## 一、ERNIE-4.5-0.3B-PT4icraft430tai（历史上板验证件）

上板通过记录不代表当前 lang 链正在使用该模型。当前是否启用由 `chain.llm` 与 npuserv 的 `/status` 决定。

### lang 服务实际配置键

`config/llm.yaml` 对应 `server/backends/llm.py:NpuServ.__init__`，支持 `base_url`（默认 `http://127.0.0.1:18080`）、`model`（必填，空值表示该档不可用）、`max_new`（128）、`timeout`（900 秒）、`wait_s`（10 秒）。可分别通过 `NPUSERV_URL`、`NPUSERV_MODEL`、`NPUSERV_MAX_NEW`、`NPUSERV_TIMEOUT`、`NPUSERV_WAIT_S` 覆盖。后端不读取 `url`、`max_new_tokens` 或 `num_threads`。

### 编译期（`compile/E41_parse.toml`，五段 icraft）

| 参数 | 值 | 说明 |
|---|---|---|
| `target` | **`zhuge`** | **只能写 `zhuge`**（写 `ZG` 会静默报错埋进度条后，同 `tts/README.md` §3.1 那条坑） |
| `qdtype` | `fp16` | 现役量化档（权重全 BF16 导出，量化面未做） |
| `max_prompt_length` | 32 | `models/ERNIE-4.5-0.3B-PT4icraft430tai/compile/E41_parse.toml:7`，编译期设置 |
| `max_tokens_length` | 512 | 同一 TOML 第 8 行，编译期设置；运行期生成长度另由驱动控制 |

### 运行期（`board/e41_chatd.py` / `e41_chat_console.py`）

| 参数 | 默认 | 说明 |
|---|---|---|
| `E41_PADFIX` | **1（开）** | **首步 slot1→slot0 复制**（token 0 连喂两次＋每步槽 1 复制进槽 0）。**板上 A/B 后定为默认**：关→同一提示退化成时间戳循环；开→55 token 通顺到 eos；速度无可测差（0.42 vs 0.42 s/步）。**必须开** |
| `--max_new`（控制台 `/max N`） | 96 | 生成长度上限。**默认 96 容易撞**——这颗模型遇"1+1 等于几"也会展开成长篇，撞限以 `limit` 截断（不是 eos）；要它说完就 `/max 160` |
| `E41_STOP` | 2 | 板端驱动 stop 档（默认 2，现役） |
| `E41STEP` | 0..3 | 分步探针（`e41_probe.py`），量 `VmRSS/VmHWM`＋`MemAvailable/CmaFree`＋logic/phy 段 |
| `E41P_MODE` | `wall`\|`prof` | 六段计时探针（`e41_prof.py`） |
| 空闲自退 | 300 s | 守护在最后一个客户端断开后 300 s 自退 |
| `dt` 口径 | 0.42 s/步 | **驱动 `dt`＝`forward`＋`waitForReady`＋`reset` 之后、所有 `Tensor.read` 之前**；不含读回与 argmax。轮级墙钟 0.47 s/步，差 46.2 ms＝读回尾巴 |
| 读回 | 整块 36 MiB | 现役；**窄读（只读本步新槽）净省 26.07 ms＝5.6 %**（成立未实装，要她点头才上板） |

### 三条为迁就 parser 付的债（都是结构常量，别当可调）

1. **KV head 2→8 平铺**：parser 只认得 Qwen3 那套几何 ⇒ 每颗 cache `[1,8,512,128]` fp16＝1,048,576 B（原生 2 头的 4 倍）。
2. **补 4 条没人消费的 cache 声明**（38→42）：每步多喂 4 MiB 死数据，驱动必须喂满 42 路。
3. **RoPE 配对改数据不改属性**：只能按 `PERM=[0,2,…,127,1,3,…]` 置换 qkv 权重的 head_dim 轴。

### 板侧分词（不依赖 tokenizers）

**纯标准库 `e41_tok`**（`board/e41_tok.py`＋`e41_tok.pack`，3,302,740 B），板上不解析 11 MB 的
tokenizer.json。**为什么不用 tokenizers**：板上 0.13.3 读不了这颗 json（新版 `ignore_merges` 字段）。

```python
import e41_tok
tk = e41_tok.load()               # 或 load("/mnt/data/ernie/e41_tok.pack")
ids = tk.encode(prompt_text); text = tk.decode(gen_ids)
```

---

## 二、Qwen3-0.6B（Qwen3-0.6B-Instruct，待驱动上板）

### 板侧分词依赖（**必须 ≥ 0.15，实测 0.20.3**）

板上 `tokenizers 0.13.3` 加载即报 `data did not match any variant of untagged enum ModelWrapper`
（同 ERNIE 当年那条病），根因＝太旧。升 0.20.3（2026-11-04 板上实测）直接可读，7 组样例 ids 与 PC
`tokenizers 0.23.3` 逐字相同。安装（板端，root，无网装轮子）：

```bash
python3 -m pip install --no-index --no-deps tokenizers-0.20.3-cp38-cp38-manylinux_2_17_aarch64.manylinux2014_aarch64.whl
# 轮子正本：S:\work\WCIS\workenv\tokwhl\tokenizers-0.20.3-cp38-cp38-manylinux_2_17_aarch64.manylinux2014_aarch64.whl
```

### 上板驱动两处必改（`patch/` 已给，未验）

| 改动 | 位置 | 说明 |
|---|---|---|
| `token_dynamic_fm` 从 2 个值补成 3 个 | 驱动 `:364`（prompt 循环）、`:416`（decode 步） | **ZG 件把 `token_dynamic_fm` 声明成 fp32[1,3]＝12 B，厂商 Qwen2 驱动只喂 8 B**——本机 Host 后端不校验输入宽度，PC 上永远看不见；板上真跑撞 `hardop.cpp:46`。第 3 个元素图里不读（`[1,1,1]` 与 `[1,1,0]` logits 逐位相同） |
| `n_kv_heads=8`／`head_dim=128` | 驱动 `:222`／`:223` | 与 Qwen3 结构一致（28 层） |

### 已知（未出 token）

四档预检 `s1/s2/s3` 全 `exit=0`，`s3` 的 `apply` 实测 **75.1 s**、`CmaFree` 最低 **80 kB**（lazy-load 窗口）、
`MemAvailable` 最低 **5,020 kB**（apply 窗口）⇒ 这颗 `_ZG` 件在 ZG330 上完成过 NPU 编程，但生成那一步从未执行。

---

## 三、Qwen2-0.5B（厂商 ZG330 demo，参考件）

### 编译期（`Qwen2-0.5B_decoder.toml`）

| 参数 | 值 | 说明 |
|---|---|---|
| `target` | `zhuge` | 厂商 demo 自述适配 iCraft **3.36.1**；我方实际用 **3.39.0**（图上 `_ZG` 件是 3.39.0 产物，**不是厂商 3.36.1 那份**） |
| `max_prompt_length` | 32 | 厂商 toml 值 |
| `max_tokens_length` | 512 | 厂商 toml 值 |
| `qdtype` | `fp16` | 现役量化档 |

### 我方上板改动（3 处 6 行，`patch/` 有逐行 diff）

| 文件 | 改动 | 为什么 |
|---|---|---|
| `board/qwen2_infer_decode_only_axi.py` | 删 `from pyrtutils.calctime_utils import *` 一行 | `calctime_detail` 只在注释＝死 import；不删得在板上装 pandas（993 MB 板） |
| `board/qwen2_infer_decode_only_axi_pad3.py` | 改 2 行：`token_dynamic_fm` 2 值补成 3 值 | 同 Qwen3 那条 12 B 坑 |
| `patch/modelzoo_utils/…` | 删 1＋2 行 `.calctime_utils`/`pandas`/`matplotlib.pyplot` import | 同死 import 因 |

### 板上实测（第五轮点火，10-14；已出库）

`First generated token: 104198`、共 20 token、`Total time: 7.08 s`、**2.83 tok/s**、247.71 ms/token、
CMA 谷底 8,860 kB。**2026-10-28 已从板出库**（`/root/qwen2` 从 1.7 G 降到 496 K）。

### 起跑前硬门禁（三颗通用）

1. **位流含 NPU**：`live.log` 出现 `Device Name: zg330aiu`（NPU 是 PS→PL 唯一 AXI 端点，位流没它时一摸 AXI 窗口整机楔死）。
2. **板子没被别人占**：`ssh deb-h61 'cat /home/user/Desktop/113.txt'` 要是 `off`。
3. **CMA 单一池 256 MiB**：`udma` 开机静态占 128 MiB，只剩约 128 MiB；**不能与视频链同窗**。空载 CMA 已见底也能起（11-03 实测 `CmaFree` 3,968 kB 起跑照通），但先跑短探针。

---

## 四、一行总结与红线

**LLM 的参数面比 KWS/VAD 窄得多**：三颗都是 NPU 迁移件，旋钮基本只有「编译期 toml 的
`target`/`qdtype`/`max_*`」＋「板端驱动的运行期项（`--max_new`、padfix、探针模式）」。
**真正决定能不能出 token 的不是参数，是驱动对上图的输入合同**（`token_dynamic_fm` 12 B、
`n_kv_heads`/`head_dim`、KV 轴序）。

红线（背下来）：

1. `target` 只能写 `zhuge`（写 `ZG` 静默报错）。
2. **绝不在板上编译**（993 MB 会被挤死）；五段 `icraft compile` 只能本机 Windows CLI。
3. **`token_dynamic_fm` 必须喂 3 个值**（fp32[1,3]＝12 B）——厂商 Qwen2 驱动喂 8 B，板上撞 `hardop.cpp:46`，PC 上看不见。
4. **ERNIE 的 `padfix` 必须开**（关→时间戳循环退化）。
5. 板侧分词：ERNIE 用纯标准库 `e41_tok`（0.13.3 读不了其 tokenizer.json）；Qwen3 要 `tokenizers` ≥ 0.15。
6. 三颗件都是 ids-in/ids-out，**分词/解码在主机侧**，板端不吃文本。

---

## 相关文件

| 文件 | 内容 |
|---|---|
| `README.md` | **选型结论**（板上现役＝ERNIE）——本页是参数，README 是选择 |
| `models/ERNIE-4.5-0.3B-PT4icraft430tai/README.md`＋`pipeline.md` | ERNIE 流程正本（13 节）＋全部板端读数 |
| `models/Qwen3-0.6B/README.md`＋`pipeline.md` | Qwen3 交付件 |
| `models/Qwen2/README.md` | 厂商 demo＋我方 6 行改动 |
| `lang/server/backends/llm.py` | 链路里 llm 档位（ids-in/ids-out 需 tokenizer 桥） |
| `lang/config/server.yaml` | `chain.llm`（echo/openai/npuserv） |
| `lang/config/klar.yaml` | 板端 NPU 槽位档（留空＝不接板端推理） |
| `../kws/para.md`、`../vad/para.md` | 同链 KWS/VAD 参数（语义不同，别互抄） |
