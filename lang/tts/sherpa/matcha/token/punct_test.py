import os
import numpy as np
import onnxruntime as ort

D = r"D:\yzd\ttsbench\models\matcha-icefall-zh-baker"
sm = ort.InferenceSession(os.path.join(D, "model-steps-3.onnx"),
                          providers=["CPUExecutionProvider"])

tok = {}
for ln in open(os.path.join(D, "tokens.txt"), encoding="utf-8"):
    p = ln.rstrip("\n").split()
    if len(p) >= 2:
        try:
            tok[p[0]] = int(p[-1])
        except ValueError:
            pass

# punctuation tokens?
for probe in [",", ".", "0", "_", "!" , "?", ":", '"', "'"]:
    print("punct %-3r id=%s" % (probe, tok.get(probe)))

lex = {}
for ln in open(os.path.join(D, "lexicon.txt"), encoding="utf-8"):
    p = ln.rstrip("\n").split()
    if len(p) >= 2:
        lex[p[0]] = p[1:]

def frames(ids, ls=1.0):
    m = sm.run(None, {"x": np.array([ids], np.int64),
                      "x_length": np.array([len(ids)], np.int64),
                      "noise_scale": np.array([0.0], np.float32),
                      "length_scale": np.array([float(ls)], np.float32)})[0]
    return m.shape[-1]

def syl_ids(text, with_punct=True):
    ids = []
    for ch in text:
        if ch in "，。、！？":
            if with_punct and ch in tok:
                ids.append(tok[ch])
            continue
        ph = lex.get(ch)
        if ph:
            for q in ph:
                if q in tok:
                    ids.append(tok[q])
                else:
                    # try without tone
                    import re
                    m = re.match(r"^([a-z]+)([0-9])$", q)
                    if m and m.group(1) in tok:
                        ids.append(tok[m.group(1)])
    return ids

TEXT = "你好，我是你的语音助手。"
for tag, wp in (("no punct", False), ("with punct", True)):
    for ls in (1.0, 1.5, 1.86):
        ids = syl_ids(TEXT, wp)
        print("%-12s ls=%.2f tokens=%2d frames=%3d"
              % (tag, ls, len(ids), frames(ids, ls)))
print()
print("target: sherpa 2.29 s = 197 frames")
