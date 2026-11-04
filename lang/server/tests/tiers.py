#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tiers.py —— 真档的**装载实测**（不看代码，看能不能真跑起来）。

与 smoke.py 的分工：smoke 验「契约与编排」（全用自检档，不依赖任何模型）；
本文件验「真档能不能在本机装起来并出结果」——webrtc / rms / silero / kws(sherpa) 四档。

    python -X utf8 tests/tiers.py

每档打印：装上了没有、跑一次出了什么、失败原因是什么（缺包/缺模型/缺 .so 都明说）。
不外发、不装任何东西、不改 PATH。
"""

import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))          # lang/ 根（vad/、kws/ 在那儿）

from backends import audioio                                  # noqa: E402
from cfg import Config                                        # noqa: E402
from core import Chain, WORK_RATE                             # noqa: E402

RESULTS = []


def probe(stage, profile_name, cfg_overrides=None, wav=None, label=None):
    """装某一档并跑一次；返回 (ok, 说明)。"""
    label = label or ("%s/%s" % (stage, profile_name))
    cfg = Config()
    cfg.data.setdefault("chain", {})
    cfg.data["chain"][stage] = profile_name
    for sec, kv in (cfg_overrides or {}).items():
        cfg.data.setdefault(sec, {}).update(kv)
    chain = Chain(cfg)
    try:
        inst = chain.stage(stage)
    except Exception as exc:
        detail = ("%s: %s" % (type(exc).__name__, exc))[:220]
        RESULTS.append((label or profile_name, "LOAD-FAIL", detail))
        return False, detail
    try:
        audio = wav if wav is not None else audioio.encode_wav(
            audioio.sine(WORK_RATE, 2.0, 300.0), WORK_RATE)
        samples, _ = audioio.decode_payload(audio)
        # 入参形状按契约走：vad 吃整段 samples；kws/asr 吃 [seg] 列表
        res = inst.run(samples) if stage == "vad" else inst.run([samples])
        # 各级的返回值形状不同：vad → [段...]；kws → (hit, tag)；其余 → 自定义
        if isinstance(res, tuple):
            detail = "跑通：返回 %s" % (res,)
        elif hasattr(res, "__len__"):
            detail = "跑通：切出 %d 段" % len(res)
        else:
            detail = "跑通：返回 %r" % (res,)
        RESULTS.append((label or profile_name, "OK", detail))
        return True, detail
    except Exception as exc:
        detail = ("%s: %s" % (type(exc).__name__, exc))[:220]
        RESULTS.append((label or profile_name, "RUN-FAIL", detail))
        return False, detail


def main():
    print("=" * 72)
    print("lang/server 真档装载实测 —— python %s" % sys.version.split()[0])
    print("=" * 72)

    # 1) vad: rms（纯标准库，零依赖）—— 期望 OK
    ok, d = probe("vad", "rms")
    print("[%s] vad/rms           %s" % ("ok" if ok else "!!", d))

    # 2) vad: webrtc（复用 lang/vad/webrtc/，含板上 aarch64 .so；本机 win 无 .so ⇒ 预期失败且说清）
    ok, d = probe("vad", "webrtc")
    print("[%s] vad/webrtc        %s" % ("ok" if ok else "!!", d))

    # 3) vad: silero（raw-ORT 档——单运行时模式下被禁，这里显式关掉才测得到）
    ok, d = probe("vad", "silero",
                  cfg_overrides={"env": {"single_runtime": "false",
                                         "allow_pip_onnxruntime": "true"}},
                  label="vad/silero(第二运行时)")
    print("[%s] vad/silero        %s" % ("ok" if ok else "!!", d))

    # 4) kws: sherpa（复用 lang/kws/models/ 的 int8 副本；没装 sherpa_onnx 就明说）
    ok, d = probe("kws", "sherpa")
    print("[%s] kws/sherpa        %s" % ("ok" if ok else "!!", d))

    # 5) vad: sherpa（板端现役档，需板上 wheel 的 Vad 类；本机 pip 版没有 ⇒ 预期失败且说清）
    ok, d = probe("vad", "sherpa")
    print("[%s] vad/sherpa        %s" % ("ok" if ok else "!!", d))

    # 6) asr: sherpa（本地流式 zipformer，模型在 /mnt/data ；本机没有 ⇒ 预期 LOAD-FAIL 且说清）
    ok, d = probe("asr", "sherpa")
    print("[%s] asr/sherpa        %s" % ("ok" if ok else "!!", d))

    # 7) tts: sherpa（本地 matcha+vocos，模型在 /mnt/data ；本机没有 ⇒ 预期 LOAD-FAIL 且说清）
    ok, d = probe("tts", "sherpa")
    print("[%s] tts/sherpa        %s" % ("ok" if ok else "!!", d))

    print("-" * 72)
    n_ok = sum(1 for _, s, _ in RESULTS if s == "OK")
    for name, status, detail in RESULTS:
        print("  %-16s %-10s %s" % (name, status, detail))
    print("装载成功 %d / %d" % (n_ok, len(RESULTS)))
    print("（失败不算 bug：缺 webrtcvad 的 win 版／缺板端模型的档会明说缺什么，"
          "板上跑才是最终判据）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
