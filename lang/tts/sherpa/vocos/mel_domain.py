"""Isolate the remaining gap: mel domain vs iSTFT.

Take sherpa's wav -> compute log-mel with my analyser -> compare with my matcha
mel at ls=0.82. If they match, the mel is RIGHT and only my iSTFT differs from
vocos' internal one.
"""
import os
import numpy as np
import sherpa_onnx
import onnxruntime as ort

BASE = r"D:\yzd\ttsbench\models"
D = os.path.join(BASE, "matcha-icefall-zh-baker")
SR, NFFT, HOP, NMEL = 22050, 1024, 256, 80
FMIN, FMAX = 0, 8000
WIN = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(NFFT) / NFFT)
TEXT = "你好，我是你的语音助手。"
FINAL_IDS = [1, 1116, 1, 597, 1, 2, 1, 1737, 1, 1518, 1, 1117, 1, 314,
             1, 1872, 1, 1845, 1, 1993, 1, 1522, 1, 3, 1]

def hz2mel(f): return 2595.0 * np.log10(1.0 + f / 700.0)
def mel2hz(m): return 700.0 * (10.0 ** (m / 2595.0) - 1.0)
def mel_fb():
    nb = NFFT // 2 + 1
    pts = mel2hz(np.linspace(hz2mel(FMIN), hz2mel(FMAX), NMEL + 2))
    bins = np.floor((NFFT + 1) * pts / SR).astype(int)
    fb = np.zeros((NMEL, nb), np.float32)
    for m in range(1, NMEL + 1):
        l, c, r = bins[m-1], bins[m], bins[m+1]
        for k in range(l, c): fb[m-1, k] = (k-l)/max(1, c-l)
        for k in range(c, r): fb[m-1, k] = (r-k)/max(1, r-c)
    return fb
FB = mel_fb()

def logmel(x):
    x = np.pad(np.asarray(x, np.float64), (NFFT//2, NFFT//2), mode="reflect")
    T = 1 + (len(x) - NFFT)//HOP
    S = np.empty((T, NFFT//2+1), dtype=np.complex128)
    for t in range(T):
        S[t] = np.fft.rfft(x[t*HOP:t*HOP+NFFT] * WIN)
    P = (np.abs(S)**2) @ FB.T
    return 10.0*np.log10(np.maximum(P, 1e-10))

def resample_time(L, T):
    F, B = L.shape
    src = np.linspace(0, F-1, F)
    dst = np.linspace(0, F-1, T)
    out = np.empty((T, B), np.float32)
    for b in range(B):
        out[:, b] = np.interp(dst, src, L[:, b])
    return out

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
Lref = logmel(ref)                      # from sherpa's OWN audio
print("sherpa wav %d samples -> logmel %s" % (ref.size, Lref.shape))

sm = ort.InferenceSession(os.path.join(D, "model-steps-3.onnx"), providers=["CPUExecutionProvider"])

print("\n%-6s %8s %16s %16s" % ("ls", "mel帧", "原始corr", "时间归一corr"))
for ls in (0.78, 0.80, 0.82, 0.84, 1.00):
    m = sm.run(None, {"x": np.array([FINAL_IDS], np.int64),
                      "x_length": np.array([len(FINAL_IDS)], np.int64),
                      "noise_scale": np.array([0.0], np.float32),
                      "length_scale": np.array([float(ls)], np.float32)})[0]
    mm = np.ascontiguousarray(m[0][:80, :].astype(np.float32)).T   # (T,80) 与 Lref 同向
    n = min(mm.shape[0], Lref.shape[0])
    c1 = float(np.corrcoef(mm[:n].ravel(), Lref[:n].ravel())[0, 1])
    mrn = resample_time(mm, Lref.shape[0])
    c2 = float(np.corrcoef(mrn.ravel(), Lref.ravel())[0, 1])
    print("%-6.2f %8d %16.4f %16.4f" % (ls, mm.shape[0], c1, c2))
