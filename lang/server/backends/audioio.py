#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audioio.py —— 音频进出与采样率变换（**纯标准库**，板上 py3.8 与本机 py3.13 都能跑）。

采样表示：单声道 float 列表（-1..1）。重采样算法沿用 HA4FPAI 实测选型（docs/RESAMPLER-CHOICE.md）：
  降采样 48000→16000  decimate3-mavg3（3 抽样平均，实测与 31 抽 sinc 识别结果完全一致，快 11-14 倍）
  其余方向            linear（np.interp 口径的纯 python 版；22050→48000 实测频谱相关 0.9997）
numpy 加速路径存在但**不必须**——没有 numpy 就走纯 python，量级（≤60s/16k）完全够。
"""

import array
import io
import math
import struct
import wave


def _has_numpy():
    try:
        import numpy                                  # noqa: F401
        return True
    except Exception:
        return False


def decode_payload(raw, rate_hint=None, work_rate=16000):
    """请求体 → (float 列表 单声道, rate)。

    RIFF/WAVE 用 wave 解（PCM16；多声道取均值）；解不动就当裸 pcm16@rate_hint
    （板端 raw 流的姿势，与 server_app._payload_to_pcm16 同语义）。空返回 ([], rate)。
    """
    rate, nch, width, frames = int(rate_hint or work_rate), 1, 2, raw or b""
    try:
        with wave.open(io.BytesIO(raw)) as w:
            rate = w.getframerate()
            nch = w.getnchannels()
            width = w.getsampwidth()
            frames = w.readframes(w.getnframes())
            if w.getcomptype() != "NONE":
                raise ValueError("只认无压缩 PCM")
    except Exception:
        pass                                          # 裸流：保持上面的默认
    if width != 2:                                    # 只认真 16bit；其余当裸 pcm16 硬解
        frames = raw or b""
        nch, width = 1, 2
    a = array.array("h")
    a.frombytes(frames[:len(frames) // 2 * 2])
    if nch > 1:
        vals = [sum(a[i:i + nch]) / float(nch) / 32768.0 for i in range(0, len(a), nch)]
    else:
        vals = [v / 32768.0 for v in a]
    return vals, rate


def encode_wav(samples, rate):
    """float 单声道 → 16bit PCM wav 字节。"""
    a = array.array("h", (max(-32768, min(32767, int(v * 32768.0))) for v in samples))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(rate))
        w.writeframes(a.tobytes())
    return buf.getvalue()


def decimate3_mavg3(samples):
    """48k→16k：3 抽样平均再抽取（voice_kit.resample_48_to_16 的纯 python 同款）。"""
    x = samples
    if len(x) < 3:
        return []
    return [(x[i] + x[i + 1] + x[i + 2]) / 3.0 for i in range(0, len(x) - 2, 3)]


def resample_linear(samples, src, dst):
    """任意率线性插值（np.interp 同口径：等距目标点取值）。"""
    x = list(samples)
    n = len(x)
    if src == dst or n == 0:
        return x
    n_out = int(round(n * float(dst) / float(src)))
    if n_out <= 0:
        return []
    if n_out == 1:
        return [x[0]]
    if _has_numpy():                                  # 快路径（可选）
        import numpy as np
        idx = np.linspace(0.0, n - 1.0, n_out, dtype=np.float64)
        return np.interp(idx, np.arange(n, dtype=np.float64), np.asarray(x, dtype=np.float64)).tolist()
    step = (n - 1) / float(n_out - 1)
    out = []
    for i in range(n_out):
        pos = i * step
        i0 = int(pos)
        frac = pos - i0
        i1 = i0 + 1 if i0 + 1 < n else n - 1
        out.append(x[i0] * (1.0 - frac) + x[i1] * frac)
    return out


def to_work_rate(samples, rate, work_rate=16000):
    """统一到工作采样率：48k 走 decimate3（实测正解），其余线性。"""
    if rate == work_rate:
        return samples, rate
    if rate == 48000 and work_rate == 16000:
        return decimate3_mavg3(samples), work_rate
    return resample_linear(samples, rate, work_rate), work_rate


def concat(pieces, gap_s=0.08, out_rate=0):
    """多段 (samples, rate) → 一段：统一采样率、段间补静音、顺序拼接。

    out_rate=0 保持第一段的采样率（TTS 原生率）；给定则全部重采样到它（她定：TTS 后
    要接「合成（拼接）＋SAR（输出重采样）」）。返回 (samples, rate)。
    """
    pieces = [(s, r) for s, r in pieces if s]
    if not pieces:
        return [], 0
    rate = int(out_rate) if out_rate else int(pieces[0][1])
    norm = []
    for smp, rt in pieces:
        norm.append(resample_linear(smp, rt, rate) if rt != rate else smp)
    gap = [0.0] * int(rate * gap_s) if gap_s > 0 else []
    out = []
    for i, smp in enumerate(norm):
        out.extend(smp)
        if gap and i < len(norm) - 1:
            out.extend(gap)
    return out, rate


def sine(rate, sec, freq=440.0, amp=0.3):
    """自检用的正弦波（tests 与 tts 的 sine 自检档共用）。"""
    return [amp * math.sin(2.0 * math.pi * freq * i / float(rate))
            for i in range(int(rate * sec))]
