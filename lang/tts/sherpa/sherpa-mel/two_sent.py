"""sherpa 把文本切成 2 句，每句独立跑 matcha，再拼起来。

验证：分别对 "你好"(2 音节) 和 "我是你的语音助手"(8 音节) 建 token，
各加 add_blank，各自跑 matcha+vocos，拼接后与 sherpa 波形比。
"""
import os
import numpy as np
import sherpa_onnx
import onnxruntime as ort

BASE = r"D:\yzd\ttsbench\models"
D = os.path.join(BASE, "matcha-icefall-zh-baker")
SR, NFFT, HOP = 22050, 1024, 256
WIN = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(NFFT) / NFFT)
TEXT = "你好，我是你的语音助手。"
PAD = 1

tok = {}
with open(os.path.join(D, "tokens.txt"), encoding="utf-8") as f:
    for line in f:
        p = line.strip().split()
        if len(p) == 2:
            tok[p[0]] = int(p[1])
lex = {}
with open(os.path.join(D, "lexicon.txt"), encoding="utf-8") as f:
    for line in f:
        p = line.strip().split()
        if len(p) >= 2:
            lex[p[0]] = p[1:]

def ids_of(text):
    out = []
    for ch in text:
        if ch in "，。、！？,.!?;:":
            continue
        for k in lex.get(ch, []):
            if k in tok:
                out.append(tok[k])
    return out

def add_blank(ids, pad=PAD):
    a = [pad]
    for i in ids:
        a += [i, pad]
    return a

# sherpa 的两句（按 ， 切开）
S1 = "你好"
S2 = "我是你的语音助手"
ids1 = ids_of(S1)
ids2 = ids_of(S2)
print("句1 %s -> %d 音节 %s" % (S1, len(ids1), ids1))
print("句2 %s -> %d 音节 %s" % (S2, len(ids2), ids2))
print("加 blank 后: 句1=%d 句2=%d 总计=%d"
      % (len(add_blank(ids1)), len(add_blank(ids2)),
         len(add_blank(ids1)) + len(add_blank(ids2))))

def istft(mag, cos, sin):
    nb = mag.shape[1]
    spec = np.zeros((mag.shape[0], NFFT), dtype=np.complex128)
    spec[:, :nb] = mag * cos + 1j * (mag * sin)
    spec[:, nb:] = np.conj(spec[:, 1:nb - 1][:, ::-1])
    fr = np.fft.irfft(spec, n=NFFT, axis=1).astype(np.float32)
    n = HOP * (fr.shape[0] - 1) + NFFT
    out = np.zeros(n, np.float32); ws = np.zeros(n, np.float32)
    for i in range(fr.shape[0]):
        p = i * HOP
        out[p:p + NFFT] += fr[i] * WIN
        ws[p:p + NFFT] += WIN * WIN
    y = out / np.maximum(ws, 1e-8)
    return y[NFFT // 2:n - NFFT // 2]

# sherpa 参考
tts = sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(
    model=sherpa_onnx.OfflineTtsModelConfig(
        matcha=sherpa_onnx.OfflineTtsMatchaModelConfig(
            acoustic_model=os.path.join(D, "model-steps-3.onnx"),
            vocoder=os.path.join(BASE, "vocos-22khz-univ.onnx"),
            tokens=os.path.join(D, "tokens.txt"),
            lexicon=os.path.join(D, "lexicon.txt"),
            dict_dir=os.path.join(D, "dict"),
            noise_scale=0.0),
        num_threads=1), max_num_sentences=1))
ref = np.asarray(tts.generate(TEXT, sid=0, speed=1.0).samples, np.float32)
print("\nsherpa: %d samples = %.3f s" % (ref.size, ref.size / SR))

sm = ort.InferenceSession(os.path.join(D, "model-steps-3.onnx"), providers=["CPUExecutionProvider"])
sv = ort.InferenceSession(os.path.join(BASE, "vocos-22khz-univ.onnx"), providers=["CPUExecutionProvider"])

def run_sent(ids):
    m = sm.run(None, {"x": np.array([ids], np.int64),
                      "x_length": np.array([len(ids)], np.int64),
                      "noise_scale": np.array([0.0], np.float32),
                      "length_scale": np.array([1.0], np.float32)})[0]
    mm = np.ascontiguousarray(m[0][:80, :].astype(np.float32))
    o = sv.run(None, {"mels": mm[None].astype(np.float32)})
    w = istft(o[0][0].T, o[1][0].T, o[2][0].T)
    return mm, w

print("\n%-24s %8s %8s" % ("方案", "mel帧", "音频s"))
m1, w1 = run_sent(add_blank(ids1))
m2, w2 = run_sent(add_blank(ids2))
print("%-24s %8d %8.3f" % ("句1 加blank", m1.shape[1], w1.size / SR))
print("%-24s %8d %8.3f" % ("句2 加blank", m2.shape[1], w2.size / SR))
w_cat = np.concatenate([w1, w2])
print("%-24s %8d %8.3f" % ("拼接", m1.shape[1] + m2.shape[1], w_cat.size / SR))

n = min(ref.size, w_cat.size)
c = float(np.corrcoef(ref[:n], w_cat[:n])[0, 1])
print("\ncorr(sherpa, 两句拼接) = %.6f    (n=%d)" % (c, n))

# 也比一下不加 blank 的两句
m1r, w1r = run_sent(ids1)
m2r, w2r = run_sent(ids2)
wr = np.concatenate([w1r, w2r])
n2 = min(ref.size, wr.size)
print("corr(sherpa, 两句不加blank) = %.6f  (%.3f s)"
      % (float(np.corrcoef(ref[:n2], wr[:n2])[0, 1]), wr.size / SR))
