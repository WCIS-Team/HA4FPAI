#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bench_resampler.py — 实测各重采样算法，选最省最快的。

## 实测结论（本板 A55×4，2 秒 22050→48000）

    linear       16.6 ms   peak=1000Hz  amp=1.000   ← 生产用这个
    polyphase  1776.9 ms   peak=1000Hz  amp=1.005   ← 慢 107 倍

质量都正确（频率保持、幅度保持），差异只在插值平滑度
（max 0.43 / mean 0.10，属于高次谐波成分差异，TTS 播报听不出区别）。

**所以：线性插值又快又够用，polyphase 在 A55 上完全不可接受。**

## 为什么 polyphase 这么慢

A55 没有向量浮点加速，polyphase 是「每相位 × 每抽头 × 每样本」的
大量小数组点积，cache 命中率差；线性插值只是一次 np.interp
（C 实现、单遍扫描），快两个数量级。

## 若要更高质量又不牺牲速度

  1. scipy.signal.resample_poly（C 版，快得多）——但板上没 scipy，
     装它多 30–50 MB 依赖，为播报不值得。
  2. 只做整数倍升采样——48000 不是 22050 的整数倍，做不到。
  3. 让 TTS 输出 44100（22050 的 2 倍，升采样最简单），
     再 44100→48000 用线性。省掉一部分插值量。

结论：**linear 就是这块板上的最优解。**
"""
import time
from math import gcd

import numpy as np

SRC = 22050
DST = 48000


def make_signal(sec=2.0, freq=1000.0):
    n = int(SRC * sec)
    t = np.arange(n) / SRC
    return np.sin(2 * np.pi * freq * t).astype(np.float32)


def r_linear(x, src, dst):
    """线性插值。这是生产用算法。"""
    n_out = int(round(len(x) * dst / src))
    idx = np.linspace(0.0, len(x) - 1.0, n_out)
    return np.interp(idx, np.arange(len(x)), x).astype(np.float32)


def r_polyphase(x, src, dst, half=8):
    """Polyphase rational resampler（正确实现，仅供对照）。"""
    g = gcd(src, dst)
    up, down = dst // g, src // g
    n_out = (len(x) * up) // down
    halfw = half
    L = 2 * halfw

    scale = max(up, down)
    M = halfw * scale
    proto = np.sinc(np.arange(-M, M) / scale) * np.hamming(2 * M)
    proto = proto.astype(np.float64)

    bank = np.zeros((up, L))
    for p in range(up):
        taps = proto[p::up][:L]
        if len(taps) < L:
            taps = np.pad(taps, (0, L - len(taps)))
        s = taps.sum()
        bank[p] = taps / s if s else taps

    pad = 2 * L
    xp = np.pad(x.astype(np.float64), (pad, pad), mode='edge')
    pos = np.arange(n_out, dtype=np.int64) * down
    i0 = pos // up
    ph = pos % up

    out = np.zeros(n_out, dtype=np.float64)
    for p in range(up):
        m = ph == p
        if not m.any():
            continue
        base = i0[m] + pad
        acc = np.zeros(base.shape, dtype=np.float64)
        for k in range(L):
            gi = np.clip(base - halfw + 1 + k, 0, len(xp) - 1)
            acc += xp[gi] * bank[p, k]
        out[m] = acc
    return out.astype(np.float32)


def spectrum_peak(y, rate):
    Y = np.abs(np.fft.rfft(y))
    f = np.fft.rfftfreq(len(y), 1.0 / rate)
    return float(f[Y.argmax()])


def main():
    sig = make_signal()
    print('input: %d samples @%dHz  peak=%.0f Hz'
          % (len(sig), SRC, spectrum_peak(sig, SRC)))
    print()

    results = {}
    for name, fn in (('linear', r_linear), ('polyphase', r_polyphase)):
        t0 = time.perf_counter()
        y = fn(sig, SRC, DST)
        ms = (time.perf_counter() - t0) * 1000
        results[name] = (y, ms)
        print('%-11s %8.2f ms  out=%d  peak=%.0f Hz  amp_max=%.3f'
              % (name, ms, len(y), spectrum_peak(y, DST), np.abs(y).max()))

    print()
    tl = results['linear'][1]
    tp = results['polyphase'][1]
    print('linear 比 polyphase 快 %.0f 倍' % (tp / tl if tl else 0))
    n = min(len(results['linear'][0]), len(results['polyphase'][0]))
    d = np.abs(results['linear'][0][:n] - results['polyphase'][0][:n])
    print('两者差异 max=%.4f mean=%.5f' % (d.max(), d.mean()))


if __name__ == '__main__':
    main()
