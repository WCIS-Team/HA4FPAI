# lang/split —— TTS 前的切分器（断句，不分词）·三档可切换

2026-11-04 她定两件事，本目录按这两条落地：

1. **「分词器」的真实语义是把 LLM 输出的长句切短后再进 TTS，以压 TTS 的峰值 RSS**
   （`D-1793754986`）——不是做词语切分；
2. **链路切到新档，且「超过 20 字就强制截断」**；原来的两档（`punct`／`window`）也搬进本目录，
   **做成可切换的三档**，参数落一份 YAML（`lang/config/split.yaml`）。

现状：`lang/config/server.yaml` 的 `chain.tokenizer` ＝ **`tts_split`**；三档都在 `lang/split/`；
`lang/server/backends/text.py` 已降级成薄壳（只把三档转出去），**不再是实现处**。

## 一、为什么这里没有 jieba

「分词」和「断句」是两件不同的事：

| | 要解决的问题 | 靠什么 | 代价 |
|---|---|---|---|
| 分词（jieba 等） | 词语边界（给检索、标注、NLU 用） | 词典／统计模型 | 常驻词典一份；给不出「在哪断气」 |
| **断句（本目录）** | 长文本整段塞进 TTS 会撞峰值 RSS／单段字数上限 | **标点 优先 ＋ 字数上限** | 零依赖，额外内存只有 Python 运行时那点 |

TTS 只关心「在哪停一下、每段多长」，不关心词语边界。所以这里不加载词典也不加载模型。

## 二、三档（`lang/config/split.yaml` 里切）

| 档名 | 实现 | 怎么切 | 块长 |
|---|---|---|---|
| **`tts_split`**（默认） | `tts_split.py` | 句末优先 → 退分句符 → 硬断；护住引号/ASCII 记号 | **严格 ≤ 20 字，无例外**（她 2026-11-04 定） |
| `punct` | `legacy_tiers.py` | 句末必切；逗号只在攒够 `max_chars` 才切；超长块二次硬断 | 也不超上限，但**断点不看语义**，常把一句话切成半句 |
| `window` | `legacy_tiers.py` | 固定字数硬切，不认任何标点 | ≤ `width`，语流最差 |

后两档是从 `lang/server/backends/text.py` **逐行照搬**过来的（行为一点没改）——
`server/tests/smoke.py` 的 `t_tokenizer()` 钉的就是原行为。

**换档只需改一行**（`lang/config/server.yaml`）：

```yaml
chain:
  tokenizer: tts_split     # 或 punct / window（三档都已在 server/backends/__init__.py 登记）
```

## 三、文件

| 文件 | 作用 |
|---|---|
| `tts_split.py` | 新档：`split_for_tts()` ＋ `ServerStage`（server 分词器档的壳） |
| `legacy_tiers.py` | 原来的 `Punct`／`Window`（逐行照搬，行为未改） |
| `cli.py` | 命令行：拿真实文本试切，定 `max_chars` 用 |
| `selftest.py` | 无依赖自检：严格/宽松两套口径 ＋ 400 条伪随机语料的不变量 ＋ 三档契约 |
| `__init__.py` | `from split import split_for_tts, Punct, Window` |

## 四、怎么跑

```text
# 直接给文本（默认 20 字严格上限）
python -X utf8 lang/split/cli.py "第一句话。第二句很长很长……"

# 宽松模式：允许引号/长英文词/过短末块外溢几个字符
python -X utf8 lang/split/cli.py --max-chars 40 --loose --file in.txt

# 一块一行直接喂 TTS / 只看统计 / 机器可读
python -X utf8 lang/split/cli.py --file in.txt --raw
python -X utf8 lang/split/cli.py --file in.txt --stats
python -X utf8 lang/split/cli.py --file in.txt --json

# 自检（任意 cwd）
python -X utf8 lang/split/selftest.py
```

```python
import sys; sys.path.insert(0, "repos/HA4FPAI/lang/split")
from tts_split import split_for_tts
split_for_tts("……长文本……")                    # 默认 20 字严格上限
split_for_tts(text, max_chars=40, strict=False)  # 宽松口径
```

自检现状（2026-11-04 实测）：`selftest.py` **13908 ok / 0 fail**（本机 py3.13.11 **与板上 py3.8.10 各跑一遍**）；
`lang/server/tests/smoke.py` **全绿**（当日 49/49——计数会随并发会话新增用例变动，别当常量引用）。

## 五、参数（`lang/config/split.yaml` → `tokenizer_profiles.tts_split`）

| 参数 | 默认 | 说明 |
|---|---|---|
| `max_chars` | `20` | 每块字数上限。严格模式下**原始字符数**不超过它（含空白） |
| `strict` | `1` | `1`＝超过上限就强制切、**无例外**；`0`＝宽松，允许下面三处外溢 |
| `prefer` | `sentence` | `sentence`＝句末优先，窗口内没句末才退分句符；`fill`＝①句末②分句合成一遍，纯按位置取最靠后（块数更少，代价是可能切在逗号上） |
| `hard_cut` | `1` | `1`＝没标点就在上限处硬断；`0`＝越过上限找下一个标点（**只在 `strict: 0` 下有效**） |
| `min_tail` | `4` | 末块短于此值：并得进前一块就并，并不进就**把边界往左挪**（`20+1` → `17+4`），仍守住上限；`0`＝不改 |

调参口径：`max_chars` 设在 TTS 单段上限以内，**Edge 每句约 3 s**（`lang/tts/models/edge/README.md`）。
想压总时长优先动 `prefer: fill`／`min_tail`，别放大 `max_chars`（块越大峰值越高，正是要避免的）。

## 六、切口规则（这些是**刻意行为**，不是 bug）

1. **句末优先**：窗口（前 `max_chars` 字）里取最靠后的句末符 `。！？!?；;…` 或换行；`.` 只在其后
   不是字母数字时才算（`3.14`、`e.g` 不算）。
2. **再退分句符**：`，,、：`、空格／全角空格／连接号、右引号右括号。ASCII `:` 不算切点——它长在
   `https://`、`12:30`、`18091:端口` 里；连接号 `-` 同理。
3. **最后硬断**：窗口里没有合法标点时在上限处切，但会护住四样东西：
   - ASCII 记号（英文词／`3.14159`／`1,000,000`／`https://…`）不许劈开，先退到记号开头
     （**宽松模式**下记号比上限还长就整块保留；**严格模式**下不例外，照切）；
   - 右引号右括号（`。」`、`。）`）跟着左块走；
   - 左块不以开引号收尾（`他说：「` 这类不拆开）；
   - 不从分句符前面切。
4. **宽松模式的三处外溢**（`strict: 0`）：收尾标点外溢（`……` 与成对右引号并入左块）、超长
   ASCII 记号整块保留、过短末块合并（不看余量）。严格模式下这三处都让位给上限。
5. **严格模式的代价（她选的：长度优先）**：右引号 `」』”` 或分句符 `，` 正好落在第 20 字之后时，
   只能让下一块以它起头——并回左块就要超 1 个字符。要消除这个现象就把 `strict` 关掉。

## 七、与 `lang/server` 的关系（**已接线，别再改回两份实现**）

| 接线点 | 现在是什么 |
|---|---|
| 默认档 | `lang/config/server.yaml` → `chain.tokenizer: tts_split` |
| 档位注册表 | `lang/server/backends/__init__.py` → `"tokenizer": {"tts_split", "punct", "window"}` |
| 实现处 | **只有 `lang/split/`**；`lang/server/backends/text.py` 是薄壳（转出三个名字，别在里面加代码） |
| 参数 | `lang/config/split.yaml` 的 `tokenizer_profiles.{tts_split,punct,window}` |
| 契约 | `name` ＋ `__init__(cfg, profile, shared)` ＋ `run(text)→[chunk]`（`server/core.py` 头注） |

判据（2026-11-04 实测）：`selftest.py` 13901/0、`server/tests/smoke.py` 42/42（本机 py3.13.11，
`python -X utf8`）。smoke 里新增的四条就是这次接线：新档严格 ≤20、内容守恒、三档登记、`split.yaml` 生效。

## 八、`max_chars = 20` 是怎么算出来的（实测，不是拍的）

现役 TTS 是 **`edge`**（板上 `/health` 实读：`tokenizer: punct`、`tts: edge`）。本机 py3.13 用
edge-tts 7.3.1 **直接量**它的单段上限（探针：`workenv/tts-limit-probe/`，2026-11-04）：

| 送进去字数 | 音频字节 | 音频时长 | 墙钟 | 判定 |
|---:|---:|---:|---:|---|
| 20 | 28,512 B | 4.8 s | 1.2–2.3 s | 完整 |
| 200 | 260,928 B | 43.5 s | 3.2 s | 完整 |
| 800 | 1,018,656 B | 169.8 s | 9.3 s | 完整 |
| 1400 | 1,784,304 B | 297.4 s | 20.4 s | 完整 |
| 2800 | 3,565,584 B | 594.3 s | 22.5 s | 完整 |
| **3000** | **3,600,000 B** | **600.0 s** | 26.9 s | **尾巴被静默丢掉** |
| 3200 / 6400 | 3,600,000 B | 600.0 s | 31–55 s | 同样静默丢字（不报错） |

⇒ **单块（一次合成调用）音频上限 600 s ≈ 2,827 汉字**；本仓实测 **每字 ≈ 0.21 s 音频**
（20 字→4.8 s、800 字→169.8 s，线性）。`edge-tts` 库自己按 4096 字节切请求，但这个 600 s
是**单次 `Communicate` 的累计音频上限**，超出部分**没有异常、直接丢**——这才是真正的硬天花板。

**校准式**（换 TTS 引擎时按同一式子重算）：

```
max_chars = min(引擎单块上限 × 安全系数, 峰值 RSS / 延迟预算允许的字数)
```

| 引擎 | 实测锚点 | 对 `max_chars` 的含义 |
|---|---|---|
| `edge`（现役） | 600 s ≈ 2827 字；每字 0.21 s；每块一次往返 1.5–2.3 s | 硬上限 2827；20 字只占 **0.7%** |
| `sherpa`/matcha（本地档，模型板上已失） | 20 音素 **1730 ms**、60 音素 4504 ms、150 音素 12211 ms（板上 4×A53/2 线程） | 20 字≈20 音素 ⇒ 落在 **1.7 s 拐点** |
| piper/VITS 系 | 峰值 **391–755 MB**，993 MB 板上被 OOM 杀过 12 次 | 拆块的**初衷**：块越长峰值越高 |
| `ekho` / `http` 档 | 自带 `--max-segment-chars`（0＝按 ring 推）与流式分句 | 不吃我们的块长，按自己的节奏分段 |
| `sine` | 自检档，时长随字数走 | 无上限概念 |

⇒ `min(2827 × 0.5, 20 字≈1.7 s) = **20**`。所以 20 不是"保守拍的"，而是**本地档延迟拐点**
决定的数；相对 edge 的硬上限有两百多倍余量。

**防呆**：代码里留了 `MAX_CHARS_TRIPWIRE = 1000`——一旦有人把 `max_chars` 抬过这条线，
`ServerStage` 会往 stderr 打一句带实测依据的告警（**不硬失败**，上板换档时不许让配置把人拦住）。
判据在 `selftest.py` 的 `case_max_chars_tripwire()`。

## 九、还没做

- **流式增量切**：现在是「整段回复切完再合成」。LLM 边出字边切要另加 `iter_chunks`（句末一到就吐块），
  可以拿 Edge 的 3 s/句做流水线重叠。
- **按 TTS 的单段上限反推默认值**：`max_chars` 目前是人给的 20；接上具体 TTS 后应按它的上限校准。