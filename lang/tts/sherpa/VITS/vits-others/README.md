# 其余 VITS 候选 —— ❌ 全部在 icraft 编译阶段失败

> 这一目录只是**留档"试过哪些、为什么不成"**，没有可复跑的脚本——因为它们连
> parse 都过不去，没有产物。**结论：在这套工具链上，VITS 家族整体走不通。**

测量口径：`icraft-parse` 一次，看 `rc` 与缺陷算子。完整表格在
`wiki/PL/MOD/modellist/TTS同量级候选进NPU横评.md`。

---

## 1. 逐个结果

| # | 模型 | 大小 | 节点 | **rc** | 产物 | 缺陷算子 / 断点 |
|---|---|---:|---:|---:|---|---|
| 1 | `sherpa-onnx-vits-zh-ll` | 121.1 MB | — | **19020** | ✘ | **`Type Error: tensor(int64)`** |
| 2 | `vits-icefall-zh-aishell3` | 30.5 MB | 4896 | **19020** | ✘ | 同 `int64` |
| 3 | `vits-melo-tts-zh_en` | 170.4 MB | — | **26000** | ✘ | 见横评页 §5 断点 |
| 4 | `vits-zh-hf-eula` | 121.9 MB | — | **19020** | ✘ | `tensor(int64)` |
| 5 | `vits-piper-zh_CN-*`（3 个） | 63.2 MB | 2682–2755 | **−1073741819（段错误）** | ✘ | `Split` 改写引发崩溃 |

**5 个模型族全部失败，没有一个出得了 `json+raw`。**

---

## 2. 对照：为什么 `matcha` / `vocos` 反而能过

| 模型 | rc | 产物 | 缺陷算子 |
|---|---:|---|---|
| **`vocos-22khz-univ`** | **0** ✅ | **json+raw ✔** | **零缺陷** |
| **`matcha-icefall-zh-baker`** | 71 | json 1.6 MB + raw 131 MB ✔ | `Range`、`RandomNormalLike` |
| `matcha-icefall-zh-en` | 71 | ✔ | 同上 |

**能过 parse 的只有 `vocos` 和 `matcha`（部分）。**
- `vocos` 零缺陷，五段全通，已上板（见 `../../vocos/`）
- `matcha` `rc=71` 且**连 optimize 都进不去**（`Unable to match pattern with Op, Op_id is 3, Op_type is icraft::xir::AddNode`）⇒ 留 CPU

---

## 3. 一条被否定的设想："CPU 补算"

曾经设想过"`rc=71` 的模型让缺的算子回落到 CPU 补算"。

**这条不存在。** 依据：
- `Unsupport` 只是**解析占位符**，**没有前向实现**；
- 实测 `icraft-optimize` 对 matcha 的产物直接 `RC=1 Index 0 out of bounds 0`，
  **连优化阶段都进不去**，谈不上"运行时回落"。

⇒ 任何"能 parse 但 rc≠0"的模型，**都只能整体留 CPU**。

---

## 4. 结论

**VITS 家族（Piper / sherpa-onnx-vits / icefall-vits / melo / eula）在 ZG330 上不通。**
要在这块板上做中文 TTS，只剩两条路：

1. **`../../ekho-linux/`** —— 录音拼接，RTF 0.49、RSS ~20 MB，**现役交付**
2. **`../../matcha/` + `../../vocos/` 或 `../../HiFi-GAN/`** —— 两段式，声码器可下沉 NPU

---

## 5. 相关

- 完整横评：`wiki/PL/MOD/modellist/TTS同量级候选进NPU横评.md`
- 单体 TTS 的详细死因：`../piper/README.md`
- 能过的那个：`../../vocos/README.md`