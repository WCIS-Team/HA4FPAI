"""Implement sherpa's OfflineTtsMatchaImpl frontend faithfully and verify.

Pipeline (from sherpa-onnx/csrc/offline-tts-matcha-impl.h):
  1. ReplacePunctuations: ，->,  。->.  ...
  2. SplitByAllPunctuation
  3. MergeShortSentencesByWords(min_words=5)
  4. SplitLongSentenceByWords(max_words=20)
  5. TokenizeFromLexiconStr: longest-match lexicon lookup (CJK char by char)
  6. if add_blank: AddBlank(ids, pad_id)  -> 2n+1 tokens
  7. ApplyBosEos (use_eos_bos=0 -> flat)
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

# ---- tokens / lexicon ----
tok = {}
with open(os.path.join(D, "tokens.txt"), encoding="utf-8") as f:
    for line in f:
        p = line.strip().split()
        if len(p) == 2:
            tok[p[0]] = int(p[1])
        elif len(p) == 1:
            tok[" "] = int(p[0])       # the space token on line 1

lex = {}
with open(os.path.join(D, "lexicon.txt"), encoding="utf-8") as f:
    for line in f:
        p = line.strip().split()
        if len(p) >= 2:
            lex[p[0]] = p[1:]

PAD = tok.get("#0", 1)
print("pad_id =", PAD, " tokens=", len(tok))

REPL = {"，": ",", "、": ",", "；": ";", "：": ",", ":": ",", "。": ".",
        "？": "?", "！": "!", "…": "..."}

def replace_punct(s):
    for k, v in REPL.items():
        s = s.replace(k, v)
    return s

def split_by_punct(s):
    out, cur = [], []
    for ch in s:
        if ch in ",.!?;:":
            if cur:
                out.append("".join(cur)); cur = []
        else:
            cur.append(ch)
    if cur:
        out.append("".join(cur))
    return out

def tokenize_sentence(s):
    ids = []
    for ch in s:
        if ch in ",.!?;:":
            if ch in tok:
                ids.append(tok[ch])
            continue
        keys = lex.get(ch)
        if keys:
            for k in keys:
                if k in tok:
                    ids.append(tok[k])
    return ids

def add_blank(ids, pad):
    ans = [pad]
    for i in ids:
        ans.append(i)
        ans.append(pad)
    return ans

norm = replace_punct(TEXT)
sents = split_by_punct(norm)
print("sentences:", sents)
# merge short (<5 words)
merged, buf = [], []
for s in sents:
    buf.append(s)
    if sum(len(x) for x in buf) >= 5:
        merged.append("".join(buf)); buf = []
if buf:
    if merged:
        merged[-1] += "".join(buf)
    else:
        merged.append("".join(buf))
print("merged:", merged)

all_ids = []
for s in merged:
    all_ids += tokenize_sentence(s)
print("raw ids (%d): %s" % (len(all_ids), all_ids))
ids_blank = add_blank(all_ids, PAD)
print("add_blank ids (%d): %s" % (len(ids_blank), ids_blank))

# ---- sherpa reference ----
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
a = tts.generate(TEXT, sid=0, speed=1.0)
ref = np.asarray(a.samples, np.float32)
print("\nsherpa: %d samples = %.2f s" % (ref.size, ref.size / SR))

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

sm = ort.InferenceSession(os.path.join(D, "model-steps-3.onnx"),
                          providers=["CPUExecutionProvider"])
sv = ort.InferenceSession(os.path.join(BASE, "vocos-22khz-univ.onnx"),
                          providers=["CPUExecutionProvider"])

print("\n=== compare: raw ids vs add_blank ids ===")
for tag, ids in (("raw", all_ids), ("add_blank", ids_blank)):
    m = sm.run(None, {"x": np.array([ids], np.int64),
                      "x_length": np.array([len(ids)], np.int64),
                      "noise_scale": np.array([0.0], np.float32),
                      "length_scale": np.array([1.0], np.float32)})[0]
    mm = np.ascontiguousarray(m[0][:80, :].astype(np.float32))
    o = sv.run(None, {"mels": mm[None].astype(np.float32)})
    w = istft(o[0][0].T, o[1][0].T, o[2][0].T)
    n = min(ref.size, w.size)
    c = float(np.corrcoef(ref[:n], w[:n])[0, 1])
    print("  %-10s tokens=%2d frames=%3d dur=%.2fs  corr_vs_sherpa=%+.4f"
          % (tag, len(ids), mm.shape[1], w.size / SR, c))
