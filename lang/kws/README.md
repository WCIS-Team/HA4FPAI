> **参数全录在 [para.md](para.md)**（sherpa-onnx v2.0.1 源码级：13 个实例参数、chunk 约束、
> `keywords.txt` 行格式、运行时纪律、`lang/server` 的接法）。
> 本页只管**选型**——两个模型怎么挑。

没有单一答案，取决于你要不要英文唤醒词。
只做中文 → sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01 的 int8 版最好。 判别余量最高（均值 0.675、最差关键词 0.5）、体积最小（5.02 MB）、最快（RTF 0.087）、零误报，而且它是中文专用训练的。
中英双语都要 → sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20 的 chunk-8 int8 版最好。 中文余量 0.637，只比中文专用模型低 0.04，却省掉一套模型；而且它是全部配置里唯一在阈值 0.5 仍保持 8/8 命中的，延迟也只有 160 ms。

完整排序（按中文判别余量，均值/最差词）：

| 排名 | 配置 | 余量 均值/最低 | 阈值0.5命中 | 误报 | 体积 | RTF |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | zh-wenet ch16 int8 | 0.675 / 0.5 | 7/8 | 0/104 | 5.02 MB | 0.087 |
| 2 | zh-wenet ch16 fp32 | 0.65 / 0.4 | 7/8 | 0/104 | 13.08 MB | 0.095 |
| 3 | zh-en ch8 int8 | 0.637 / 0.5 | 8/8 | 0/104 | 5.45 MB | 0.100 |
| 4 | zh-en ch8 fp32 | 0.625 / 0.5 | 8/8 | 0/104 | 13.07 MB | 0.105 |
| 5 | zh-en ch16 fp32 | 0.613 / 0.4 | 7/8 | 0/104 | 13.07 MB | 0.091 |
| 6 | zh-en ch16 int8 | 0.55 / 0.3 | 6/8 | 0/104 | 5.45 MB | 0.082 |

明确不推荐的是最后一行：zh-en 的 chunk-16 int8 是唯一最差关键词余量掉到 0.3、且阈值 0.5 就掉到 6/8 的组合——同一个包里选 int8 就该选 chunk-8。

一点必要的保留：前两名（0.675 vs 0.637）和阈值 0.5 处谁掉词的结果互相矛盾，差距落在 7 段音频、8 个关键词这个小样本能分辨的范围边缘。如果你的场景是关键词表固定、可以自己调阈值，第 1 名和第 3 名实质上是并列的，按"要不要英文"来选比按这点余量差来选更靠谱。

## 模型清单（`models/`，本机副本不入库）

两个模型的 **int8 版**已放在 [models/](models) 下，各一个子目录。
**权重不入库**（`repos/.gitignore` 的 `*.onnx` 规则挡着，`lang/vad/models/` 同理）——
正本用官方发布页重拉，见下表末列。

| 目录 | 内容 | 用途 |
|---|---|---|
| `models/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01/` | encoder/decoder/joiner **全 int8**（**chunk-16**，合计 4.79 MB）＋ `tokens.txt` ＋ `keywords.txt`（8 个中文词，拼音建模）＋ 7 段 `test_wavs/` | 中文专用，余量最高（均值 0.675），RTF 0.087 |
| `models/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20/` | encoder/joiner **int8** ＋ **decoder 只有 fp32**（上游未发 int8 decoder；**chunk-8**，合计 5.19 MB）＋ `tokens.txt` ＋ `en.phone` ＋ `test_wavs/keywords.txt`（10 词，中英双语） | 中英双语，阈值 0.5 仍 8/8，**板端现役就是这一套**（`HA4FPAI/boards/klar-voice.json`） |

**chunk 不能混**：两套的 encoder/decoder/joiner 三个文件 chunk 必须一致
（wenetspeech 是 chunk-16，zh-en 是 chunk-8），且必须整套换。
实测混用（zh-en chunk-8 encoder ＋ wenetspeech chunk-16 decoder/joiner）**不报错但命中 0/9**——
静默全废，所以两套必须分目录放、不许交叉引用。

**重拉正本**（GitHub Release，官方发布页）：

```bash
# 两个 tar.bz2 各 ~31 MB，解包后按上表挑 int8 那几件
https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01.tar.bz2
https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20.tar.bz2
```

> 本机 `huggingface.co` 返 **401 "Invalid username or password"**（直连、走代理、
> hf-mirror 三条路都一样；`hf auth whoami` 显示未登录且无 token 文件）⇒ 走 GitHub Release。
> 上游另有同名 HF 仓库（`k2-fsa/sherpa-onnx-kws-zipformer-*`）与 ModelScope 镜像，
> 内容一致。

## KWS 之后的那次路由（`router.py`，server 调用）

链路（`lang/server`）：`sar → rms → kws ──[router.route()]──▶ vad → asr → router(klar) → …`

KWS 只负责给 `(hit, tag)`；**接下来往哪走由本目录的 `router.py` 决定**——她 2026-11-04 定的
"具体写在 kws 文件夹里，server 去调用"。server 侧只有薄调用层
（`lang/server/backends/kws_route.py`），要改路由请改这里 ＋ `lang/config/kws.yaml`。

返回：`{"action": "continue"|"stop"|"direct", "route": "…", "reply": "…", "reason": "…"}`

- `continue` → 进 vad → asr（常规：唤醒后说话）
- `stop` → 就此打住（只唤醒不说话／噪声误触）
- `direct` → 跳过 vad/asr，用 `reply` 直接走分词＋TTS（如"我在。"）

### 配置（`kws.yaml → kws_route_profiles.default`）

⚠️ 用**三张扁平表**，别写 `routes.<tag>.{...}` 三层嵌套——本仓配置解析器只支持两层嵌套，
三层会被拍平（见 `server/PITFALLS.md` #26）。

```yaml
kws_route_profiles:
  default:
    on_miss: stop          # 没命中：stop | continue
    default_action: continue
    default_route: general
    routes:                # tag → 路由名（继续 vad/asr）
      小助手: general
      开门: device
      验证: verify
    direct:                # tag → 固定回应（action=direct）
      我在: "我在。"
    stop:                  # tag → 停
      再见: 1
```

匹配顺序：`direct` → `stop` → `routes` → `default_*`；tag 精确优先，再双向子串。
中文 tag 当键是支持的（`cfg.parse` 已放宽到非 ASCII 键，2026-11-04 修）。