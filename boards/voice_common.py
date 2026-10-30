#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""voice_common.py — shared bits for the three Wyoming voice services.

Centralises:
  * the process-wide VoiceKit singleton (=> ONE onnxruntime, shared by VAD/ASR/
    KWS/TTS, which is the "shared env" requirement),
  * Wyoming `info` descriptions with the fields this wyoming 1.10.2 build
    actually requires (`attribution` is mandatory on every Artifact),
  * the 48k -> 16k decimation used for every inbound audio chunk.

Nothing here loads a model at import time; models stay lazy in VoiceKit.
"""
import os
import sys

import numpy as np

# make `voice_kit` importable no matter where the service file lives
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import voice_kit  # noqa: E402

from wyoming.info import (  # noqa: E402
    AsrModel, AsrProgram, Attribution, Info,
    TtsProgram, TtsVoice,
    WakeModel, WakeProgram,
)

SHERPA_ATTR = Attribution(
    name="k2-fsa/sherpa-onnx",
    url="https://github.com/k2-fsa/sherpa-onnx",
)

_KIT = None


def kit(cfg=None):
    """Return the single process-wide VoiceKit (created on first call)."""
    global _KIT
    if _KIT is None:
        _KIT = voice_kit.VoiceKit(cfg or voice_kit.load_config())
    return _KIT


def to_16k(audio_bytes, rate):
    """int16le bytes @rate -> float32 mono @16k."""
    x = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    if rate == 16000:
        return x
    if rate == 48000:
        # decimate3-mavg3: 3-tap moving average then take every 3rd sample
        if x.size < 3:
            return np.zeros(0, dtype=np.float32)
        acc = x[:-2] + x[1:-1] + x[2:]
        return (acc / 3.0)[::3].astype(np.float32)
    # generic fallback: naive linear resample (should not be hit on this board)
    n = int(len(x) * 16000 / rate)
    if n <= 0:
        return np.zeros(0, dtype=np.float32)
    idx = np.linspace(0, len(x) - 1, n)
    return np.interp(idx, np.arange(len(x)), x).astype(np.float32)


def asr_info(cfg):
    models = []
    for key, p in (cfg.get("asr_profiles") or {}).items():
        models.append(AsrModel(
            name=key,
            attribution=SHERPA_ATTR,
            installed=True,
            description=p.get("label", key),
            version=None,
            languages=["zh", "en"],
        ))
    if not models:
        models = [AsrModel(
            name="sherpa-onnx-streaming-zipformer",
            attribution=SHERPA_ATTR,
            installed=True,
            description="streaming zipformer transducer",
            version=None,
            languages=["zh", "en"],
        )]
    return Info(asr=[AsrProgram(
        name="sherpa-onnx",
        attribution=SHERPA_ATTR,
        installed=True,
        description="sherpa-onnx streaming zipformer over a shared onnxruntime",
        version="2.0.1",
        models=models,
        supports_transcript_streaming=True,
        # silero VAD lives inside this same process (see cfg['vad'])
        requires_external_vad=False,
    )], tts=[], handle=[], intent=[], wake=[], mic=[], snd=[])


def tts_info(cfg):
    t = cfg["tts"]
    voice_name = os.path.basename(t["model"]).replace(".onnx", "")
    return Info(tts=[TtsProgram(
        name="sherpa-onnx-piper",
        attribution=SHERPA_ATTR,
        installed=True,
        description="piper zh_CN vits via sherpa-onnx (shared onnxruntime)",
        version="2.0.1",
        voices=[TtsVoice(
            name=voice_name,
            attribution=SHERPA_ATTR,
            installed=True,
            description="piper medium, %d Hz" % int(t.get("output_sample_rate", 22050)),
            version=None,
            languages=["zh-CN"],
        )],
        supports_synthesize_streaming=False,
    )], asr=[], handle=[], intent=[], wake=[], mic=[], snd=[])


def wake_info(cfg):
    k = cfg["kws"]
    phrases = []
    kf = k.get("keywords_file")
    if kf and os.path.exists(kf):
        try:
            with open(kf, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if "@" in line:
                        phrases.append(line.split("@")[-1].strip())
        except OSError:
            pass
    models = [WakeModel(
        name="sherpa-onnx-kws",
        attribution=SHERPA_ATTR,
        installed=True,
        description="zipformer zh-en chunk-8 int8 keyword spotter",
        version="2.0.1",
        languages=["zh", "en"],
        phrase=", ".join(phrases) if phrases else None,
    )]
    return Info(wake=[WakeProgram(
        name="sherpa-onnx-kws",
        attribution=SHERPA_ATTR,
        installed=True,
        description="sherpa-onnx keyword spotter (shared onnxruntime)",
        version="2.0.1",
        models=models,
    )], asr=[], tts=[], handle=[], intent=[], mic=[], snd=[])
