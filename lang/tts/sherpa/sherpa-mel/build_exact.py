"""Use sherpa's OWN tokenizer (version 2.0.1 debug print) to get exact IDs,
then run matcha ourselves to get the mel. This is "use its frontend", not
reimplement it.

Outputs per sentence: <tag>_exact_mel.bin  and  <tag>_cpu.wav
"""
import os
import re
import subprocess
import sys
import numpy as np
import onnxruntime as ort

BASE = r"D:\yzd\ttsbench\models"
D = os.path.join(BASE, "matcha-icefall-zh-baker")
OUT = r"D:\yzd\ttsbench\samples"

# Interpreter for the HELPER subprocess (sherpa_onnx comes in via PYTHONPATH
# in sherpa_ids()). Resolution: env WCIS_TTS_PY -> this script's interpreter.
# History: the school box's PATH python is a Store stub, so the live ASR
# venv's python (D:\yzd\asr\onnx\env\Scripts\python.exe) was borrowed as a
# ready-made real interpreter -- nothing is installed into or imported from it.
SUB_PY = os.environ.get("WCIS_TTS_PY") or sys.executable
SR, NFFT, HOP = 22050, 1024, 256
WIN = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(NFFT) / NFFT)

SENTS = [
    ("01_问候", "你好，我是你的语音助手。"),
    ("02_天气", "今天天气不错，适合出去走走。"),
    ("03_数字", "现在时间是下午三点二十分。"),
    ("04_长句", "请帮我把客厅的灯关掉，然后打开窗户。"),
]

HELPER = r"D:\yzd\ttsbench\get_ids.py"
with open(HELPER, "w", encoding="utf-8") as f:
    f.write('''
import os, sys, sherpa_onnx
BASE = r"D:\\\\yzd\\\\ttsbench\\\\models"
D = os.path.join(BASE, "matcha-icefall-zh-baker")
TEXT = sys.argv[1]
mc = sherpa_onnx.OfflineTtsModelConfig(
    matcha=sherpa_onnx.OfflineTtsMatchaModelConfig(
        acoustic_model=os.path.join(D, "model-steps-3.onnx"),
        vocoder=os.path.join(BASE, "vocos-22khz-univ.onnx"),
        tokens=os.path.join(D, "tokens.txt"),
        lexicon=os.path.join(D, "lexicon.txt"),
        dict_dir=os.path.join(D, "dict"),
        noise_scale=0.0),
    num_threads=1, debug=True)
tts = sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(model=mc, max_num_sentences=1))
tts.generate(TEXT, sid=0, speed=1.0)
''')

def sherpa_ids(text):
    p = subprocess.run(
        [SUB_PY, "-u", "-X", "utf8", HELPER, text],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**os.environ, "PYTHONPATH": r"D:\yzd\ttsbench\sh_new"})
    ids, frames = [], []
    for line in p.stderr.splitlines():
        m = re.search(r"final IDs \((\d+)\): \[([-\d,\s]+)\]", line)
        if m:
            ids.append([int(x) for x in m.group(2).split(",")])
        m2 = re.search(r"Sentence (\d+) tokens: \[(.*)\]", line)
        if m2:
            frames.append(m2.group(2))
    return ids, frames

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

def wav_write(path, x, sr=SR):
    import struct
    x = np.asarray(x, np.float32)
    pk = np.abs(x).max()
    if pk > 0.99:
        x = x / pk * 0.99
    pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    with open(path, "wb") as f:
        f.write(b"RIFF"); f.write(struct.pack("<I", 36 + pcm.size * 2))
        f.write(b"WAVEfmt "); f.write(struct.pack("<IHHIIHH", 16, 1, 1, sr, sr*2, 2, 16))
        f.write(b"data"); f.write(struct.pack("<I", pcm.size * 2)); f.write(pcm.tobytes())

sm = ort.InferenceSession(os.path.join(D, "model-steps-3.onnx"), providers=["CPUExecutionProvider"])
sv = ort.InferenceSession(os.path.join(BASE, "vocos-22khz-univ.onnx"), providers=["CPUExecutionProvider"])

for tag, text in SENTS:
    ids_list, tok_lines = sherpa_ids(text)
    if not ids_list:
        print("%-8s NO IDS" % tag); continue
    mel_all = []
    for ids in ids_list:
        m = sm.run(None, {"x": np.array([ids], np.int64),
                          "x_length": np.array([len(ids)], np.int64),
                          "noise_scale": np.array([0.0], np.float32),
                          "length_scale": np.array([1.0], np.float32)})[0]
        mel_all.append(m[0][:80, :])
    mm = np.ascontiguousarray(np.concatenate(mel_all, axis=1).astype(np.float32))
    mm.tofile(os.path.join(OUT, tag + "_exact_mel.bin"))
    o = sv.run(None, {"mels": mm[None].astype(np.float32)})
    w = istft(o[0][0].T, o[1][0].T, o[2][0].T)
    p = os.path.join(OUT, tag + "_cpu.wav")
    wav_write(p, w)
    print("%-8s ids=%s frames=%d  wav=%.2fs  %s"
          % (tag, [len(x) for x in ids_list], mm.shape[1], w.size / SR, p))
print("done")
