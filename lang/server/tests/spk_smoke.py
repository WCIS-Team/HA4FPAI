#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""spk_smoke.py —— 声纹档（spk / CAM++）的自检。**单独一个文件**，原因有二：

  1. `tests/smoke.py` 的定位是"**纯标准库、无模型、无网络**都能跑"（它自己的头注钉住了这条）。
     声纹这条链要 `sherpa_onnx` ＋ 27 MB 权重，塞进去会破坏那个性质；
  2. 声纹是**默认不入链**的可选件，它的"怎么判过了"和主链不是一回事。

跑法（从 `lang/server/` 目录）：
    python -X utf8 tests/spk_smoke.py          # 模型在 → 连真模型一起验；不在 → 只验接线
    python -X utf8 tests/spk_smoke.py --live   # 模型不在也算 FAIL（板上/CI 用）

判据（模型在位时全绿才算过）：
  * `chain.spk` 默认 `none`；`spk` 在 `Chain.STAGES` 但**不在** `SHERPA_STAGES`（不预热）；
  * `SpkSherpa` 构造**不碰 onnx**（`_ext is None`），`status()` 只读库；
  * 路由点名：`router=echo` 说 `verify:` → 走 spk、结论进 text、LLM 被跳过；
  * `kws_route` 的 `verify:` 表 → `action=verify`；
  * 真模型：同人 1:1 过门限、异人不过；`drop()` 后 `loaded=False`，再验能自动重装。
"""

import io
import os
import sys
import tempfile
import threading
import wave

HERE = os.path.dirname(os.path.abspath(__file__))          # server/tests
SERVER = os.path.dirname(HERE)                             # server/
sys.path.insert(0, SERVER)

import cfg as cfg_mod                                      # noqa: E402
from cfg import Config                                     # noqa: E402
from core import Chain                                     # noqa: E402
from backends import audioio, REGISTRY                     # noqa: E402

PASS = []
LANC = os.path.join(os.path.dirname(SERVER), "vertify", "models",
                    "3dspeaker_speech_campplus_sv_zh-cn_16k-common")
WAV = os.path.join(LANC, "test_wavs")


def ok(name, cond, detail=""):
    PASS.append((name, bool(cond), detail))
    print("[%s] %s %s" % ("ok" if cond else "FAIL", name, detail))


def read_wav(path):
    with wave.open(path, "rb") as w:
        n, ch, sw, sr = w.getnframes(), w.getnchannels(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(n)
    a = [v / 32768.0 for v in __import__("array").array("h", raw[:len(raw) // 2 * 2])]
    if ch > 1:
        a = [sum(a[i:i + ch]) / float(ch) for i in range(0, len(a), ch)]
    return a, sr


def main():
    live = "--live" in sys.argv
    c = Config()
    print("=" * 68)
    print("spk_smoke —— config=%s" % c.path)
    print("=" * 68)

    # ---- 1. 接线与默认档 ------------------------------------------------
    ok("spk 档已登记（sherpa）", "sherpa" in REGISTRY.get("spk", {}),
       str(sorted(REGISTRY.get("spk", {}))))
    ok("chain.spk 默认 none（不入链）", (c.get("chain", "spk", "") or "none") == "none",
       c.get("chain", "spk", ""))
    ok("spk 在 Chain.STAGES", "spk" in Chain.STAGES, str(Chain.STAGES))
    ok("spk 不在 SHERPA_STAGES（不预热/不常驻）", "spk" not in Chain.SHERPA_STAGES,
       str(Chain.SHERPA_STAGES))

    # ---- 2. 构造不装载 --------------------------------------------------
    from backends.spk import SpkSherpa
    tmp = os.path.join(tempfile.gettempdir(), "spk-smoke-%d.json" % os.getpid())
    if os.path.isfile(tmp):
        os.remove(tmp)
    sp = SpkSherpa(c, {"library": tmp}, None)
    st0 = sp.status()
    ok("构造不碰 onnx（_ext is None）", sp._ext is None, str(st0.get("loaded")))
    ok("未装载时 status 只读库", st0.get("loaded") is False and st0.get("speakers") == [],
       str(st0.get("speakers")))
    ok("没装也能卸载（幂等）", sp.drop() is False)
    ok("空库读得动", sp.speakers() == [])

    # ---- 3. 路由点名（假 spk，不碰模型） --------------------------------
    c2 = Config()
    c2.data["chain"].update(router="echo", asr="echo", tts="sine", spk="sherpa")

    class _FakeAsr(object):
        def __init__(self, text):
            self.text = text

        def run(self, segs):
            return self.text, []

    class _FakeSpk(object):
        lock = threading.Lock()

        def __init__(self):
            self.calls = 0

        def run(self, segs):
            self.calls += 1
            return {"match": "alice", "score": 0.81, "scores": {"alice": 0.81},
                    "threshold": 0.5, "speech_s": 0.2, "dim": 192}

        def drop(self):
            return True

        def status(self):
            return {"loaded": False, "speakers": [], "dim": 192}

    fake = _FakeSpk()
    ch = Chain(c2)
    ch._stages[("spk", "sherpa")] = fake
    ch._stages[("asr", "echo")] = _FakeAsr("verify:开门")
    wav_in = audioio.encode_wav(audioio.sine(16000, 0.2, 220.0), 16000)
    out = ch.run_audio(wav_in)
    ok("router verify → 点名 spk 并念结论",
       out.get("route") == "verify" and (out.get("spk") or {}).get("match") == "alice"
       and "alice" in (out.get("text") or "") and bool(out.get("audio_b64"))
       and out.get("llm_ok") is None, str(out.get("text")))
    ok("spk 只被调一次", fake.calls == 1, str(fake.calls))

    # kws_route 的 verify 表
    from backends.kws_route import KwsRoute
    dec = KwsRoute(c, {"verify": {"验证声纹": 1}}, None).run(([], (True, "验证声纹")))
    ok("kws_route verify 表 → action=verify", dec.get("action") == "verify", str(dec))

    # 全链：kws_route 说 verify ⇒ 不跑 vad/asr/router/llm
    c3 = Config()
    c3.data["chain"].update(kws="passthrough", kws_route="default", spk="sherpa",
                            asr="echo", tts="sine")
    ch3 = Chain(c3)
    ch3._stages[("spk", "sherpa")] = _FakeSpk()

    class _KwsVerify(object):
        lock = threading.Lock()

        def run(self, x):
            return {"action": "verify", "route": "spk", "reason": "test"}

    class _MustSkip(object):
        def run(self, *a):
            raise AssertionError("verify 不该跑 vad/asr/router/llm")

    ch3._stages[("kws_route", "default")] = _KwsVerify()
    for name in ("vad", "asr", "router", "llm"):
        ch3._stages[(name, ch3.active(name))] = _MustSkip()
    o = ch3.run_audio(wav_in)
    ok("kws_route verify 全链：跳过 vad/asr/router/llm",
       o.get("route") == "verify" and not any(
           k in (o.get("stages_ms") or {}) for k in ("vad", "asr", "router", "llm")),
       str(list((o.get("stages_ms") or {}).keys())))

    # ---- 4. 真模型（守卫） ----------------------------------------------
    model_ok = os.path.isdir(WAV) and any(f.endswith(".onnx") for f in
                                          os.listdir(LANC) if os.path.isfile(os.path.join(LANC, f)))
    try:
        import sherpa_onnx                                    # noqa: F401
        has_runtime = True
    except Exception:
        has_runtime = False
    if not (model_ok and has_runtime):
        print("[skip] 模型或 sherpa_onnx 不在 → 真模型那 6 项不判（%s）"
              % ("缺模型" if not model_ok else "缺 sherpa_onnx"))
        if live:
            ok("真模型可用（--live）", False, "模型=%s runtime=%s" % (model_ok, has_runtime))
    else:
        prof = {"library": tmp, "threshold": 0.5, "num_threads": 1,
                "idle_unload_s": 0, "min_speech_ms": 600}
        sp = SpkSherpa(Config(), prof, None)
        a1, sr = read_wav(os.path.join(WAV, "fangjun-sr-1.wav"))
        a2, _ = read_wav(os.path.join(WAV, "fangjun-test-sr-1.wav"))
        b1, _ = read_wav(os.path.join(WAV, "leijun-test-sr-1.wav"))
        r = sp.enroll("alice", a1, sr, replace=True)
        ok("真模型：登记 alice", r.get("name") == "alice" and r.get("total") == 1,
           "dim=%s" % r.get("dim"))
        ok("真模型：嵌入 192 维", int(r.get("dim") or 0) == 192, str(r.get("dim")))
        v1 = sp.verify(a2, sr, name="alice")
        ok("真模型：同人 1:1 过门限", v1.get("match") == "alice" and v1.get("score", 0) >= 0.5,
           "score=%.3f" % v1.get("score", 0))
        v2 = sp.verify(b1, sr, name="alice")
        ok("真模型：异人 1:1 不过门限", v2.get("match") == "" and v2.get("score", 1) < 0.5,
           "score=%.3f" % v2.get("score", 0))
        v3 = sp.verify(b1, sr)                                # 1:N：库里只有 alice
        ok("真模型：1:N 不误认", v3.get("match") == "", str(v3.get("scores")))
        ok("真模型：卸载后 loaded=False", sp.drop() is True and sp.status().get("loaded") is False)
        v4 = sp.verify(a2, sr, name="alice")                   # 卸载后再用：应自动重装
        ok("真模型：卸载后按需重装仍能验", v4.get("match") == "alice",
           "score=%.3f" % v4.get("score", 0))
        sp.drop()
        os.remove(tmp) if os.path.isfile(tmp) else None

    fails = [p for p in PASS if not p[1]]
    print("-" * 68)
    print("通过 %d / %d" % (len(PASS) - len(fails), len(PASS)))
    for name, _, detail in fails:
        print("  FAIL: %s %s" % (name, detail))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())