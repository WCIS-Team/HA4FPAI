#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rms 信号原语——能量双门限分段档的数学部分。

2026-11-04 从 asr_chain.py 抽出（她裁「dbfs() 移到 vad/，方便链路运行时塞回去用」）。
本模块**只放纯函数**：不读参数、不碰分段状态、零依赖（标准库 struct/math），PC 和板端
python3.8/3.11/3.13 都能直接 import。

  dbfs(frame)        一帧 PCM16 的响度（dBFS），链路主循环 100 ms 一帧逐帧调
  decimate(frame, f) 整数倍抽取（带简单平均抗混叠），WavSource 降采样用
  seg_peak_dbfs(...) 一段音频的峰值响度，jsonl 里 peak_dbfs 字段用它算

链路侧用法（asr_chain.py 顶部现在是这样接回去的）：
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "vad"))
    import rms
    value = rms.dbfs(data)
"""

import math
import struct

__all__ = ["dbfs", "decimate", "seg_peak_dbfs"]


def dbfs(frame):
    """一帧 PCM16 小端 mono 的响度（dBFS）。空帧/全零返回 -120.0（死寂）。"""
    n = len(frame) // 2
    if n <= 0:
        return -120.0
    acc = 0
    for v in struct.unpack("<%dh" % n, frame[: n * 2]):
        acc += v * v
    rms = math.sqrt(acc / n)
    if rms <= 0.0:
        return -120.0
    return 20.0 * (math.log10(rms) - math.log10(32768.0))


def decimate(frame, factor):
    """PCM16 整数倍抽取，factor 个样本平均成 1 个（简单抗混叠）。factor==1 原样返回。"""
    if factor == 1:
        return frame
    out = bytearray()
    for i in range(0, len(frame) - 2 * factor + 1, 2 * factor):
        acc = 0
        for j in range(factor):
            acc += struct.unpack_from("<h", frame, i + 2 * j)[0]
        out += struct.pack("<h", int(round(acc / factor)))
    return bytes(out)


def seg_peak_dbfs(payload, frame_bytes):
    """一段 PCM16 的峰值 dBFS（按 frame_bytes 逐帧取 max），round 到 1 位小数。"""
    peak = -120.0
    for i in range(0, len(payload), frame_bytes):
        v = dbfs(payload[i : i + frame_bytes])
        if v > peak:
            peak = v
    return round(peak, 1)
