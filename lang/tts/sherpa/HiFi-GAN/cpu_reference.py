"""Generate the HiFi-GAN CPU reference on the SAME mel, so the comparison is
NPU-hifigan vs CPU-hifigan (not vs CPU-vocos, which was my earlier mistake).
"""
import os
import glob
import struct
import numpy as np
import onnxruntime as ort

BASE = r"D:\yzd\ttsbench\models"
SD = r"D:\yzd\ttsbench\samples"
OUT = r"D:\yzd\ttsbench\hifi_cpu"
SR = 22050
os.makedirs(OUT, exist_ok=True)

MODEL = r"D:\yzd\ttsbench\hifigan_v2.onnx"

def wav(p, x, sr=SR):
    x = np.asarray(x, np.float32)
    pk = np.abs(x).max()
    if pk > 0.99:
        x = x / pk * 0.99
    pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    with open(p, "wb") as f:
        f.write(b"RIFF"); f.write(struct.pack("<I", 36 + pcm.size * 2))
        f.write(b"WAVEfmt "); f.write(struct.pack("<IHHIIHH", 16, 1, 1, sr, sr*2, 2, 16))
        f.write(b"data"); f.write(struct.pack("<I", pcm.size * 2)); f.write(pcm.tobytes())

so = ort.InferenceSession(MODEL, providers=["CPUExecutionProvider"])
print("model inputs:", [(i.name, i.shape) for i in so.get_inputs()])
print("model outputs:", [(o.name, o.shape) for o in so.get_outputs()])

for melp in sorted(glob.glob(os.path.join(SD, "*_exact_mel.bin"))):
    tag = os.path.basename(melp).replace("_exact_mel.bin", "")
    mel = np.fromfile(melp, np.float32).reshape(80, -1)
    T = min(mel.shape[1], 600)
    inp = mel[None, :80, :T].astype(np.float32)
    o = so.run(None, {so.get_inputs()[0].name: inp})[0]
    w = np.asarray(o, np.float32).ravel()[:T * 256]
    p = os.path.join(OUT, tag + "_hifigan_cpu.wav")
    wav(p, w)
    print("%-10s T=%3d  out=%s  wav=%.2fs rms=%.4f"
          % (tag, T, o.shape, w.size / SR, float(np.sqrt((w ** 2).mean()))))
print("done ->", OUT)
