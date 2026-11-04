"""Time-normalised content comparison.

Resample both log-mels to the same number of frames, then correlate. If the
token stream is right, content must match even when durations differ.
Also reports the per-frame best-match (DTW-lite) to expose stretching.
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
IDS = [1117, 597, 1737, 1518, 1117, 314, 1872, 1845, 1993, 1522]
PAD = 1

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
    """linear-interpolate the frame axis of (F,80) to T frames"""
    F, B = L.shape
    src = np.linspace(0, F-1, F)
    dst = np.linspace(0, F-1, T)
    out = np.empty((T, B), np.float32)
    for b in range(B):
        out[:, b] = np.interp(dst, src, L[:, b])
    return out

def istft(mag, cos, sin):
    nb = mag.shape[1]
    spec = np.zeros((mag.shape[0], NFFT), dtype=np.complex128)
    spec[:, :nb] = mag*cos + 1j*(mag*sin)
    spec[:, nb:] = np.conj(spec[:, 1:nb-1][:, ::-1])
    fr = np.fft.irfft(spec, n=NFFT, axis=1).astype(np.float32)
    n = HOP*(fr.shape[0]-1)+NFFT
    out = np.zeros(n, np.float32); ws = np.zeros(n, np.float32)
    for i in range(fr.shape[0]):
        p = i*HOP
        out[p:p+NFFT] += fr[i]*WIN
        ws[p:p+NFFT] += WIN*WIN
    y = out/np.maximum(ws, 1e-8)
    return y[NFFT//2:n-NFFT//2]

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
Lr = logmel(ref)
print("sherpa logmel:", Lr.shape)

sm = ort.InferenceSession(os.path.join(D, "model-steps-3.onnx"), providers=["CPUExecutionProvider"])
sv = ort.InferenceSession(os.path.join(BASE, "vocos-22khz-univ.onnx"), providers=["CPUExecutionProvider"])

def both(ids, pad=PAD):
    a=[pad]
    for i in ids: a += [i,pad]
    return a

print("\n%-16s %6s %8s %14s" % ("variant","tokens","frames","time-norm corr"))
for name, ids in (("raw(10)", IDS), ("both(21)", both(IDS)), ("both+pad(22)", both(IDS)+[PAD])):
    m = sm.run(None, {"x": np.array([ids], np.int64),
                      "x_length": np.array([len(ids)], np.int64),
                      "noise_scale": np.array([0.0], np.float32),
                      "length_scale": np.array([1.0], np.float32)})[0]
    mm = np.ascontiguousarray(m[0][:80,:].astype(np.float32))
    o = sv.run(None, {"mels": mm[None].astype(np.float32)})
    w = istft(o[0][0].T, o[1][0].T, o[2][0].T)
    L = logmel(w)
    Ln = resample_time(L, Lr.shape[0])
    c = float(np.corrcoef(Ln.ravel(), Lr.ravel())[0,1])
    print("%-16s %6d %8d %14.4f" % (name, len(ids), mm.shape[1], c))
