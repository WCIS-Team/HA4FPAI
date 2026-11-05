#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tts.py —— TTS 档。契约：run(chunk) -> (samples, rate)；失败抛给链（丢音频不丢文本）。

http     接现役外部 TTS 服务（REMOTE_CHAIN.md 的姿势，本服务不自己装载模型）：
           proto=ekho   POST {url}?wait=1 body=文本           → wav（ekho-server，:2046，48k 立体声）
           proto=json   POST {url} JSON {"text","length_scale"} → wav（任意 JSON 服务）
           （piper 档及其 piper_tts_server 已于 2026-11-04 删除；代码里保留 piper 形状仅为兼容旧客户端）
         env：TTS_URL / TTS_PROTO / TTS_TIMEOUT / LENGTH_SCALE。
         采样率不一致没关系——链尾的「合成+SAR」统一重采样（她定的链尾两环）。
sine     自检档：每块出一段正弦（时长随字数走），无声卡/无模型环境的全链自检用。
"""

import json
import os
import time
import urllib.request

from core import Stage

from backends import audioio


def _lang_dir():
    """lang/ 根（server/ 的上级）。edge 实现在 lang/tts/models/edge/。"""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


def _lang_sub(*parts):
    return os.path.join(_lang_dir(), *parts)


def _pick(profile, key, env, default=None):
    v = os.environ.get(env)
    if v not in (None, ""):
        return v
    v = (profile or {}).get(key)
    return v if v not in (None, "") else default


class Http(Stage):
    name = "http"

    def __init__(self, *a, **kw):
        Stage.__init__(self, *a, **kw)
        p = dict(self.cfg.online("tts"))          # 在线 TTS 统一在 lang/config/online.yaml
        p.update(self.profile or {})
        self.url = _pick(p, "url", "TTS_URL", "")
        if not self.url:
            raise ValueError("http 档要给 TTS 服务地址：online.tts.url 或 TTS_URL")
        self.proto = (_pick(p, "proto", "TTS_PROTO", "piper") or "piper").lower()
        self.length_scale = float(_pick(p, "length_scale", "LENGTH_SCALE", 1.0) or 1.0)
        self.timeout = float(_pick(p, "timeout", "TTS_TIMEOUT", 60) or 60)

    def run(self, chunk):
        if self.proto == "ekho":
            sep = "&" if "?" in self.url else "?"
            req = urllib.request.Request(
                self.url + sep + "wait=1", data=chunk.encode("utf-8"), method="POST",
                headers={"Content-Type": "text/plain; charset=utf-8"})
        else:                                             # piper 形状（默认）
            payload = {"text": chunk, "length_scale": self.length_scale}
            req = urllib.request.Request(
                self.url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                method="POST", headers={"Content-Type": "application/json"})
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            wav = resp.read()
        samples, rate = audioio.decode_payload(wav)
        return samples, rate


class Sine(Stage):
    """自检档：不接真 TTS。每块出正弦波，时长随字数走，供全链自检（tests/smoke.py）。"""
    name = "sine"

    def __init__(self, *a, **kw):
        Stage.__init__(self, *a, **kw)
        self.rate = int((self.profile or {}).get("rate", 22050) or 22050)
        self._n = 0

    def run(self, chunk):
        self._n += 1
        dur = min(4.0, 0.3 + 0.06 * len(chunk or "x"))
        freq = 440.0 + 40.0 * (self._n % 4)               # 各块频率不同，拼接后可肉眼验证
        return audioio.sine(self.rate, dur, freq=freq), self.rate


class SherpaTts(Stage):
    """本地 sherpa-onnx OfflineTts（移植 `HA4FPAI/boards/voice_kit.py` 的 `_load_tts`）。

    两个引擎族（`tts_profiles.<档名>.kind`）：
      matcha  声学模型 **必须** 外挂独立声码器（vocos 等）。把 matcha 声学模型当 vocoder 会
              **段错误**（C++ 侧 "Unsupported model_type: matcha-tts"）——这是板上踩过的坑。
      vits    piper 及普通 vits。中文 piper **必须** 带 lexicon 构造，否则 C++ 侧
              **abort 整个进程**；所以先带 lexicon 试，失败再退不带 lexicon。

    **共用 env**：走 sherpa-onnx 自带的 onnxruntime（与 VAD/KWS/ASR 同一份）。
    配置：kind / model / vocoder / tokens / lexicon / data_dir / num_threads / speed / sid。
    """
    name = "sherpa"

    def __init__(self, *a, **kw):
        Stage.__init__(self, *a, **kw)
        import os
        sherpa_onnx = self.shared.sherpa()       # 唯一运行时（共用 env）
        p = self.profile
        self.kind = (p.get("kind", "vits") or "vits").lower()
        self.speed = float(p.get("speed", 1.0) or 1.0)
        self.sid = int(p.get("sid", 0) or 0)
        nth = int(p.get("num_threads", 3) or 3)
        model = p.get("model", "")
        if not model or not os.path.isfile(model):
            raise ValueError("sherpa-tts 模型不在：model=%r（tts_profiles.%s）"
                             % (model, self.name))
        if self.kind == "matcha":
            voc = p.get("vocoder", "")
            if not voc or not os.path.isfile(voc):
                raise ValueError("matcha 必须外挂独立 vocoder（tts_profiles.%s.vocoder），"
                                 "把声学模型当 vocoder 会 segfault；当前 vocoder=%r"
                                 % (self.name, voc))
            mc = sherpa_onnx.OfflineTtsMatchaModelConfig(
                acoustic_model=model, vocoder=voc,
                tokens=p.get("tokens", ""), lexicon=p.get("lexicon", ""))
            cfg = sherpa_onnx.OfflineTtsModelConfig(matcha=mc, num_threads=nth)
            self.tts = sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(model=cfg))
        else:
            d = p.get("data_dir", "")
            tokens = p.get("tokens", "")
            lex = p.get("lexicon") or (os.path.join(d, "lexicon.txt") if d else "")
            attempts = [{"model": model, "tokens": tokens, "data_dir": d, "lexicon": lex},
                        {"model": model, "tokens": tokens, "data_dir": d}]
            last = None
            for kwargs in attempts:                    # lexicon 优先，失败退不带
                try:
                    vc = sherpa_onnx.OfflineTtsVitsModelConfig(**kwargs)
                    cfg = sherpa_onnx.OfflineTtsModelConfig(vits=vc, num_threads=nth)
                    self.tts = sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(model=cfg))
                    break
                except Exception as exc:              # noqa: BLE001 - 两形状都试
                    last = exc
            else:
                raise RuntimeError("sherpa-tts vits 装载失败（kind=%s）：%r" % (self.kind, last))

    def run(self, chunk):
        audio = self.tts.generate(chunk, sid=self.sid, speed=self.speed)
        return list(audio.samples), int(self.tts.sample_rate)


class EdgeTts(Stage):
    """Edge「大声朗读」在线中文 TTS（实现在 `lang/tts/models/edge/edge_tts_client.py`）。

    与本地 sherpa TTS 同契约（每块文本 → (samples, rate)），但走**在线**（需联网，无需 key）：
    edge-tts 拿 mp3 → ffmpeg 转 wav → 这里 decode 成采样。缺 ffmpeg/edge-tts 会明确报错。
    配置：`online.yaml` 的 `online.edge`（voice/rate/volume/pitch/proxy/ffmpeg/sample_rate）。
    """
    name = "edge"

    def __init__(self, *a, **kw):
        Stage.__init__(self, *a, **kw)
        p = dict(self.cfg.online("edge"))         # 在线 TTS 之一，配置在 online.yaml
        p.update(self.profile or {})
        self.voice = p.get("voice", "zh-CN-XiaoxiaoNeural")
        self.rate_s = p.get("rate", "+0%")
        self.volume = p.get("volume", "+0%")
        self.pitch = p.get("pitch", "+0Hz")
        self.proxy = p.get("proxy", "")
        self.ffmpeg = p.get("ffmpeg", "")
        self.decoder = p.get("decoder", "auto")   # auto|miniaudio|soundfile|mpg123|ffmpeg
        self.sample_rate = int(p.get("sample_rate", 24000) or 24000)
        self._client = None

    def _get(self):
        if self._client is None:
            import sys
            d = _lang_sub("tts", "models", "edge")
            if d not in sys.path:
                sys.path.insert(0, d)
            import edge_tts_client as client          # 惰性
            self._client = client
        return self._client

    def run(self, chunk):
        client = self._get()
        wav = client.synthesize_media(
            chunk, voice=self.voice, rate=self.rate_s, volume=self.volume,
            pitch=self.pitch, proxy=self.proxy, ffmpeg_bin=self.ffmpeg,
            fmt="wav", sample_rate=self.sample_rate, decoder=self.decoder)
        samples, rate = audioio.decode_payload(wav)
        return samples, rate
