# -*- coding: utf-8 -*-
"""Correct iSTFT for vocos output.

Bug in the earlier version: the window-sum normalisation divided by sum(win^2),
but for a plain overlap-add of windowed frames the divisor is sum(win) squared,
or equivalently you analyse with win and synthesise with win (weighted OLA).

Standard weighted-OLA reconstruction:
    frame = irfft(spec)           # the *analysis* result already had win applied
    out[s:s+N] += frame * win
    wss[s:s+N] += win * win
    out /= max(wss, eps)
That is what we had. The loudness gap therefore comes from elsewhere: check
whether the reference (sherpa) applies a different normalisation, e.g. no
division and then a global scale, or whether mag is in a different unit.

This script prints diagnostics for both hypotheses and produces two wavs at
matched RMS so the CONTENT can be compared by ear.
"""
import numpy as np, struct, os

B = r"S:\work\WCIS\workenv\.tmp_matcher"
SR, NFFT, HOP = 22050, 1024, 256
WIN = np.hanning(NFFT).astype(np.float32)

def spec_from(mag, cos, sin, nfft=NFFT):
    nb = mag.shape[1]
    s = np.zeros((mag.shape[0], nfft), dtype=np.complex64)
    s[:, :nb] = mag * cos + 1j * (mag * sin)
    s[:, nb:] = np.conj(s[:, 1:nb - 1][:, ::-1])
    return s

def istft_wola(mag, cos, sin):
    s = spec_from(mag, cos, sin)
    fr = np.fft.irfft(s, n=NFFT, axis=1).astype(np.float32)
    n = HOP * (fr.shape[0] - 1) + NFFT
    out = np.zeros(n, np.float32); ws = np.zeros(n, np.float32)
    for i in range(fr.shape[0]):
        p = i * HOP
        out[p:p + NFFT] += fr[i] * WIN
        ws[p:p + NFFT] += WIN * WIN
    return out / np.maximum(ws, 1e-8)

def istft_plain(mag, cos, sin):
    """no window at all: plain overlap-add, may show blockiness but reveals level"""
    s = spec_from(mag, cos, sin)
    fr = np.fft.irfft(s, n=NFFT, axis=1).astype(np.float32)
    n = HOP * (fr.shape[0] - 1) + NFFT
    out = np.zeros(n, np.float32); c = np.zeros(n, np.float32)
    for i in range(fr.shape[0]):
        p = i * HOP
        out[p:p + NFFT] += fr[i]
        c[p:p + NFFT] += 1
    return out / np.maximum(c, 1e-8)

def wav(path, x, sr=SR, norm=True):
    x = np.asarray(x, np.float32)
    if norm:
        p = np.abs(x).max()
        if p > 1e-9:
            x = x / p * 0.95
    pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    with open(path, "wb") as f:
        f.write(b"RIFF"); f.write(struct.pack("<I", 36 + pcm.size * 2))
        f.write(b"WAVEfmt "); f.write(struct.pack("<IHHIIHH", 16, 1, 1, sr, sr*2, 2, 16))
        f.write(b"data"); f.write(struct.pack("<I", pcm.size*2)); f.write(pcm.tobytes())
    return pcm.size / sr

# --- inputs ---
mel = np.fromfile(B + r"\board_run\real_mel.bin", np.float32).reshape(80, -1)
T = mel.shape[1]
import onnxruntime as ort
sv = ort.InferenceSession(B + r"\vocos-22khz-univ.onnx", providers=["CPUExecutionProvider"])
o = sv.run(None, {"mels": np.ascontiguousarray(mel[None].astype(np.float32))})
cmag, ccos, csin = o[0][0].T, o[1][0].T, o[2][0].T

# NPU outputs [1,513,600] -> (T,513)
n0 = np.fromfile(B + r"\wav_real\real_out_0.bin", np.float32).reshape(513, 600)[:, :T].T
n1 = np.fromfile(B + r"\wav_real\real_out_1.bin", np.float32).reshape(513, 600)[:, :T].T
n2 = np.fromfile(B + r"\wav_real\real_out_2.bin", np.float32).reshape(513, 600)[:, :T].T

print("CPU  mag rms=%.4f   NPU mag rms=%.4f" % (float(np.sqrt((cmag**2).mean())), float(np.sqrt((n0**2).mean()))))

w_cpu = istft_wola(cmag, ccos, csin)
w_npu = istft_wola(n0, n1, n2)
m = min(w_cpu.size, w_npu.size)
r_cpu = float(np.sqrt((w_cpu ** 2).mean()))
r_npu = float(np.sqrt((w_npu ** 2).mean()))
cc = float(np.corrcoef(w_cpu[:m], w_npu[:m])[0, 1])
print('WOLA: cpu rms=%.4f  npu rms=%.4f  corr=%.4f' % (r_cpu, r_npu, cc))

p1 = B + r"\wav_real\A_cpu_real.wav"
p2 = B + r"\wav_real\B_npu_real.wav"
print("wrote %s %.2fs" % (p1, wav(p1, w_cpu)))
print("wrote %s %.2fs" % (p2, wav(p2, w_npu)))
