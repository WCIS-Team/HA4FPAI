# -*- coding: utf-8 -*-
"""Extract PRelu NPU output and compare against the hifigan CPU reference."""
import os, glob, json, struct
import numpy as np

SD = "/root/vocos_run/prelu"
WAV = r"D:\yzd\ttsbench"          # not used on board; we write locally
SR = 22050

VJ = "/root/vocos_run/prelu/hifigan_v2_prelu_ZG.json"
j = json.load(open(VJ))
vids = [v["v_id"] for o in j["ops"] if "Output" in str(o["_type_key"]) for v in o["inputs"]]
print("output v_ids:", vids)

def wav(path, x, sr=SR):
    x = np.asarray(x, np.float32)
    pk = np.abs(x).max()
    if pk > 0.99:
        x = x / pk * 0.99
    pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    with open(path, "wb") as f:
        f.write(b"RIFF"); f.write(struct.pack("<I", 36 + pcm.size * 2))
        f.write(b"WAVEfmt "); f.write(struct.pack("<IHHIIHH", 16, 1, 1, sr, sr * 2, 2, 16))
        f.write(b"data"); f.write(struct.pack("<I", pcm.size * 2)); f.write(pcm.tobytes())

def rms(x):
    x = np.asarray(x, np.float32)
    return float(np.sqrt((x ** 2).mean()))

for melp in sorted(glob.glob("/root/vocos_run/exact/*_exact_mel.bin")):
    tag = os.path.basename(melp).replace("_exact_mel.bin", "")
    T = min(np.fromfile(melp, np.float32).reshape(80, -1).shape[1], 600)
    d = glob.glob(os.path.join(SD, "d_" + tag, "*", "ftmpHQB"))
    if not d:
        print("%-10s NO DUMP" % tag)
        continue
    n = 1
    for ext in ("", ".ftmp"):
        if os.path.exists(os.path.join(d[0], "%d%s" % (vids[0], ext))):
            n = vids[0]
            break
    cand = os.path.join(d[0], "%d.ftmp" % vids[0])
    if not os.path.exists(cand):
        print("%-10s no %d.ftmp, files=%s" % (tag, vids[0], os.listdir(d[0])[:4]))
        continue
    raw = np.fromfile(cand, np.float32)
    w = raw.ravel()[:T * 256]
    p = os.path.join(SD, "%s_prelu_npu.wav" % tag)
    wav(p, w)
    print("%-10s T=%3d raw=%d  wav=%.2fs rms=%.4f  -> %s"
          % (tag, T, raw.size, w.size / SR, rms(w), p))
print("done")