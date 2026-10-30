#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""voice_kit.py — klar voice stack (A55 board, Ubuntu 20.04, py3.8).

Single process, single shared onnxruntime (bundled inside the sherpa-onnx
wheel). Four independent components with independent streaming sessions:

    VoiceKit.new_vad()   -> VadSession    (silero v5, 16000 Hz windowed)
    VoiceKit.new_asr()   -> AsrSession    (streaming zipformer int8)
    VoiceKit.new_kws()   -> KwsSession    (zipformer zh-en chunk-8 int8)
    VoiceKit.synth(text) -> (samples, sr) (piper zh_CN-xiao_ya int8)

Models load lazily on first use and are shared for every session created
afterwards. Heavy state lives inside sherpa-onnx; this file only wraps.

Config: ~/klar-voice.json (override with env KLAR_VOICE_JSON).

Measured on this board (2026-10-30, A55 aarch64, 2 threads):
  ASR small 14M          RTF 0.40   realtime OK
  ASR zh-large 2025-06-30 RTF 0.96..1.19  NOT realtime (kept as optional profile)
  TTS piper zh xiao_ya   RTF 2.94..3.05 @3 threads
  peak RSS for ASR-large 308 MB; small-model chain ~128 MB
"""
import json
import os
import time
import wave

import numpy as np
import sherpa_onnx

DEFAULT_CFG = os.path.expanduser("~/klar-voice.json")


def load_config(path=None):
    path = path or os.environ.get("KLAR_VOICE_JSON") or DEFAULT_CFG
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def read_wav(path):
    """Return (float32 [-1..1] samples, sample_rate) from 16-bit wav."""
    with wave.open(path) as w:
        sr = w.getframerate()
        n = w.getnframes()
        data = w.readframes(n)
    return np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0, sr


def resample_48_to_16(x):
    """48k -> 16k by 3-sample moving average then decimation (float32 in/out)."""
    x = np.asarray(x, dtype=np.float32)
    if x.size < 3:
        return np.zeros(0, dtype=np.float32)
    acc = x[:-2] + x[1:-1] + x[2:]
    return (acc / 3.0)[::3].astype(np.float32)


def vmrss_mb():
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    return -1.0


#  ---- sessions -------------------------------------------------------------
class AsrSession:
    """Independent streaming recognition session."""

    def __init__(self, kit):
        self.kit = kit
        self.stream = kit._rec.create_stream()
        self.t0 = time.perf_counter()

    def accept(self, x16k):
        """Feed a chunk of float32 16k samples; returns current partial text."""
        rec, st = self.kit._rec, self.stream
        st.accept_waveform(self.kit.cfg["asr"].get("sample_rate", 16000),
                           np.asarray(x16k, dtype=np.float32))
        while rec.is_ready(st):
            rec.decode_stream(st)
        return rec.get_result(st)

    def end(self):
        rec, st = self.kit._rec, self.stream
        st.input_finished()
        while rec.is_ready(st):
            rec.decode_stream(st)
        return rec.get_result(st)

    @property
    def elapsed_ms(self):
        return int((time.perf_counter() - self.t0) * 1000)


class KwsSession:
    """Independent keyword-spotting session.

    Feeds chunks; on end() appends the mandatory tail padding so streaming
    transducer can flush trailing blanks. Hits carry the display text after
    '@' (e.g. LIGHT_UP, 女儿).
    """

    def __init__(self, kit):
        self.kit = kit
        self.stream = kit._kws.create_stream()
        self.hits = []
        self._tail = int(kit.cfg["kws"].get("tail_paddings_s", 0.66)
                         * kit.cfg["kws"].get("sample_rate", 16000))
        self.t0 = time.perf_counter()

    def accept(self, x16k):
        k, st = self.kit._kws, self.stream
        st.accept_waveform(self.kit.cfg["kws"].get("sample_rate", 16000),
                           np.asarray(x16k, dtype=np.float32))
        self._pump()
        return list(self.hits)

    def _pump(self):
        k, st = self.kit._kws, self.stream
        while k.is_ready(st):
            k.decode_stream(st)
            r = k.get_result(st)
            if r:
                self.hits.append(str(r).strip())
                k.reset_stream(st)

    def end(self):
        if self._tail > 0:
            self.stream.accept_waveform(self.kit.cfg["kws"].get("sample_rate", 16000),
                                        np.zeros(self._tail, dtype=np.float32))
        self.stream.input_finished()
        self._pump()
        return list(self.hits)


class VadSession:
    """Independent silero VAD session.

    accept() -> (speech_segments_float32, speaking_bool);
    completed speech segments (samples) are returned when closed out.
    """

    def __init__(self, kit):
        self.kit = kit
        self.vad = kit._vad
        self.win = int(kit.cfg["vad"].get("window_samples", 512))

    def accept(self, x16k):
        v = self.vad
        segs = []
        x = np.asarray(x16k, dtype=np.float32)
        i = 0
        while i + self.win <= x.size:
            v.accept_waveform(x[i:i + self.win])
            i += self.win
            while not v.empty():
                segs.append(np.asarray(v.front.samples, dtype=np.float32))
                v.pop()
        speaking = False
        try:
            speaking = bool(v.is_speech_detected())
        except AttributeError:
            pass
        return segs, speaking


class VoiceKit:
    """Lazy singleton holder for the four sherpa-onnx models."""

    def __init__(self, cfg=None):
        self.cfg = cfg or load_config()
        self._vad = None
        self._rec = None
        self._kws = None
        self._tts = None

    # -- internals -----------------------------------------------------------
    def _load_vad(self):
        c = self.cfg["vad"]
        mcfg = sherpa_onnx.VadModelConfig()
        mcfg.silero_vad.model = c["model"]
        mcfg.silero_vad.threshold = float(c.get("threshold", 0.5))
        mcfg.silero_vad.min_silence_duration = float(c.get("min_silence_duration", 0.25))
        mcfg.silero_vad.min_speech_duration = float(c.get("min_speech_duration", 0.25))
        mcfg.sample_rate = int(c.get("sample_rate", 16000))
        mcfg.num_threads = 1
        self._vad = sherpa_onnx.Vad(mcfg)

    def _asr_profile(self):
        """Resolve asr profile: cfg['asr']['profile'] selects a key under
        cfg['asr_profiles']; falls back to the flat cfg['asr'] block."""
        a = self.cfg["asr"]
        prof = a.get("profile")
        profiles = self.cfg.get("asr_profiles") or {}
        if prof and prof in profiles:
            merged = dict(a)
            merged.update(profiles[prof])
            return merged
        return a

    def _load_asr(self):
        c = self._asr_profile()
        self._rec = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=c["tokens"], encoder=c["encoder"], decoder=c["decoder"],
            joiner=c["joiner"],
            num_threads=int(c.get("num_threads", 2)),
            sample_rate=int(c.get("sample_rate", 16000)),
            feature_dim=int(c.get("feature_dim", 80)),
            decoding_method=c.get("decoding_method", "greedy_search"),
        )

    def _load_kws(self):
        c = self.cfg["kws"]
        self._kws = sherpa_onnx.KeywordSpotter(
            tokens=c["tokens"], encoder=c["encoder"], decoder=c["decoder"],
            joiner=c["joiner"], keywords_file=c["keywords_file"],
            num_threads=int(c.get("num_threads", 1)),
            sample_rate=int(c.get("sample_rate", 16000)),
            feature_dim=int(c.get("feature_dim", 80)),
            keywords_score=float(c.get("keywords_score", 1.0)),
            keywords_threshold=float(c.get("keywords_threshold", 0.25)),
        )

    def _load_tts(self):
        c = self.cfg["tts"]
        d = c["data_dir"]
        model = c["model"]
        tokens = c.get("tokens", "")
        # ORDER MATTERS: the Chinese piper model aborts the process if constructed
        # without a lexicon. lexicon-first was verified on this board.
        attempts = [
            {"model": model, "tokens": tokens, "data_dir": d,
             "lexicon": os.path.join(d, "lexicon.txt")},
            {"model": model, "tokens": tokens, "data_dir": d},
        ]
        nth = int(c.get("num_threads", 3))
        last = None
        for kwargs in attempts:
            try:
                pc = sherpa_onnx.OfflineTtsVitsModelConfig(**kwargs)
                m = sherpa_onnx.OfflineTtsModelConfig(vits=pc, num_threads=nth)
                ocfg = sherpa_onnx.OfflineTtsConfig(model=m)
                self._tts = sherpa_onnx.OfflineTts(ocfg)
                return
            except Exception as e:      # noqa: BLE001 - probe both shapes
                last = e
        raise RuntimeError("tts load failed: %r" % (last,))

    # -- public --------------------------------------------------------------
    def new_vad(self):
        if self._vad is None:
            self._load_vad()
        return VadSession(self)

    def new_asr(self):
        if self._rec is None:
            self._load_asr()
        return AsrSession(self)

    def new_kws(self):
        if self._kws is None:
            self._load_kws()
        return KwsSession(self)

    def synth(self, text, speed=None, sid=None):
        """Returns (float32 samples at tts output samplerate, sample_rate)."""
        if self._tts is None:
            self._load_tts()
        speed = float(speed if speed is not None else self.cfg["tts"].get("speed", 1.0))
        sid = int(sid if sid is not None else self.cfg["tts"].get("sid", 0))
        audio = self._tts.generate(text, sid=sid, speed=speed)
        return np.asarray(audio.samples, dtype=np.float32), int(self._tts.sample_rate)

    @property
    def loaded(self):
        return {k: bool(v) for k, v in
                (("vad", self._vad), ("asr", self._rec),
                 ("kws", self._kws), ("tts", self._tts))}
