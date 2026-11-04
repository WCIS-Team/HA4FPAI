# lang/vertify —— 声纹验证（CAM++）：选型与现状

> **参数全录在 [para.md](para.md)**（sherpa-onnx v2.0.1 源码级：4 个实例参数、判决级 `threshold`、
> fbank 固定面、运行时纪律、int8 三档实测）。本页只管**选型**——用 fp32 还是本仓量化 int8。

**一句话**：声纹（说话人验证）档在 `lang/server` **还没实现**——`Chain.STAGES`（`server/core.py:111`）
没有 `spk`/`verify` 这一级，`backends/__init__.py` 的 `REGISTRY` 里也没有。本目录只落**模型与参数面**
（`models/`＋`para.md`），接进链路时按 `para.md` §七 的契约做。

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
├─ README.md        本页（选型＋现状）
├─ para.md          参数全录（源码级，含 int8 三档实测）
├─ models/          模型（fp32＋本仓 int8＋官方 test_wavs＋checksum.txt；本机副本不入库）
└─ （接进链路后）档位实现在 lang/server/backends/
```

## 六、相关

- 参数面与契约：`para.md`（§七 接进 `lang/server` 的建议契约）
- 链路现状：`lang/server/core.py`（`STAGES` 无 `spk`）、`lang/server/backends/__init__.py`（`REGISTRY` 无 `verify`）
- 同类参数面：`../kws/para.md`、`../vad/para.md`、`../asr/para.md`、`../tts/para.md`
- 复跑入口：`workenv/vertify/`（`quant.py` 出 int8、`verify_kws.py`/`probe_per_keyword.py` 出读数）
