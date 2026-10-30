# HA 侧待实现清单（板上已完成的部分之外）

> 生成于 2026-10-30。板上语音链 + klar engine **已全部完成并验收通过**（见 `../README.md`）。
> 本页只列**还需要在 HA 主机上做的事**，每条给工作量估计。

## 现状对照：已完成的 vs 待做的

| 部件 | 位置 | 状态 |
|---|---|---|
| VAD (silero v5) | 板 `/mnt/data/models/vad` | ✅ 已完成，进程内 |
| ASR (zipformer 14M int8) | 板，Wyoming 10301 | ✅ RTF 0.54 |
| KWS (3M-2025-12-20 int8) | 板，Wyoming 10300 | ✅ RTF 0.50，唤醒词「小克」闭环通过 |
| TTS (matcha-zh-baker + vocos) | 板，Wyoming 10302 | ✅ RTF 1.4 |
| klar NLU engine | 板，10500/10520 | ✅ 中文解析 execute |
| 共享 env / 独立 session | 板 | ✅ 单进程单 onnxruntime |
| 统一 option JSON | 板 `~/klar-voice.json` | ✅ 含 llm 预留段 |
| **HA Core** | **HA 主机** | ❌ **待装** |
| **klar HACS 集成** | **HA 主机** | ❌ **待装** |
| **HA ↔ klar engine 接线** | HA 主机 | ❌ 待配 |
| **真实实体表对接** | HA 主机 | ❌ 待接（关键） |
| **短语缓存** | 板 | ⏸ 等接上 HA 再做 |
| **LLM** | 预留 | ⏸ 未启用 |

---

## 一、HA Core 安装（工作量：**小，0.5 天**）

**唯一的硬前提**：要有一台 **Python ≥ 3.14.2** 的 Linux 机器（实测数据见 `HA-PYTHON-DOWNGRADE.md`）。

三种方式，按简单程度排：

### 方式 A：Docker 容器（最省事，推荐）
```bash
docker run -d --name homeassistant --restart=unless-stopped \
  --network=host \
  -v /path/to/ha-config:/config \
  -v /etc/localtime:/etc/localtime:ro \
  ghcr.io/home-assistant/home-assistant:stable
```
- 优点：**不用管 Python 版本**，镜像自带
- 前提：那台机器有 docker
- 工作量：**2 小时**（含首次启动向导）

### 方式 B：venv（要自己装 py3.14）
```bash
uv python install 3.14
uv venv --python 3.14 /opt/ha
/opt/ha/bin/pip install homeassistant
/opt/ha/bin/hass --config /srv/ha
```
- 工作量：**半天**（py3.14 + 依赖编译，那里没有轮子时要源码编）
- 实测：wheel 62.7 MB，解压 **238 MB**

### 方式 C：HA OS 整机镜像
- 工作量：**1 小时**，但需要专用机器/虚拟机，不能和你现有服务共存

**建议：方式 A。** 你只要有台能跑 docker 的机器就完事。

---

## 二、klar HACS 集成（工作量：**很小，0.5 小时**）

1. HA 装 HACS（如果没装）
2. HACS → 右上角 ⋮ → Custom repositories
3. 添加 `https://github.com/FABBricate-IT-Solutions/klar-ha-nlu`，类别选 **Integration**
4. 安装，然后 **重启 HA**
5. 设置 → 设备与服务 → 添加集成 → 搜 `klar` → 填 engine 地址

**engine 地址** = `http://192.168.1.113:10520`（板上的 HTTP parse 面）

> 实测：集成本体只有 `custom_components/klar_nlu/` 这一层（131 文件），仓库其余是 Rust 源码和语言包，不用管。

---

## 三、HA ↔ 板上语音链接线（工作量：**小，0.5 小时**）

`configuration.yaml` 加：
```yaml
wyoming:
  - host: 192.168.1.113
    port: 10301        # ASR
  - host: 192.168.1.113
    port: 10302        # TTS (matcha)

klar_nlu:
  engine_url: http://192.168.1.113:10520
  language: zh-CN
```

然后 **设置 → 语音助手 → 新建**：
```
唤醒词      : 板上 KWS 10300（"小克"），或 HA 自带
语音转文字   : wyoming 192.168.1.113:10301
对话代理     : klar_nlu
文字转语音   : wyoming 192.168.1.113:10302
```

**验收**：HA 里对着麦克风说「小克」→「打开客厅的灯」，应看到识别文本 + klar 的 `execute` + 灯动作 + 语音播报。

⚠️ 前提：**HA 主机得能访问 192.168.1.113 的这些端口**。如果 HA 在另一网段，需要路由器放通或走中转。

---

## 四、真实实体表对接（工作量：**中，1–2 天**）★ 最关键

**这是目前唯一的实质缺陷。**

现状：engine 没有真实 HA 实体表时，用**内置德语默认公寓**，中文词映射到德语实体 id：

| 你说 | 现在播报 | 应该是 |
|---|---|---|
| `打开风扇` | **Lüfter开了。** | 客厅风扇开了。 |
| `打开窗帘` | **Rollo Wohnzimmer开了。** | 客厅窗帘开了。 |
| `打开所有灯` | **Wohnzimmer或Esszimmer或…?** | 客厅或餐厅或卧室? |

**修法**：给 engine 传真实 registry
```bash
# systemd 单元里
--config-dir /config        # 指向 HA 真实的 config 目录
```
HA 主机的 `/config/.storage/core.entity_registry` 就是真值。engine 只读，不写。

**这一步做什么**：
1. HA 里把房间（Area）命名成中文：客厅 / 卧室 / 厨房 / 餐厅 / 书房
2. 给设备起中文名（`light.living_room` 的 friendly_name = 客厅的灯）
3. 让 engine 能读到那份 `.storage`（挂载/同步/或直接把 engine 移到 HA 机器上）
4. 逐个验证：每个房间 × 每种设备（灯/风扇/窗帘/空调/电视）至少测一遍开与关

**工作量拆解**：
- 实体命名整理：3–4 小时（取决于你有多少设备）
- engine 读 registry 打通：2 小时
- 逐组合验证：半天（60–100 条指令）

**⚠️ 关键决策点**：engine 现在在**板上**，但 `.storage` 在 **HA 主机**。两个选择：
- **A**：把 engine 也移到 HA 主机（那板只做语音 I/O）—— **推荐**，省得同步文件
- **B**：把 HA 的 `.storage` 定期同步到板 —— 要写同步逻辑，易出问题

选 A 的话，板上只剩 10300/10301/10302 三个语音服务，内存压力也更小。

---

## 五、短语缓存（工作量：**小，半天**，但**等第四步完成再做**）

理由：现在做会缓存一批德语 wav，接上真实实体后全废。

做完第四步后，缓存键 = `speech 文本 + sid + speed` 的内容哈希。需要预热的模板（我采样 34 条指令得到 **15 个唯一模板**）：

**通用短句（必做）**
| 模板 | 场景 |
|---|---|
| `真的要执行吗？` | confirm |
| `打开什么？` | clarify |
| `关闭什么？` | clarify |
| `我没听清。比如说，告诉我要开哪盏灯。` | reject（最高频） |
| `多长时间？` | 定时器澄清 |
| `没有播放器。` | 媒体无播放器 |
| `定时器已开始。` | 定时器成功 |

**实体相关（按你的房间×设备枚举）**
| 模板 | 数量 |
|---|---|
| `{房间}的灯开了。` | × N 房间 |
| `{房间}的灯关了。` | × N 房间 |
| `{房间}的灯 {n}%` | × N 房间 × M 档位 |
| `{房间}或{房间}或…?` | 多候选澄清 |

**实现**：`klar_tts.py` 里加一层 `speech_hash -> wav bytes` 的 LRU + 落盘缓存，命中直接回播放，不经过模型。预计能省 1.2s/条（RTF 1.4 下的 2s 音频）。

---

## 六、LLM 预留位（工作量：**待定，0 或 1 天**）

`klar-voice.json` 的 `llm` 段已留好，`boards/klar_llm.py` 是可用但未启用的客户端。

启用只需填三个字段：
```json
"llm": { "enabled": true, "base_url": "http://<某台机>:8080/v1",
         "api_key": "", "model": "qwen2.5-0.5b-instruct" }
```

**位置**：klar 判定「不是家居指令」之后才走 LLM 闲聊，或把已定的播报改写自然。**LLM 永不决定设备动作。**

⚠️ **别在本板跑 LLM**：993 MB 内存，语音链已占 ~500 MB。指向另一台机。

---

## 工作量汇总

| # | 任务 | 工作量 | 阻塞关系 |
|---|---|---|---|
| 1 | HA Core 安装（Docker） | 0.5 天 | 需要有 py3.14/docker 的机器 |
| 2 | klar HACS 集成 | 0.5 小时 | 依赖 1 |
| 3 | Wyoming + klar 接线 | 0.5 小时 | 依赖 1、2 |
| 4 | **真实实体表对接** | **1–2 天** | 依赖 3，**关键** |
| 5 | 短语缓存 | 0.5 天 | **依赖 4** |
| 6 | LLM 启用 | 0–1 天 | 可选 |

**总计：约 2.5–4 天**，其中第四步占一半以上。

### 最小可用路径（1 天出结果）
1 → 2 → 3 走完（**1 天**），此时能听见识别与播报，但播报里还是德语实体名。
第 4 步做玩才算真能用。

---

## 需要你确认的两件事

1. **HA 主机是哪台？** 有 docker 吗？Python 版本多少？—— 决定第 1 步是 2 小时还是半天。
2. **engine 留板上还是移到 HA 主机？**（第四节 A/B）—— 我建议移到 HA 主机，省掉文件同步。
