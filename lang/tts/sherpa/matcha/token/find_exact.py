"""Find the exact token sequence that gives 197 frames (== sherpa's duration).

Brute-force a few plausible variants around the add_blank pattern.
"""
import os
import numpy as np
import onnxruntime as ort

D = r"D:\yzd\ttsbench\models\matcha-icefall-zh-baker"
tok = {}
with open(os.path.join(D, "tokens.txt"), encoding="utf-8") as f:
    for line in f:
        p = line.strip().split()
        if len(p) == 2:
            tok[p[0]] = int(p[1])
        elif len(p) == 1:
            tok[" "] = int(p[0])

BASE_IDS = [1117, 597, 1737, 1518, 1117, 314, 1872, 1845, 1993, 1522]  # 10 syllables
PAD = tok.get("#0", 1)
DOT = tok.get(".", None)
COMMA = tok.get(",", None)
print("PAD=%s DOT=%s COMMA=%s" % (PAD, DOT, COMMA))

def add_blank(ids, pad=PAD):
    ans = [pad]
    for i in ids:
        ans.append(i); ans.append(pad)
    return ans

sm = ort.InferenceSession(os.path.join(D, "model-steps-3.onnx"),
                          providers=["CPUExecutionProvider"])

def frames(ids):
    m = sm.run(None, {"x": np.array([ids], np.int64),
                      "x_length": np.array([len(ids)], np.int64),
                      "noise_scale": np.array([0.0], np.float32),
                      "length_scale": np.array([1.0], np.float32)})[0]
    return m.shape[-1]

variants = {
    "raw10": BASE_IDS,
    "blank21": add_blank(BASE_IDS),
    "blank21+dot": add_blank(BASE_IDS) + [DOT],
    "blank21+comma+dot": add_blank(BASE_IDS) + [COMMA, DOT],
    "blank+dot(23)": add_blank(BASE_IDS + [DOT]),
    "blank+comma,dot(25)": add_blank(BASE_IDS + [COMMA, DOT]),
    "blank then sil?": add_blank(BASE_IDS),
}
print("\ntarget = 197 frames")
best = None
for name, ids in variants.items():
    if any(i is None for i in ids):
        continue
    fr = frames(ids)
    print("  %-22s tokens=%2d frames=%3d  diff=%+d" % (name, len(ids), fr, fr - 197))
    if best is None or abs(fr - 197) < best[1]:
        best = (name, abs(fr - 197), ids, fr)

print("\nclosest:", best[0], "frames=", best[3], "tokens=", len(best[2]))
print("ids:", best[2])
