#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rms.py —— 能量门/电平级（链路：sar 之后、KWS 之前）。

为什么放在 KWS 前：KWS 模型装载与推理都比算 RMS 贵，先用**极便宜的帧能量**把"整段静音"
挡掉（她 2026-11-04 定的顺序 `sar → rms → kws → vad → asr`）。本级的产出：
  - `meta`（峰值 dBFS / 有声毫秒 / 帧数）进 `out["rms"]`，供路由与排查；
  - `silent=true` 时链路短路（`wake=False`），不跑 KWS/VAD/ASR。

契约：`run(samples) -> dict`（不改音频）。参数在 `vad.yaml` 的 `rms_profiles.energy`：
  frame_ms       帧长（默认 100）
  silence_dbfs   静音判定阈值（默认 -45；越小越宽松）
  min_voiced_ms  至少这么多毫秒超过阈值才算"有内容"（默认 150）
  gate           是否短路（默认 true；false＝只测不挡）
"""

import math

RATE = 16000


def dbfs(chunk):
    """float 采样的 RMS 响度（dBFS）。全零/空 → -120。"""
    n = len(chunk)
    if n <= 0:
        return -120.0
    acc = 0.0
    for v in chunk:
        acc += v * v
    rms = math.sqrt(acc / n)
    if rms <= 0.0:
        return -120.0
    return 20.0 * math.log10(rms)


class RmsEnergy(object):
    """帧能量统计 +（可选）静音短路。零依赖，纯标准库。"""
    name = "energy"

    def __init__(self, cfg, profile, shared):
        self.cfg = cfg
        self.profile = profile or {}
        self.shared = shared
        self.frame_ms = int(self.profile.get("frame_ms", 100) or 100)
        self.silence_dbfs = float(self.profile.get("silence_dbfs", -45) or -45)
        self.min_voiced_ms = int(self.profile.get("min_voiced_ms", 150) or 150)
        self.gate = str(self.profile.get("gate", "true")).strip().lower() not in \
            ("0", "false", "no", "off")

    def run(self, samples):
        rate = int(self.profile.get("rate", RATE) or RATE)
        step = max(1, int(rate * self.frame_ms / 1000))
        frames = 0
        voiced = 0
        peak = -120.0
        for i in range(0, len(samples), step):
            v = dbfs(samples[i:i + step])
            frames += 1
            if v > peak:
                peak = v
            if v >= self.silence_dbfs:
                voiced += 1
        voiced_ms = voiced * self.frame_ms
        meta = {"frame_ms": self.frame_ms, "frames": frames,
                "peak_dbfs": round(peak, 1), "voiced_ms": voiced_ms,
                "silence_dbfs": self.silence_dbfs,
                "silent": bool(self.gate and voiced_ms < self.min_voiced_ms)}
        return meta