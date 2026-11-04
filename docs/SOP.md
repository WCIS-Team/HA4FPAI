---
tags: [SOP]
updated: 2026-10-30
---

# 板上语音栈与 klar NLU 部署和验收

> 一句话：**113 只跑语音链和 klar engine，HA 本体在别的机器。** 全部四条 systemd 服务起在板上，五项冒烟全绿即算过了。

## 前提判据（先查再动手）

| 判据 | 命令 | 通过值（2026-10-30 实测） |
|---|---|---|
| rootfs 有余量 | `df -h /` | 26G 总量、剩 ≥4G |
| 数据分区还在 | `df -h /mnt/data` | 30G、剩 26G |
| 模型齐 | `ls /mnt/data/models/{vad,asr,kws,tts-piper}` | silero v5 / 14M zipformer / chunk-8 KWS / xiao_ya piper |
| py 依赖在 | `pip3 show sherpa-onnx wyoming` | sherpa-onnx **2.0.1**、wyoming **1.10.2** |
| 板子不能编 | —— | 993 MB / 4×A55：**任何编译走校机** |

## 一、链路结构

```
HA 主机                          板 192.168.1.113
─────────                        ─────────────────
HA Core + klar HACS 集成   ◀────▶  klar engine  10500 wyoming / 10520 http
   (conversation agent)            ├ voice ASR   10301
Assist 语音流水线          ◀────▶  ├ voice TTS   10302
                                  └ voice wake  10300
                                   ↳ 全部共享同一份 onnxruntime（进程内单例）
```

**共享 env**：三个语音服务都 `from voice_common import kit`，`VoiceKit` 是进程内单例，模型懒加载一次后复用；onnxruntime 由 sherpa-onnx wheel 内部提供，不再各起一套。

**独立 session**：每个 Wyoming 连接各自 `kit().new_asr()` / `new_kws()` / VAD session，解码器状态不串。TTS 的 `generate()` 本身无状态，靠独立 handler 保证音频块不交错。

## 二、部署（在工作站上执行，不在板上编）

```bash
cd repos/HA4FPAI
BOARD=root@192.168.1.113 ./archiver/install-voice.sh
```

脚本做六件事：查磁盘 → 推 py 服务 + `~/klar-voice.json` → 板上 `py_compile` → 装 4 个 systemd 单元 → 缺则下 klar engine（`klar-linux-aarch64.tar.gz`，tag `2026.10.10`）→ `daemon-reload` + restart + 报告。

## 三、验收（怎么判过了）

```bash
BOARD=root@192.168.1.113 ./deploy/smoke.sh
```

五项全 PASS 才算过：

| # | 检查 | 判据 |
|---|---|---|
| 1 | 服务自述 | 10301/10302/10300 `Describe` 都能回 `Info`，模型名对得上 |
| 2 | ASR 实时 | 有文本，且 **RTF < 1.0**（实测 0.48–0.55） |
| 3 | TTS 出声 | 落盘 wav 有内容（暖机 RTF ≈3，冷启动 ≈12） |
| 4 | KWS 中文 | `zh_3.wav` 命中、`not_detected=False` |
| 5 | klar 中文 NLU | `打开客厅的灯` → `decision.type=execute` |

**唤醒词闭环**（"小克"）：

```bash
# 1. 确认 tokens.txt 里 iǎo / k / è 都在（本模型第 124/129/194 行）
# 2. keywords.txt 追加（已做，备份在 keywords.txt.bak-20261030）
printf 'x iǎo k è @小克\n' >> /mnt/data/models/kws/keywords.txt
# 3. 用 piper 自合成"小克"，再喂回 KWS
python3 wyoming_probe.py tts  --text '小克' --out /tmp/xiaoke.wav --uri tcp://127.0.0.1:10302
python3 wyoming_probe.py wake --wav /tmp/xiaoke.wav --uri tcp://127.0.0.1:10300
# 通过值：WAKE hits=['小克'] not_detected=False
```

## 四、踩过的坑（都是实测）

1. **HA 装不上板**：HA 最新 `requires_python>=3.14.2`，板上 py3.8.10；`homeassistant==2024.6.0` 在 arm64/py3.8 `No matching distribution found`；无 docker/podman；993 MB 内存不够。
2. **`schema_version` 不能进请求体**：`POST /api/v2/parse` 只认 `text/conversation_id/language/personality/preferred_area/nlu_*`，多传就 422。`schema_version:"2.0"` 是**响应**字段。
3. **piper 必须带 lexicon**：`OfflineTtsVitsModelConfig` 缺 `lexicon` 会让 C++ 侧直接 abort 整个进程（不是抛异常）。尝试顺序必须 lexicon 优先。
4. **klar engine 没有 `serve` 子命令**：直接 `./klar --http ... --wyoming ...` 就是服务。
5. **`attribution` 是 Info 必填**：wyoming 1.10.2 的所有 `Artifact` 子类都要它；`AsrProgram.requires_external_vad` 默认 True，本方案 VAD 在同进程，要显式置 False。
6. **`/dev/snd` 不存在**：板上无音频设备，麦克风在 HA 侧，音频走网络进 16k（板侧 `decimate3-mavg3` 处理 48k）。

## 五、回滚

```bash
ssh root@192.168.1.113
systemctl disable --now klar-voice-asr klar-voice-tts klar-voice-wake klar-nlu-engine
cp /root/klar-voice.json.bak-* /root/klar-voice.json   # 若有
cp /mnt/data/models/kws/keywords.txt.bak-20261030 /mnt/data/models/kws/keywords.txt
```

## 相关页面

- 仓库：`repos/HA4FPAI`（README 有全部实测数字表）
- HA 侧接线：`repos/HA4FPAI/ha/HOME-ASSISTANT.md`
- 板子目标平台：`wiki/PS/`、`wiki/PL/`
