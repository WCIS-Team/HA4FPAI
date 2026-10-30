---
tags: [SOP]
updated: 2026-10-30
---

# PAD 语义与开门判据（源码级核对）

> 起因：我上一轮把 `pad_security_calibrated` 和 `status` 两个字段的含义混为一谈，
> 说成「阈值没标定」。**这个说法是错的。** 本页按源码重写。

## 一、两个字段的真实含义

### `security_calibrated`
**含义**：是否完成**现场安全标定 + 误放/误拒验证**。当前恒为 `False`。

**它不代表没有判断逻辑。** 融合器早就在工作：把两个模型的 logits 做 softmax 后取均值，
按最高分类分数产出 `model_live` / `model_non_live`。

源码依据 `model_backend/face_pad.py:83` `combine_logits()`：
```python
def combine_logits(logits_by_model):
    """Official two-model mean softmax. This is model evidence, not access.
    ... Scores are not calibrated probabilities of safe entry and must be
    bound to the same frame/face outside this module."""
    ...
    return dict(model_label=label, model_predicts_live=label == 1,
                live_score=float(mean[1]), scores=mean.tolist(),
                **calibrated=False**)
```
注释原文就说清了：**是模型证据，不是准入判断；分数不是标定过的安全概率。**

### `status`
**含义**：**这一帧**的实际处理结果，不是「阈值标没标」。取值域：

| status | 含义 | 是否有 PAD 证据 |
|---|---|---|
| `not_applicable` | 这帧没检测到人脸，**PAD 模型根本没跑**（通常配 `reason: no_face`） | ❌ 无 |
| `model_live` | 模型判为活体 | ✅ 有（未标定） |
| `model_non_live` | 模型判为非活体（照片/屏幕/回放） | ✅ 有（未标定） |
| `inconclusive` | 两模型分类分数打平，无法判定 | ⚠️ 打平 |
| `stale` | 帧过期，结果作废（`reason: frame_expired`） | ❌ 无 |
| `not_evaluated` | 超出 `face_limit`，未评估 | ❌ 无 |

源码依据 `npu_photo/owner_pad.py:93` 与 `:173`：
```python
# 没脸就提前返回，不跑 PAD
return dict(evidence, status='not_applicable' if fresh() else 'stale', reason='no_face')

# 有脸才跑模型，按融合结果落 status
item.update(status='inconclusive' if tied else 'model_live'
            if combined['model_predicts_live'] else 'model_non_live',
            model_result=combined)
```

## 二、帧哈希 / frame_token / max_age_ms 的作用

它们用于**核对「这个结论属于哪一帧、是否过期」**，是证据绑定，不是判定本身。
- `source_rgb565_sha256` + `frame_token`：结论必须绑定到同一帧
- `max_age_ms`（默认 3000）：超时则 `stale`，结果作废

**完整证据块本身不能证明真假判断有效。**

## 三、当前应如何表述（准确版）

> **PAD 链路已接入，可产生未经安全标定的模型预测；
> 防照片、屏幕回放等实际效果尚未验收，不能作为开锁依据。**

不能说的是：
- ❌ 「PAD 未标定所以没判断逻辑」——错，模型在跑
- ❌ 「status=not_applicable 表示阈值没标定」——错，那是没检测到脸
- ❌ 「有完整证据块就说明防伪有效」——错，证据只是绑定

## 四、后续要做的事（不能只调阈值）

`security_calibrated` 要变 True，需要**现场样本测试**：

| 样本组 | 数量建议 | 用途 |
|---|---|---|
| 真人（不同光照/距离/角度） | ≥ 30 | 测误拒（FRR） |
| 照片打印（手机屏/相纸） | ≥ 20 | 测误放（FAR） |
| 屏幕回放（录像播放） | ≥ 20 | 测误放 |
| 面具/遮挡 | ≥ 10 | 边界 |

**要我明确说清**：不能保证只调阈值就能达标。若模型本身区分度不够，
需要换模型或加深度信息（这块板是单目，没有深度）。

## 五、代码里怎么处理的

`boards/door_unlock.py` 的 `check_face()` 已按上表重写：

```python
if pad_status == "model_live":    → pad_ok=True,  pad_have_evidence=True
elif pad_status == "model_non_live":→ pad_ok=False, pad_have_evidence=True  # 疑似攻击
elif pad_status in ("not_applicable","stale","not_evaluated"):
                                  → pad_ok=False, pad_have_evidence=False   # 无证据
```

`PAD_MODE`：
- `warn`（默认）——记录+播报提醒，不阻断（因为未标定）
- `enforce`——必须有 `model_live` 才放行（标定完成后才可启用）

开门成功时若 `pad_calibrated=False`，播报会追加：
「（注意：防伪未经安全标定，本次不依据其结论。）」

## 相关页面

- `docs/ROUTE2-IMPLEMENTED.md` — 执行器与板 API
- `docs/MEMORY-BREAKDOWN.md` — 内存实测
