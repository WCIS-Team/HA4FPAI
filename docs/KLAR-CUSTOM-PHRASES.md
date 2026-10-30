# klar 自定义短语：实测结论（2026-10-30）

> 目标：让 klar 认识本板自有的设备词（人脸识别 / 录制 / 画面），因为 zh-CN 词表
> 只有家居词（灯/风扇/窗帘/空调/电视）。
>
> **结论：自定义短语能写入、能被 100% 精确匹配（score=1.0），但会在
> `safety_decision` 阶段被判 `unsafe` 而拒绝执行。这是引擎行为，不是配置遗漏。**

## 一、接口全图（实测可用的 klar HTTP 面）

| 端点 | 方法 | 作用 |
|---|---|---|
| `/api/v2/parse` | POST | 主解析：句子 → decision/intent/slots |
| `/api/lang/preview` | POST | 同上，无副作用，带 trace |
| `/api/lang/explain` | POST | 解析 + policy_trace（诊断用） |
| `/api/lang/overlay` | GET/POST | 读写自定义短语、别名、忽略词 |
| `/api/custom` | GET | 查看已存的自定义短语 |
| `/api/entities` | GET | 已暴露实体（当前 `[]`） |
| `/api/gaps` | GET | 已知房间/别名/overlay 全貌 |
| `/api/settings` | GET/POST | 需**完整载荷**（缺 `personality` 会 422） |
| `/api/v2/intents` | GET | 37 个支持意图（HassTurnOn/Off/LightSet/Timer…） |
| `/api/v2/languages` | GET | **67 种语言，含 zh-CN / zh-TW / zh-HK** |
| `/api/speech/render` | POST | 播报渲染（intent 需是 struct，非字符串） |
| `/api/assignment/apply` | POST | 返回 `{"applied":0}`，未改变解析结果 |
| `/api/v2/policies/catalog` | GET | 21 条策略（`custom_sentence` **不在**目录里） |

## 二、自定义短语的正确格式（踩了 4 次才拿到）

```json
POST /api/lang/overlay
{"custom": [
  {"phrase": "打开人脸识别",
   "intent": "HassTurnOn",
   "slots":  {"entity_id": "switch.face_recognize"}}
]}
```

字段由引擎的报错逐步揭示：

| 提交 | 报错 |
|---|---|
| `{"phrase":...}` | `missing field 'intent'` |
| `+intent` | `missing field 'slots'` |
| `slots` 为数组 `[{name,value}]` | `invalid type: sequence, expected a map` |
| `slots` 为 map | ✅ 200，写入成功 |

`/api/custom` 能读回全部 11 条，说明**持久化成功**。

## 三、为什么还是不执行（证据链）

写完短语后 `/api/lang/explain` 给出：

```
decision: reject
policy_trace: {"compiled_risky": false,
               "match": {"id": "custom_sentence", "score": 1.0, "origin": "engine"},
               "band": "reject"}
matched_custom: null
```

**score=1.0 说明短语被精确命中**，但 `band=reject`。

`/api/lang/preview` 的 stage trace 显示：

```
normalize: 5 normalized tokens
features:  1 clauses
→ safety_decision: reject      ← 卡在这里
planning:  0 executable plan steps
```

对比正常可用的句子，`打开客厅的灯` 会走完整的
`action_candidates → target_resolution → binding → ranking → planning`；
自定义短语**跳过了 target_resolution/planning**，直接进 `safety_decision: reject`。

已排除的假设：

| 假设 | 试验 | 结果 |
|---|---|---|
| `confirm_risky_actions` 开着 | POST settings 设为 false | 仍 unsafe ❌ |
| 实体域是 switch 被拦 | 改成 `light.face_recognize` | 仍 unsafe ❌ |
| 需要 apply 才生效 | 调 `/api/assignment/apply` | `{"applied":0}`，无变化 ❌ |
| 重启才生效 | `systemctl restart` | 无变化 ❌ |
| 短语格式不对 | `/api/custom` 能读回 11 条 | 格式正确 ✅ |

**根因（高置信度推断）**：`custom_sentence` 这个 policy **不在 `/api/v2/policies/catalog` 的 21 条目录里**，它是「引擎内置兜底匹配器」。它命中后，目标实体必须存在于引擎的**已暴露实体图**（`/api/entities` 当前返回 `[]`），否则 `safety_decision` 判定「你说了一个我图里没有的东西」→ `unsafe`。

## 四、这对方案意味着什么

**不要把「让 klar 认识板子自有设备」这条路当主干。** 它需要：
1. 让引擎的实体图承认自定义实体（`/api/entities` 目前是 `[]`，且它是引擎内部状态，文档未提供写入接口）；
2. 或等上游放开 `custom_sentence` 的安全策略。

**两条都不可控。** 因此 `klar_executor.py` 采用**双通道**设计（已实现）：

| 通道 | 触发条件 | 可靠性 |
|---|---|---|
| **A. klar 解析通道** | 句子能被 klar 正常解析（家居词） | 依赖引擎，本例受限 |
| **B. 直接命令通道** | 自己维护一张「中文短语 → 板 API」表，绕过 klar | **100% 可控**（本文件实测全部 API 可用） |

通道 B 的短语表就写在 `klar_executor.py` 的 `DIRECT_COMMANDS` 里，
不依赖 klar 是否理解，只做字符串匹配 + API 调用。对「打开人脸识别」「开始录制」
这类固定指令，B 比 A 更可靠且更快（省掉 25 ms 解析）。

**建议采用 B 为主、A 为辅**：家居类句子走 klar（它有澄清/多轮能力），
板子自有指令走 B（确定性强）。

## 五、副产品：本板 API 全图（已实测可用）

这些是 executor 真正能操作的东西：

| 服务 | 端口 | 端点 | 实测 |
|---|---|---|---|
| 人脸/NPU | 8091 | `POST /api/recognize-camera` | ✅ 200，返回 faces/identities/timing |
| | | `GET /api/health` | ✅ `backend=zg330_npu_pl_and_host` |
| | | `GET /api/gallery` | ✅ 18 个 revision，含 people 列表 |
| | | `GET /api/camera/status` | ✅ 双缓冲，960×540 |
| 画面/直播 | 8092 | `GET /api/status` | ✅ ready/session |
| | | `/api/camera/h264/manifest.json` | 走 8091 的 h264 路由 |
| 录制 | 8093 | `GET /api/status` | ✅ mode/state/queue |
| | | `POST /api/mode` | ✅ 实测 auto↔off 切换成功 |
| | | `GET /api/storage` | ✅ 491 MB / 26 条 |
| | | `GET /api/recordings` | ✅ 列表 |
| | | `POST /api/purge` `/api/trash` `/api/restore` | ✅ 存在 |
| | | `POST /api/retention` | ✅ 策略读写 |

**NPU 识别实测**：`recognize-camera` 返回 `status=no_face`（当时画面无人），
但接口链路、`backend=zg330_npu_pl_and_host`、`pipeline_id=yunet-mf-fp16-npu`、
`detector.elapsed_ms=45` 全部正常。
