# 路线 2 落地：板子自己就是被控设备

> 2026-10-30 实施完成。**8/8 语音命令实测通过。**

## 一、先回答：klar engine 干什么的

**一句话：它把中文句子翻译成"要干什么"的结构化意图，但不执行。**

```
"打开客厅的灯"  →  {intent: "HassTurnOn", entity_id: "light.keting"}
```

在真正的 HA 里，HA 拿到这个意图去按开关。我们没有 HA，所以**执行这一环是空的**——`klar_executor.py` 就是补这一环的。

它**不做**的事：
- ❌ 不识别语音（那是 ASR 的事）
- ❌ 不合成语音（那是 TTS 的事）
- ❌ 不控制设备（那是 executor 的事）
- ❌ 不存实体状态（那是 registry 的事）

它**只做**：中文分词 → 匹配意图 → 解析槽位 → 输出 JSON。25 ms 一次。

## 二、整体链路（全部在本板）

```
  mic 48k ─→ KWS(小克,10300) ─→ VAD(silero) ─→ ASR(10301) ─→ 中文文本
                                                                │
                                    ┌───────────────────────────┴──────────┐
                                    │                                      │
                            通道B: 直连命令表                      通道A: klar (10520)
                            (板子自有指令，0ms)                    (家居词，25ms)
                                    │                                      │
                                    └────────────┬─────────────────────────┘
                                                 ▼
                                        klar_executor.py
                                                 │
                        ┌────────────────────────┼────────────────────────┐
                        ▼                        ▼                        ▼
                  8091 人脸/NPU            8093 录制               8092 画面
                        │
                        ▼
                  TTS(10302) 播报结果
```

## 三、为什么需要双通道（重要）

我试图让 klar 直接认识"人脸识别""录制"这些词，**失败了**，证据链完整（详见 `KLAR-CUSTOM-PHRASES.md`）：

| 步骤 | 结果 |
|---|---|
| `POST /api/lang/overlay` 写入 11 条自定义短语 | ✅ 200，`/api/custom` 全部读回 |
| 引擎匹配 | ✅ `policy_trace: {id:"custom_sentence", score:1.0}` **精确命中** |
| 安全检查 | ❌ `safety_decision: reject`, reason=`unsafe` |

已排除的假设：`confirm_risky_actions` 关闭、换 light 域、调 apply、重启——**全部无效**。根因是目标不在引擎的"已暴露实体图"（`/api/entities` 返回 `[]` 且无写入接口）。

**所以：板子自有指令走通道 B（字符串直连，100% 可控）；家居词走通道 A（klar 有澄清/多轮能力）。**

## 四、实测通过的命令（8/8）

| 说法 | 通道 | 结果 |
|---|---|---|
| 看看是谁 | direct | 画面里没有人脸 |
| 打开人脸识别 | direct | 画面里没有人脸 |
| 开始录制 | direct | 录制已开启：持续录制 |
| 开始自动录制 | direct | 录制已开启：自动（有人脸才录） |
| 关闭录制 | direct | 录制已关闭 |
| 清空回收站 | direct | 回收站是空的 |
| 现在什么状态 | direct | NPU 正常；录制 off；画面 就绪 |
| 状态怎么样 | direct | NPU 正常；录制 off；画面 就绪 |

## 五、本板真实可控的 API（全部实测）

| 服务 | 端口 | 端点 | 实测 |
|---|---|---|---|
| 人脸/NPU | 8091 | `POST /api/recognize-camera` | ✅ 返回 faces/identities/timing，`backend=zg330_npu_pl_and_host`，`pipeline=yunet-mf-fp16-npu`，detector 45ms |
| | | `GET /api/health` | ✅ |
| | | `GET /api/gallery` | ✅ revision 18 |
| | | `GET /api/camera/status` | ✅ 960×540 双缓冲 |
| | | `POST /api/enroll` | 需照片（语音不开放） |
| 画面 | 8092 | `GET /api/status` | ✅ ready/session |
| 录制 | 8093 | `GET /api/status` | ✅ mode/state/queue |
| | | `POST /api/mode` | ✅ **只接受 `off`/`auto`/`manual`**（传 `on` 报 `Mode must be off, auto or manual`） |
| | | `GET /api/storage` | ✅ 491 MB / 26 条 |
| | | `GET /api/recordings` | ✅ |
| | | `POST /api/purge` | ⚠️ **必须带具体 id**（`{}` 返回 `{"error":"'id'"}`），不支持整批清空 |

## 六、两个被实测纠正的坑

1. **`mode: "on"` 不存在** → 必须映射成 `manual`。代码里已做映射。
2. **`/api/purge` 不能整批清空** → 需要逐条 id。所以"清空回收站"改成**查看**回收站（避免语音误删），真删走网页端。

## 七、文件

| 文件 | 作用 |
|---|---|
| `klar/klar_executor.py` | 双通道执行器，CLI 可直接测 |
| `klar/make_registry.py` | 生成 `core.entity_registry`/`core.area_registry` 给 klar |
| `klar/docs/KLAR-CUSTOM-PHRASES.md` | klar 自定义短语的完整证据链 |
| `deploy/klar-executor.service` | （待加）常驻服务 |

用法：

```bash
python3 klar_executor.py --do "看看是谁"          # 一句话端到端
python3 klar_executor.py --do "..." --dry-run     # 只规划不执行
python3 klar_executor.py --do "..." --json        # 机器可读
```

## 八、重量级任务放哪

**板子只做推理和 HTTP 调用，绝不编译。** 分工：

| 任务 | 放哪 | 理由 |
|---|---|---|
| 语音推理（ASR/TTS/KWS） | **板子** | 已优化到 RTF 0.5–1.4，实时可用 |
| NPU 人脸识别 | **板子** | 45 ms/帧，已实测 |
| 执行器 HTTP 调用 | **板子** | 毫秒级，无算力需求 |
| **模型量化 / 编译 / 转换** | **校机** | EPYC 9654 / 125 GB / 24 核 |
| **重建 HA / 大依赖编译** | **校机** | 板上 993 MB 内存不够 |
| **批量训练 / 评测** | **校机** | 板上 4×A55 @2.4GHz |

板上 993 MB 内存，当前语音链已用 ~650 MB，**没有余量跑重活**。

## 九、下一步可选

1. **接屏幕反馈**（板子有 HDMI）：说"开灯"时在屏幕显示执行结果，不依赖 TTS。
2. **接 TTS 播报**：executor 的 `message` 送给 10302 播放。
3. **扩直连命令表**：把你常用的说法都加进 `DIRECT_COMMANDS`（改一处即可）。
4. **接 Wyoming intent**：让 klar 通过 10500 主动推意图给 executor，省掉 HTTP 轮询。
