"""Feed sherpa's EXACT final IDs to matcha and verify against sherpa's audio."""
import os
import numpy as np
import sherpa_onnx
import onnxruntime as ort

BASE = r"D:\yzd\ttsbench\models"
D = os.path.join(BASE, "matcha-icefall-zh-baker")
SR, NFFT, HOP = 22050, 1024, 256
WIN = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(NFFT) / NFFT)
TEXT = "你好，我是你的语音助手。"

# sherpa 2.0.1 debug 打印出来的最终 ID（25 个，含 blank）
FINAL_IDS = [1, 1116, 1, 597, 1, 2, 1, 1737, 1, 1518, 1, 1117, 1, 314,
             1, 1872, 1, 1845, 1, 1993, 1, 1522, 1, 3, 1]
print("final ids (%d): %s" % (len(FINAL_IDS), FINAL_IDS))

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
print("sherpa 2.0.1: %d samples = %.3f s" % (ref.size, ref.size / SR))

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

sm = ort.InferenceSession(os.path.join(D, "model-steps-3.onnx"), providers=["CPUExecutionProvider"])
sv = ort.InferenceSession(os.path.join(BASE, "vocos-22khz-univ.onnx"), providers=["CPUExecutionProvider"])

m = sm.run(None, {"x": np.array([FINAL_IDS], np.int64),
                  "x_length": np.array([len(FINAL_IDS)], np.int64),
                  "noise_scale": np.array([0.0], np.float32),
                  "length_scale": np.array([1.0], np.float32)})[0]
mm = np.ascontiguousarray(m[0][:80, :].astype(np.float32))
print("my mel: %s  (%.3f s)" % (mm.shape, mm.shape[1] * HOP / SR))

o = sv.run(None, {"mels": mm[None].astype(np.float32)})
w = istft(o[0][0].T, o[1][0].T, o[2][0].T)
print("my wav:  %d samples = %.3f s" % (w.size, w.size / SR))

n = min(ref.size, w.size)
c = float(np.corrcoef(ref[:n], w[:n])[0, 1])
print("\n>>> corr(sherpa, mine with EXACT ids) = %.6f" % c)
mm.tofile(r"D:\yzd\ttsbench\samples\EXACT_mel.bin")
print("saved EXACT_mel.bin")
