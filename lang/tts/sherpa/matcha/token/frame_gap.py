import os
import numpy as np
import sherpa_onnx
import onnxruntime as ort

BASE = r"D:\yzd\ttsbench\models"
D = os.path.join(BASE, "matcha-icefall-zh-baker")

lex, tok = {}, {}
for ln in open(os.path.join(D, "lexicon.txt"), encoding="utf-8"):
    p = ln.rstrip("\n").split()
    if len(p) >= 2:
        lex[p[0]] = p[1:]
for ln in open(os.path.join(D, "tokens.txt"), encoding="utf-8"):
    p = ln.rstrip("\n").split()
    if len(p) >= 2:
        try:
            tok[p[0]] = int(p[-1])
        except ValueError:
            pass

TEXT = "你好，我是你的语音助手。"
ids = []
for ch in TEXT:
    if ch in "，。、！？":
        continue
    ph = lex.get(ch)
    if ph:
        ids += [tok[q] for q in ph if q in tok]

sm = ort.InferenceSession(os.path.join(D, "model-steps-3.onnx"),
                          providers=["CPUExecutionProvider"])
x = np.array([ids], np.int64)
xl = np.array([len(ids)], np.int64)

print("tokens=%d  ids=%s" % (len(ids), ids))
print("lexicon entries for each char:")
for ch in TEXT:
    if ch in "，。、！？":
        continue
    print("   %s -> %s" % (ch, lex.get(ch)))

print()
print("frame count vs length_scale (ns=0):")
for ls in (1.0, 1.2, 1.4, 1.6, 1.8, 1.86, 2.0):
    m = sm.run(None, {"x": x, "x_length": xl,
                      "noise_scale": np.array([0.0], np.float32),
                      "length_scale": np.array([float(ls)], np.float32)})[0]
    print("   ls=%.2f -> %d frames" % (ls, m.shape[-1]))

print()
print("sherpa native (speed=1.0): 2.29 s = %d frames" % int(round(2.29 * 22050 / 256)))
print("ratio = %.2f" % (197 / 106))
print()
print("=> если sherpa uses ls=1.0 and gets 197 frames from the SAME 10 tokens,")
print("   then matcha-icefall-zh-baker's ONNX must see MORE tokens than 10.")
print("   Check: maybe sherpa feeds tokens WITH the leading blank/id-0?")
print("   token id 0 =", tok.get("0"), " '_' =", tok.get("_"))
