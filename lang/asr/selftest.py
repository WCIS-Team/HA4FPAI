#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""无模型自检：造一支 48 kHz 双声道、"音-静-音-静-音"的合成 wav，喂给 asr_chain.py 的 wav 入口，
检查分段数、段时长、JSONL、心跳文件与"模型槽位空置"的状态字段。全程不碰麦克风、不出声。

用法：
  python asr/selftest.py
"""

import json
import math
import os
import struct
import subprocess
import sys
import tempfile
import wave

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
RATE = 48000
BLOCKS = [("tone", 1.5, 3000), ("sil", 1.2, 30), ("tone", 1.0, 2600),
          ("sil", 1.5, 30), ("tone", 0.6, 3200), ("sil", 1.0, 30)]
EXPECT = [1.5 + 0.6, 1.0 + 0.6, 0.6 + 0.6]
TOTAL = sum(d for _k, d, _a in BLOCKS)


def make_wav(path):
    wf = wave.open(path, "wb")
    wf.setnchannels(2)
    wf.setsampwidth(2)
    wf.setframerate(RATE)
    frames = bytearray()
    phase = 0.0
    for _kind, dur, amp in BLOCKS:
        n = int(RATE * dur)
        for _i in range(n):
            phase += 2.0 * math.pi * 1000.0 / RATE
            v = int(amp * math.sin(phase))
            frames += struct.pack("<hh", v, v)
    wf.writeframes(bytes(frames))
    wf.close()
    return sum(int(d * RATE) for _k, d, _a in BLOCKS) / float(RATE)


def main():
    out = os.path.join(tempfile.gettempdir(), "asr-selftest")
    for sub in ("segs", "models"):
        os.makedirs(os.path.join(out, sub), exist_ok=True)
    wav_path = os.path.join(out, "synth48k2c.wav")
    total = make_wav(wav_path)

    cmd = [sys.executable, os.path.join(HERE, "asr_chain.py"),
           "--source", "wav", "--input", wav_path,
           "--segs-dir", os.path.join(out, "segs"),
           "--jsonl", os.path.join(out, "asr.jsonl"),
           "--state", os.path.join(out, "state"),
           "--model-dir", os.path.join(out, "models")]
    for name in ("asr.jsonl", "state"):
        p = os.path.join(out, name)
        if os.path.exists(p):
            os.remove(p)
    for name in os.listdir(os.path.join(out, "segs")):
        os.remove(os.path.join(out, "segs", name))

    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    text = proc.stdout.decode("utf-8", "replace")
    print(text.rstrip())
    print("[rc] %d" % proc.returncode)

    recs = []
    with open(os.path.join(out, "asr.jsonl")) as fh:
        for line in fh:
            if line.strip():
                recs.append(json.loads(line))
    state = json.load(open(os.path.join(out, "state")))

    durs = [r["dur_s"] for r in recs]
    ok = True
    checks = [
        ("返回码 0", proc.returncode == 0),
        ("音频总时长 %.2f s" % total, abs(total - TOTAL) < 0.02),
        ("段数 %d == %d" % (len(recs), len(EXPECT)), len(recs) == len(EXPECT)),
        ("段时长 %s 与预期 %s 每段差 <0.25 s" % (durs, EXPECT),
         len(durs) == len(EXPECT) and all(abs(a - b) < 0.25 for a, b in zip(durs, EXPECT))),
        ("每段峰值 > -30 dBFS（音段被判成语音）", all(r["peak_dbfs"] > -30.0 for r in recs)),
        ("识别状态 no_model", all(r["asr_status"] == "no_model" for r in recs)),
        ("心跳 model_ready False", state.get("model_ready") is False),
        ("心跳 backend == slot-empty", state.get("backend") == "slot-empty"),
        ("心跳 stop == eof", state.get("stop") == "eof"),
        ("心跳 frames > 0", state.get("frames", 0) > 0),
        ("段 wav 落盘数量一致", len(os.listdir(os.path.join(out, "segs"))) == len(recs)),
    ]
    for label, passed in checks:
        print("%s %s" % ("PASS" if passed else "FAIL", label))
        ok = ok and passed

    seg = os.path.join(out, "segs", sorted(os.listdir(os.path.join(out, "segs")))[0])
    wf = wave.open(seg, "rb")
    down = abs(wf.getframerate() - 16000) < 1 and wf.getnchannels() == 1 and wf.getsampwidth() == 2
    print("%s 段音频＝16 kHz 单声道 16 bit（48k 双声道已降采样＋取一路）" % ("PASS" if down else "FAIL"))
    wf.close()
    ok = ok and down

    print("[out] %s" % out)
    print("SELFTEST %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
