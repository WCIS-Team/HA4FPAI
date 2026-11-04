"""Find the exact sherpa token sequence by matching the frame count (197-198).

AddBlank has several plausible shapes; test them all against the target.
"""
import os
import numpy as np
import onnxruntime as ort

D = r"D:\yzd\ttsbench\models\matcha-icefall-zh-baker"
IDS = [1117, 597, 1737, 1518, 1117, 314, 1872, 1845, 1993, 1522]
PAD = 1

sm = ort.InferenceSession(os.path.join(D, "model-steps-3.onnx"),
                          providers=["CPUExecutionProvider"])

def frames(ids):
    m = sm.run(None, {"x": np.array([ids], np.int64),
                      "x_length": np.array([len(ids)], np.int64),
                      "noise_scale": np.array([0.0], np.float32),
                      "length_scale": np.array([1.0], np.float32)})[0]
    return m.shape[-1]

def inter_both(ids, pad=PAD):
    a = [pad]
    for i in ids:
        a += [i, pad]
    return a

def inter_trail(ids, pad=PAD):
    a = []
    for i in ids:
        a += [i, pad]
    return a

def inter_lead(ids, pad=PAD):
    a = [pad]
    for i in ids:
        a += [i]
    return a

def inter_only(ids, pad=PAD):
    a = [ids[0]]
    for i in ids[1:]:
        a += [pad, i]
    return a

variants = {
    "both 2n+1=21": inter_both(IDS),
    "trail 2n=20": inter_trail(IDS),
    "lead n+1=11": inter_lead(IDS),
    "only 2n-1=19": inter_only(IDS),
    "both+pad=22": inter_both(IDS) + [PAD],
    "pad+both=22": [PAD] + inter_both(IDS),
    "both+pad+pad=23": inter_both(IDS) + [PAD, PAD],
    "pad+trail=21": [PAD] + inter_trail(IDS),
    "trail+pad=21": inter_trail(IDS) + [PAD],
}

print("target frames = 197 (sherpa wav 2.29s / 256 hop = 197.5)")
print("%-20s %6s %8s %6s" % ("variant", "tokens", "frames", "diff"))
best = None
for name, ids in variants.items():
    fr = frames(ids)
    print("%-20s %6d %8d %+6d" % (name, len(ids), fr, fr - 197))
    d = abs(fr - 197)
    if best is None or d < best[1]:
        best = (name, d, ids, fr)

print("\nBEST: %s -> %d frames (tokens=%d)" % (best[0], best[3], len(best[2])))
print("ids:", best[2])
