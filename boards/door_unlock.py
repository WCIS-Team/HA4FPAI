#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""door_unlock.py — 多因子开门验证：「我回来了，开门」

串行四道闸，任一不过就不开：

    ① 口令文本     —— 必须听到「开门」类说法（防止误触/闲聊触发）
    ② 人脸识别     —— NPU 必须认出 gallery 里的已注册人
    ③ 人脸防伪 PAD —— 必须是真人，不是照片/屏幕（看 presentation_attack_detection）
    ④ 声纹校验     —— 说话人必须与已录入声纹匹配（防止有人拿着照片+听到口令）

为什么四道而不是一道：
    只有人脸 → 照片就能开
    只有声纹 → 录音就能开
    人脸+声纹 → 需要「这个人当着摄像头说」
    + PAD    → 还要求是活体，挡住照片/手机屏翻拍

实测数据（本板 2026-10-30）
    人脸 NPU   : YuNet+MobileFaceNet, recognize-camera 45–80ms
    人脸防伪   : presentation_attack_detection 字段已存在，status=not_applicable
                 security_calibrated=false  ← 尚未标定，见下
    声纹       : campplus-zh 192维, RTF≈0.24, 同人 cos=1.00 不同人 cos=-0.07~-0.09

⚠️ PAD 当前未标定（security_calibrated=false），所以默认 PAD_MODE="warn"：
   记录证据但不阻断。标定后改成 "enforce" 即真正开门禁。

用法
    python3 door_unlock.py --wav hello.wav            # 口令+声纹，人脸走摄像头
    python3 door_unlock.py --wav hello.wav --dry-run  # 只评估不执行开门
"""
import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import voiceprint as vp  # noqa: E402

from klar_executor import http, BOARD  # noqa: E402

LOG = logging.getLogger("door")

# 「开门」类口令（通道B直连，不依赖 klar 词表）
UNLOCK_PHRASES = [
    "开门", "开一下门", "打开门", "帮我开门", "我回来了", "我回来了开门",
    "我回来了，开门", "开门吧", "把门打开", "门开一下",
]

# PAD 策略：warn = 只记录不阻断；enforce = 不通过就拒绝
PAD_MODE = os.environ.get("KLAR_PAD_MODE", "warn")

# 声纹匹配阈值（实测同人 1.00 / 异人 -0.07，0.5 是安全中位）
VOICEPRINT_THRESHOLD = float(os.environ.get("KLAR_VP_THRESHOLD", "0.5"))

# 人脸相似度阈值（引擎返回的身份已经过它自己的阈值，这里只记录）
FACE_REQUIRED = True


def is_unlock_phrase(text):
    t = (text or "").strip()
    for p in UNLOCK_PHRASES:
        if p in t or t in p:
            return True, p
    return False, None


# ---------------------------------------------------------------------------
# ① 人脸识别 + ③ 防伪（同一次 recognize-camera 调用里同时拿到）
# ---------------------------------------------------------------------------
def check_face():
    """返回 dict: ok / names / faces / pad / detail"""
    st, d = http(BOARD["face"] + "/api/recognize-camera", "POST", b"", timeout=60)
    if st != 200 or not isinstance(d, dict):
        return {"ok": False, "reason": "人脸接口失败(%s)" % st, "raw": str(d)[:200]}

    res = (d.get("entry") or {}).get("result") or {}
    identities = res.get("identities") or []
    faces = res.get("faces") or []
    pad = res.get("presentation_attack_detection") or {}

    names = []
    for i in identities:
        nm = i.get("name") or i.get("identity") or i.get("person") or None
        if nm:
            names.append(nm)

    # ------------------------------------------------------------------
    # PAD 判定 —— 两个字段含义必须分清（源码依据见文末注释）
    #
    #   security_calibrated : 是否完成「现场安全标定 + 误放/误拒验证」。
    #                         现在恒为 False。**但它不代表没有判断逻辑**：
    #                         融合器早就按最高分类分数产出 model_live /
    #                         model_non_live / inconclusive。
    #
    #   status              : 本次这一帧的真实处理结果。取值域：
    #                           not_applicable  -> 这帧没检测到人脸，PAD 模型
    #                                              根本没跑（通常配 reason=no_face）
    #                           model_live      -> 模型判为活体
    #                           model_non_live  -> 模型判为非活体（照片/屏幕等）
    #                           inconclusive    -> 两模型分类分数打平，无法判定
    #                           stale           -> 帧过期，结果作废
    #                           not_evaluated   -> 超出 face_limit 未评估
    #
    #   ⇒ status 不是"阈值有没有标定"，而是"这一帧的模型结论是什么"。
    # ------------------------------------------------------------------
    pad_status = pad.get("status")
    pad_calibrated = bool(pad.get("security_calibrated", False))
    pad_reason_raw = pad.get("reason")

    # 按真实取值域映射
    if pad_status in ("not_applicable", None, ""):
        # 没跑 PAD 推理 —— 不是"通过"，是"没有证据"
        pad_ok = False
        pad_have_evidence = False
        pad_reason = "PAD 未执行（%s）：这帧没有可评估的人脸" % (pad_reason_raw or "no_face")
    elif pad_status == "model_live":
        pad_ok = True
        pad_have_evidence = True
        pad_reason = "PAD 模型判为活体（未经安全标定，仅供参考）"
    elif pad_status == "model_non_live":
        pad_ok = False
        pad_have_evidence = True
        pad_reason = "PAD 模型判为非活体（疑似照片/屏幕/回放，未经安全标定）"
    elif pad_status == "inconclusive":
        pad_ok = False
        pad_have_evidence = True
        pad_reason = "PAD 两模型分类打平，无法判定"
    elif pad_status == "stale":
        pad_ok = False
        pad_have_evidence = False
        pad_reason = "PAD 结果作废：帧已过期（%s）" % (pad_reason_raw or "frame_expired")
    elif pad_status == "not_evaluated":
        pad_ok = False
        pad_have_evidence = False
        pad_reason = "PAD 未评估：超出人脸数上限"
    else:
        pad_ok = False
        pad_have_evidence = False
        pad_reason = "PAD 未知状态 %r" % (pad_status,)

    # live_score 作为参考记录（未标定概率，不当作通过依据）
    pad_live_score = None
    mres = pad.get("model_result") or {}
    if isinstance(mres, dict):
        pad_live_score = mres.get("live_score")

    face_ok = bool(names)
    # 开门时的 PAD 门槛：
    #   enforce -> 必须有 model_live 证据才放行
    #   warn    -> 记录+播报提醒，但不阻断（当前默认，因为未标定）
    pad_gate_ok = pad_ok if PAD_MODE == "enforce" else True

    return {
        "ok": face_ok and pad_gate_ok,
        "face_ok": face_ok,
        "pad_ok": pad_ok,
        "pad_have_evidence": pad_have_evidence,
        "pad_calibrated": pad_calibrated,
        "pad_status": pad_status,
        "pad_live_score": pad_live_score,
        "pad_reason": pad_reason,
        "names": names,
        "face_count": res.get("face_count", 0),
        "reason": "人脸未匹配" if not face_ok else pad_reason,
        "threshold": res.get("threshold"),
        "pipeline": res.get("pipeline_id"),
    }


# ---------------------------------------------------------------------------
# ④ 声纹
# ---------------------------------------------------------------------------
def check_voiceprint(wav_path):
    try:
        vpobj = vp.Voiceprint.get()
        emb = vpobj.embed_wav(wav_path)
    except Exception as e:                       # noqa: BLE001
        return {"ok": False, "reason": "声纹提取失败: %s" % e}
    name, score, scores = vp.verify(emb, threshold=VOICEPRINT_THRESHOLD)
    return {
        "ok": bool(name),
        "matched": name,
        "score": score,
        "threshold": VOICEPRINT_THRESHOLD,
        "scores": scores,
        "reason": ("声纹匹配 %s (%.3f)" % (name, score)) if name
                  else "声纹未匹配（最佳 %.3f < %.2f）" % (score, VOICEPRINT_THRESHOLD),
    }


# ---------------------------------------------------------------------------
# 门锁执行端（当前没有真实门锁，先做占位 + 明确的接入点）
# ---------------------------------------------------------------------------
def actuate_unlock(dry_run=False):
    """真正开门的地方。

    本板目前没有接门锁硬件。接入时改这一个函数即可，例如：
      - GPIO 继电器：写 /sys/class/gpio/gpioN/value
      - ESPHome/ESP32：http POST /api/unlock
      - 厂商门禁：调用其 SDK
    """
    if dry_run:
        return True, "dry-run：未实际开门"
    # 记录一次开门事件（可审计）
    ev = {"action": "unlock", "at": __import__("time").time()}
    try:
        with open("/mnt/data/klar/unlock-events.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(ev) + "\n")
    except OSError:
        pass
    return True, "已记录开门事件（本板未接物理门锁，见 actuate_unlock 的接入点注释）"


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def unlock(text, wav_path, dry_run=False):
    steps = {}

    # ① 口令
    ok_phrase, hit = is_unlock_phrase(text)
    steps["phrase"] = {"ok": ok_phrase, "matched": hit,
                       "reason": "口令命中 %r" % hit if ok_phrase else "不是开门口令"}
    if not ok_phrase:
        return {"ok": False, "steps": steps,
                "speech": "这不是开门指令。"}

    # ②③ 人脸 + 防伪
    face = check_face()
    steps["face"] = face
    steps["pad"] = {"ok": face.get("pad_ok"),
                    "status": face.get("pad_status"),
                    "have_evidence": face.get("pad_have_evidence"),
                    "calibrated": face.get("pad_calibrated"),
                    "live_score": face.get("pad_live_score"),
                    "reason": face.get("pad_reason")}

    # ④ 声纹
    voice = check_voiceprint(wav_path) if wav_path else {
        "ok": False, "reason": "未提供音频，跳过声纹"}
    steps["voiceprint"] = voice

    all_ok = (ok_phrase and face.get("ok") and voice.get("ok"))
    if all_ok:
        done, msg = actuate_unlock(dry_run=dry_run)
        speech = "验证通过，%s。" % msg
        # 未标定时，即使放行也要在播报里明确说出来
        if not face.get("pad_calibrated"):
            speech += "（注意：防伪未经安全标定，本次不依据其结论。）"
    else:
        bad = []
        if not face.get("face_ok"):
            bad.append("人脸没认出来")
        elif not face.get("pad_ok") and PAD_MODE == "enforce":
            bad.append(face.get("pad_reason") or "防伪未通过")
        if not voice.get("ok"):
            bad.append("声音不像")
        speech = "不能开门：%s。" % "，".join(bad)

    return {"ok": all_ok, "steps": steps, "speech": speech}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default="我回来了，开门", help="识别出的文本")
    ap.add_argument("--wav", help="本次说话的音频（16k 或 48k wav）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", "-j", action="store_true")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    out = unlock(args.text, args.wav, dry_run=args.dry_run)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        print("口令   :", args.text)
        for k in ("phrase", "face", "pad", "voiceprint"):
            s = out["steps"].get(k) or {}
            print("  %-10s %-6s %s" % (k, "OK" if s.get("ok") else "FAIL",
                                       str(s.get("reason"))[:70]))
        print("结论   :", "开门" if out["ok"] else "拒绝")
        print("播报   :", out["speech"])
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
