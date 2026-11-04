# lang/tts/models/edge —— Edge「大声朗读」在线中文 TTS

调微软 Edge 的公开朗读端点，把中文文本合成语音。**无需 API key、无需账号，但要联网。**

## 两种用法

| 用法 | 说明 |
|---|---|
| **server 的 `edge` 档（推荐）** | `lang/server` 的 `chain.tts: edge`（或请求 `?tts=edge` / `?tts_tier=edge`）直接 `import edge_tts_client`，进程内合成，无需另起服务。配置在 `lang/config/online.yaml` 的 `online.edge`。 |
| **独立常驻 HTTP 服务** | `python -X utf8 edge_tts_server.py --port 5003`，端点 `POST /tts {"text":...}` → wav。给"server 不在同进程 / 手测 / 多消费者"用；或让 server 的 `chain.tts: http` 指到它。 |

## 依赖

- `edge-tts`（Python 包）：`pip install edge-tts`（本机已装 7.3.1）。
- **mp3 解码器（任选其一，越小越好，边缘设备别背 ffmpeg）** —— `edge_tts_client` 按
  `miniaudio → soundfile → mpg123 → ffmpeg` 自动挑第一个可用的（可用 `online.edge.decoder` 指定）：

  | 解码器 | 体积（量级） | 装法 | 说明 |
  |---|---|---|---|
  | **miniaudio** | **~0.1 MB**（实测本机 `miniaudio.py` 90 KB） | `pip install miniaudio` | 纯轮子、无系统依赖，自带 minimp3；**边缘首选**，实测与 ffmpeg 解出帧数一致、更快 |
  | mpg123 | ~0.2 MB（libmpg123+CLI） | `apt install mpg123` | 系统包最小，CLI 出裸 PCM；板上 apk/apt 一条命令 |
  | soundfile | ~1 MB | `pip install soundfile` | libsndfile≥1.1 支持 mp3 |
  | ffmpeg | **~200 MB**（本机 211 MB） | 系统包 | 兜底，最不划算；有上面任一就不必装 |

  ⇒ **你的边缘设备建议 `pip install miniaudio`**（或装系统 `mpg123`），就能完全不用 ffmpeg。

## 中文音色（部分）

| voice | 说明 |
|---|---|
| `zh-CN-XiaoxiaoNeural` | 女声（默认，自然） |
| `zh-CN-XiaoyiNeural` | 女声（活泼） |
| `zh-CN-YunxiNeural` | 男声 |
| `zh-CN-YunjianNeural` | 男声（解说） |
| `zh-CN-YunyangNeural` | 男声（新闻） |
| `zh-CN-liaoning-XiaobeiNeural` | 东北女声 |
| `zh-CN-shaanxi-XiaoniNeural` | 陕西女声 |
| `zh-HK-HiuMaanNeural` / `zh-TW-HsiaoChenNeural` | 粤语 / 台湾 |

语速 `rate="+20%"`、音量 `volume="+0%"`、音调 `pitch="+0Hz"`。

## 手测

```bash
cd repos/HA4FPAI/lang/tts/models/edge
python -X utf8 -c "import edge_tts_client as c; open('out.wav','wb').write(c.synthesize_wav('你好，世界'))"
python -X utf8 edge_tts_server.py --port 5003
curl -s -H 'Content-Type: application/json' -d '{"text":"你好，世界"}' http://127.0.0.1:5003/tts -o out.wav
```

## 坑（同见 `server/PITFALLS.md` #22）

- **不是 wav**：edge-tts 只给 mp3；必须解码成 PCM。**解码器可插拔**（miniaudio 最小首选；ffmpeg 只是兜底）。`fmt="mp3"` 可原样要 mp3（`edge_tts_server` 的 `?format=mp3`）。
- **联网**：离线/被墙会失败；板上可能要 `online.edge.proxy`（如 `http://127.0.0.1:7897`）。
- **每句一次网络往返**：实测单句约 3 s（含转码）；长文本先经 server 的分词器切块（`tokenizer_profiles.tts_split.max_chars`，**默认 20 字、严格不超**）。
- **限流**：微软端点频繁调用可能被限；不要高并发轰炸。
