# HA 侧接入（不在 113 上装）

## 为什么 HA 本体不在板上

实测证据（2026-10-30，板上直查）：

| 检查 | 结果 |
|---|---|
| `python3 -V` | **3.8.10**（focal 全源最高，无 deadsnakes，无其他解释器） |
| PyPI `homeassistant` `requires_python` | **>= 3.14.2** |
| `pip3 download homeassistant==2024.6.0` | `No matching distribution found` |
| `command -v docker podman` | 空 |
| `free -m` | total **993 MB**（HA Core 装不下） |
| `/dev/snd` | **不存在** |

所以：**板子只跑语音链 + klar engine，HA 本体装在有 Python 3.13+/容器能力的机器上。**

## 两半分工

| 部件 | 跑在哪 | 怎么装 |
|---|---|---|
| HA Core | HA 主机 | 官方 Supervised / Container / venv |
| klar **HACS 集成** (`klar_nlu`) | HA 主机 | HACS → Custom repository → `FABBricate-IT-Solutions/klar-ha-nlu` |
| klar **engine** | **本板** `/mnt/data/klar/engine/klar` | 已部署，systemd `klar-nlu-engine` |
| 语音链 ASR/TTS/wake | **本板** 10301 / 10302 / 10300 | 已部署，systemd `klar-voice-*` |

## HA `configuration.yaml` 片段

```yaml
# --- Wyoming 语音链（指向板上） ---------------------------------
wyoming:
  - host: 192.168.1.113
    port: 10301          # ASR: sherpa-onnx streaming zipformer
  - host: 192.168.1.113
    port: 10302          # TTS: piper zh_CN-xiao_ya

# --- klar NLU 对话代理 -------------------------------------------
klar_nlu:
  engine_url: http://192.168.1.113:10520   # 板上 klar engine 的 HTTP 面
  language: zh-CN
```

> Wyoming 端口以板上 `ss -lntp` 实测为准；wake（10300）只有在用板上 KWS 做唤醒时才需要挂进 Assist 的 wake 槽，若 HA 侧用别的唤醒方案则不挂。

## Assist 流水线（`settings → voice assistants`）

```
Wake word  : 板上 KWS 10300（"小克"）或 HA 自带
Speech-to-text : wyoming 192.168.1.113:10301   (sherpa-onnx)
Conversation   : klar_nlu  (engine 在 10500 wyoming / 10520 http)
Text-to-speech : wyoming 192.168.1.113:10302   (piper)
```

## 预留 LLM 位

`klar-voice.json` 的 `llm` 段为空、`enabled:false`。链路里 LLM 的位置是：**klar 判定「不是家居指令」之后**才交给 LLM 闲聊，再由 TTS 播报。设备控制权始终在 klar engine，LLM 只说话（或把已定的播报改写得更自然）。见 `boards/klar_llm.py`。

## 验证顺序（建议）

1. `./deploy/smoke.sh` —— 五项全过（服务自述 / ASR 实时 / TTS 出声 / KWS 中文命中 / klar 中文解析）。
2. HA 主机 `curl http://192.168.1.113:10520/api/v2/parse -d '{"text":"打开客厅的灯","language":"zh-CN"}' -H 'Content-Type: application/json'` 应返回 `decision.type=execute`。
3. HA 里配好上面三条 Wyoming + klar 集成，跑 Assist 五句话。

注意 `POST /api/v2/parse` 的请求体**只认** `text / conversation_id / language / personality / preferred_area / nlu_*`，多传 `schema_version` 会 422（实测踩过）。`schema_version:"2.0"` 是**响应**里的字段。
