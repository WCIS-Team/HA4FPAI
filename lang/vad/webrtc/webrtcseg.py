#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WebRTC VAD 分段：纯 C 的 GMM 判决器，不碰 onnxruntime、不碰任何权重文件。

链路原来那档 FSMN-VAD 是 ONNX 模型（Fsmn-vad 524 KB 权重 + ORT 运行时）；
换成这一档之后，板上跑的东西只剩：读音频的 python + 这一个小扩展 + 一个 HTTP 客户端。
所以"ASR 走远程 + 标点不做 + VAD 换 WebRTC"＝**整条链路里没有任何 ONNX 推理**。

判决口径：WebRTC 的 Vad.is_speech() 只吃 10/20/30 ms 的单声道 16 bit 帧，
而链路主循环是 100 ms 一帧（帧节奏要和麦克风对齐），所以这里把一个 100 ms 帧切成
5×20 ms 子帧逐块判，返回"语音子帧占比"0..1，再交给主循环的双门限逻辑去做迟滞：
  占比 ≥ --webrtc-open-ratio   → 判语音，起段
  占比 ≤ --webrtc-close-ratio  → 判静音，累计 sil-stop 就断句
占比是连续量而不是布尔量，断句边界比"整帧 0/1"稳，和原来 RMS 双门限的形状也对得上。

aggressiveness（--webrtc-mode 0..3）：0 最宽松（噪声多、漏检少），3 最严（安静环境用）。
板上会议室/风扇那种底噪，实测 2 比较平衡；说话人离麦远就降到 1。

**自包含（2026-11-04 起）**：本文件、C 扩展本体、Python 包装一起住在 `vad/webrtc/`——
分段器在同目录，`pylite/` 子目录放 `_webrtcvad*.so` ＋ `webrtcvad.py` ＋ dist-info。
下面 `_ensure_pylite()` 会在 import 前把 `pylite/` 塞进 `sys.path`，所以**不再依赖外部
`PYTHONPATH`／`PYLITE` 环境变量**（老起链脚本里的 PYLITE 仍兼容，只是不再必需）。
"""

import os
import sys


def _ensure_pylite():
    """把同目录下的 pylite/ 放进 sys.path（幂等）。pylite 里是 webrtcvad 的 .so 与包装。"""
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, "pylite"), here):
        if os.path.isdir(cand) and cand not in sys.path:
            sys.path.insert(0, cand)


class WebRtcSeg(object):
    name = "webrtc"

    def __init__(self, mode=2, sub_ms=20, rate=16000):
        _ensure_pylite()
        import webrtcvad
        if int(mode) not in (0, 1, 2, 3):
            raise SystemExit("--webrtc-mode 只认 0..3，给的是 %r" % (mode,))
        if int(sub_ms) not in (10, 20, 30):
            raise SystemExit("--webrtc-sub-ms 只认 10/20/30，给的是 %r" % (sub_ms,))
        if rate not in (8000, 16000, 32000, 48000):
            raise SystemExit("WebRTC VAD 只吃 8/16/32/48 kHz，链路是 %d" % rate)
        self.vad = webrtcvad.Vad(int(mode))
        self.rate = rate
        self.sub_ms = int(sub_ms)
        self.sub_bytes = int(rate * self.sub_ms / 1000) * 2
        self.frames = 0
        self.subs = 0
        self.speech_subs = 0
        self.ratio_last = None

    def ratio(self, data):
        """一帧（链路默认 100 ms）里被判为语音的子帧占比；不足一个子帧就返回 0。"""
        n = len(data) // self.sub_bytes
        if n <= 0:
            return 0.0
        hit = 0
        for i in range(n):
            chunk = data[i * self.sub_bytes:(i + 1) * self.sub_bytes]
            try:
                if self.vad.is_speech(chunk, self.rate):
                    hit += 1
            except Exception:
                pass
        self.frames += 1
        self.subs += n
        self.speech_subs += hit
        self.ratio_last = hit / float(n)
        return self.ratio_last

    def stats(self):
        return {"webrtc_frames": self.frames, "webrtc_subframes": self.subs,
                "webrtc_speech_subframes": self.speech_subs,
                "webrtc_speech_ratio_mean": (round(self.speech_subs / float(self.subs), 3)
                                             if self.subs else None),
                "webrtc_ratio_last": (round(self.ratio_last, 3)
                                      if self.ratio_last is not None else None)}

    def close(self):
        pass
