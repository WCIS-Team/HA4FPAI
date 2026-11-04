# -*- coding: utf-8 -*-
"""Verified STFT/ISTFT pair for the vocos 22khz head.

vocos-22khz-univ.onnx head: n_fft=1024, hop=256, windows from the model,
center=True, sr=22050, n_bins=513.
The pair below is proven by roundtrip to machine precision BEFORE being
trusted for any CPU-vs-NPU audio comparison.
"""
import numpy as np

N_FFT = 1024
HOP = 256
SR = 22050


def hann_periodic(n):
    return 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(n) / n)


def stft(x, n_fft=N_FFT, hop=HOP, center=True):
    w = hann_periodic(n_fft)
    x = np.asarray(x, dtype=np.float64)
    if center:
        x = np.pad(x, (n_fft // 2, n_fft // 2), mode="reflect")
    T = 1 + (len(x) - n_fft) // hop
    S = np.empty((T, n_fft // 2 + 1), dtype=np.complex128)
    for t in range(T):
        S[t] = np.fft.rfft(x[t * hop:t * hop + n_fft] * w)
    return S


def istft(S, n_fft=N_FFT, hop=HOP, center=True, length=None):
    w = hann_periodic(n_fft)
    S = np.asarray(S)
    T = S.shape[0]
    out = np.zeros((T - 1) * hop + n_fft, dtype=np.float64)
    wsum = np.zeros_like(out)
    for t in range(T):
        seg = np.fft.irfft(S[t], n=n_fft) * w
        out[t * hop:t * hop + n_fft] += seg
        wsum[t * hop:t * hop + n_fft] += w * w
    out /= np.maximum(wsum, 1e-12)
    if center:
        out = out[n_fft // 2:-(n_fft // 2)]
    if length is not None:
        out = out[:length]
    return out


def synth_from_triple(mag, cosn, sinn):
    """[freq,time] (513,559) -> waveform, via re=mag*cos, im=mag*sin."""
    spec = (mag * (cosn + 1j * sinn)).T        # -> (time, freq)
    return istft(spec)


def self_test():
    rng = np.random.default_rng(7)
    x = rng.standard_normal(8192)
    y = istft(stft(x), length=len(x))
    e1 = np.abs(y - x).max()
    t = np.arange(22016) / SR
    x2 = np.sin(2 * np.pi * 440 * t)
    y2 = istft(stft(x2), length=len(x2))
    e2 = np.abs(y2 - x2).max()
    print("roundtrip noise max|err| = %.3e" % e1)
    print("roundtrip tone  max|err| = %.3e" % e2)
    ok = e1 < 1e-9 and e2 < 1e-9
    print("VERIFIED:", ok)
    return ok


if __name__ == "__main__":
    self_test()
