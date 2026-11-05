# lang/vertify —— 声纹验证（CAM++）：选型与现状

> **参数全录在 [para.md](para.md)**（sherpa-onnx v2.0.1 源码级：4 个实例参数、判决级 `threshold`、
> fbank 固定面、运行时纪律、int8 三档实测）。本页只管**选型**——用 fp32 还是本仓量化 int8。

**一句话**：声纹（说话人验证）档 **2026-11-04 已实装进 `lang/server`**，档名 `spk:sherpa`
（`backends/spk.py`），但**默认不入链**（`chain.spk: none`）：只有 `router`（klar NLP）或
`kws_route`（唤醒词的 `verify:` 表）给出 `action=verify` 时才装载，跑完不常驻、闲置
`idle_unload_s`（默认 300 s）自动卸。模型在 `models/`，参数在 `para.md` 与 `config/vertify.yaml`。

## 〇、怎么用（三分钟）

```bash
# 1) 起服务（enroll/verify 不依赖 asr/tts 档）
cd repos/HA4FPAI/lang/server && python -X utf8 app.py --no-warm

# 2) 登记一段（同名可多次累加，越多人声越稳；?replace=1 覆盖）
curl -s -F audio=@me.wav "http://127.0.0.1:18091/api/v1/spk/enroll?name=alice"
# 3) 验证（给 name 是 1:1，不给是 1:N；match 为空＝没过门限 0.5）
curl -s -F audio=@test.wav "http://127.0.0.1:18091/api/v1/spk/verify?name=alice"
# 4) 看/卸
curl -s http://127.0.0.1:18091/api/v1/spk/status
curl -s -X POST http://127.0.0.1:18091/api/v1/spk/unload
```

网页试验台 `web/ggradio` 有「记录声纹」按钮（浏览器麦克风 → WAV → enroll），见那边的 README。
自检：`server/tests/spk_smoke.py`（模型在位 19/19；本机同人 0.777、异人 0.143）。
**登记库**默认 `vertify/library.json`（运行时数据，别提交；路径在 `vertify.yaml` 里可改）。

## 一、选型结论

| 档 | 体积 | 嵌入 vs fp32 余弦 | 7 段验证 | 判别余量 | RTF（1/4 线程） | 结论 |
|---|---|---|---|---|---|---|
| **fp32（官方）** | 27.0 MiB | —（基准） | 7/7 | 同人 0.778–0.860，异人 ≤0.452 | **0.0129 / 0.0094** | ✅ **交付** |
| **dyn8（本仓动态量化）** | 8.2 MiB | **0.946–0.977**（均值 0.967） | 7/7 | 与 fp32 同级：0.50 起零误接受 | **0.2128 / 0.1089**（**16× 慢**） | ✅ 交付（只买体积/常驻） |
| sta8-QUInt8（静态） | 8.3 MiB | **0.265–0.538**（均值 0.450） | 7/7（侥幸） | **塌**：门限 0.30 就 14/14 误接受 | 0.0073（1.8× 快） | ❌ **不可用** |
| sta8-QInt8（静态对称） | ≈8.3 MiB | 0.817–0.882 | 未全量 | 未全量 | 0.2914 | ❌ 不留 |

**⇒ 只有 fp32 与 dyn8（动态量化）两档可用**；静态量化（QUInt8）又快又小但判别余量塌，禁用。

## 二、门限怎么定（声纹唯一真正"调"的旋钮）

判据是**余弦相似度**，`threshold` 取值 0..1。**本仓取 0.5**（与 kws 的 `keywords_threshold` 同一个数，
语义不同、别互相抄）：0.30–0.45 有跨人误接受，0.50 起干净，同人到 0.75 都不掉。

**保留**：3 人 7 段分辨不出 0.5 与 0.6 的差别——这是"选个安全落点"，不是 EER 寻优。
真调门限要用本仓自己的登记库与真实采集音频重扫。

## 三、模型清单（`models/`，本机副本不入库）

模型来源＝**3d-speaker 的中文 CAM++**（ModelScope `iic/speech_campplus_sv_zh-cn_16k-common` 导出成
ONNX 后随 sherpa-onnx 发布页分发）。**上游只发 fp32，没有 int8**；`models/` 里的 int8 是**本仓自己
量化出来的**（`workenv/vertify/quant.py`）。

| 件 | 字节 | 来源 | 入库 |
|---|---|---|---|
| `3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx` | 28,281,138（27.0 MiB） | GitHub release `speaker-recongition-models`，与官方 `checksum.txt` 逐字节一致 | ❌ `*.onnx` 被 `repos/.gitignore:24` 挡 |
| `…int8.onnx`（动态量化 QInt8 权重 / per-channel） | 8,595,741（8.2 MiB） | **本仓量化**（`quant.py`） | ❌ 同上 |
| `test_wavs/*.wav`（14 段，3 人 × `-sr`/`-test-sr`） | 1,923,010（1.83 MiB）合计 | 同一 release 的官方示例音频 | ❌ 本机副本 |
| `checksum.txt` | 3,477 | 官方全表（sha256） | ✅ 文本，可入库 |

**重拉正本**（唯一官方发布页；本机 huggingface.co 返 401，与 `../kws/README.md` 同一条实测）：

```bash
https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx
```

**⚠️ ten-vad 那条坑别带到这来**：这个 release 的 tag 是 `speaker-recongition-models`（拼写如此），
不是 `vad-models`。

## 四、与 kws 相反的那条（最易踩）

| | kws | **vertify（这个档）** |
|---|---|---|
| 重采样 | **不会**（喂 48k 只识别不出来） | **会**（内部 `LinearResample`；48k cos 0.9997，8k 只有 0.820） |
| 门限语义 | 声学概率 | **余弦**（0..1） |
| embedding 维度 | — | **192**（不是 512；512 是 eres2net/ecapa） |

## 五、目录

```
lang/vertify/
├─ README.md        本页（选型＋怎么用）
├─ para.md          参数全录（源码级，含 int8 三档实测）
├─ models/          模型（fp32＋本仓 int8＋官方 test_wavs＋checksum.txt；本机副本不入库）
└─ library.json     声纹登记库（**运行时才有**，默认路径；别提交）
```

档位实现在 `lang/server/backends/spk.py`，参数在 `lang/config/vertify.yaml`。

## 六、相关

- 参数面与契约：`para.md`（§七 是链路接法与端点）
- 链路现状：`../server/core.py`（`STAGES` 含 `spk`；默认 `chain.spk: none`）、
  `../server/backends/spk.py`（档位实现）、`../server/app.py`（`/api/v1/spk/*`）
- 自检：`../server/tests/spk_smoke.py`
- 同类参数面：`../kws/para.md`、`../vad/para.md`、`../asr/para.md`、`../tts/para.md`
- 复跑入口：`workenv/vertify/`（`quant.py` 出 int8、`probe2.py` 出读数）
