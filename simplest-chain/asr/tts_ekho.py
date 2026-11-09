#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""链路侧的 ekho 客户端：把 ASR 出的文本丢给本机的 ekho-server（默认 2046），拿回 wav。

服务端就是板上现成的 /root/ekho-bench/bin/ekho-server（ekho 11.0，纯 C++ 单元拼接，不吃 ONNX），
接口（从二进制里核出来的，也实测过）：
  GET  /health                  → {"status":"ok","voice":"Mandarin","sample_rate":48000,...}
  GET  /voices                  → {"default":"Mandarin","voices":[...]}
  POST /tts?wait=1  body=文本   → 完整 WAV（Content-Length，48 kHz 立体声）
  POST /tts         body=文本   → audio/L16 分块流（要边收边放才用它）

链路只认 tts.say(text, seq, stamp) -> (wav_path, ms)，和 Piper 那档同签名，所以两边可以互换。

配置（环境变量或 opts）：
  TTS_EKHO_URL      默认 http://127.0.0.1:2046
  TTS_EKHO_TIMEOUT  单句超时秒，默认 60
  TTS_EKHO_VOICE    可选，Mandarin/Cantonese/English；不填用服务端默认
  TTS_OUT_DIR       落盘目录，默认 /root/funasr/tts
"""

import json
import os
import time


def _pick(opts, key, env, default):
    for v in (opts.get(key), os.environ.get(env)):
        if v is not None and v != "":
            return v
    return default


class EkhoTts(object):
    name = "ekho"

    def __init__(self, opts=None):
        opts = opts or {}
        self.url = str(_pick(opts, "url", "TTS_EKHO_URL", "http://127.0.0.1:2046")).rstrip("/")
        self.timeout = float(_pick(opts, "timeout", "TTS_EKHO_TIMEOUT", 60))
        self.voice = str(_pick(opts, "voice", "TTS_EKHO_VOICE", "") or "")
        self.out_dir = str(_pick(opts, "out_dir", "TTS_OUT_DIR", "/root/funasr/tts"))
        self.calls = 0
        self.errors = 0
        self.ms_total = 0
        self.ms_last = None
        self.audio_s_total = 0.0
        self.sample_rate = None
        self.channels = None
        os.makedirs(self.out_dir, exist_ok=True)

    def describe(self):
        return {"url": self.url, "timeout": self.timeout, "voice": self.voice or "(default)",
                "out_dir": self.out_dir, "proto": "POST /tts?wait=1"}

    def health(self, timeout=5.0):
        from urllib.request import urlopen
        t0 = time.time()
        try:
            with urlopen(self.url + "/health", timeout=timeout) as resp:
                body = resp.read(400).decode("utf-8", "replace")
            return {"ok": True, "ms": int((time.time() - t0) * 1000), "body": body[:300]}
        except Exception as exc:
            return {"ok": False, "error": repr(exc)[:200], "url": self.url,
                    "ms": int((time.time() - t0) * 1000)}

    def say(self, text, seq, stamp=""):
        from urllib.request import Request, urlopen
        text = (text or "").strip()
        if not text:
            return None, 0
        q = "/tts?wait=1"
        if self.voice:
            q += "&voice=%s" % self.voice
        req = Request(self.url + q, data=text.encode("utf-8"),
                      headers={"Content-Type": "text/plain; charset=utf-8"}, method="POST")
        t0 = time.time()
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                data = resp.read()
                ctype = resp.headers.get("Content-Type") or ""
        except Exception:
            self.errors += 1
            raise
        ms = int((time.time() - t0) * 1000)
        if not data:
            return None, ms
        name = "tts_%s_%04d.wav" % (stamp or time.strftime("%Y%m%dT%H%M%SZ"), seq)
        path = os.path.join(self.out_dir, name)
        tmp = path + ".part"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
        self.calls += 1
        self.ms_last = ms
        self.ms_total += ms
        dur = _wav_info(path, self)
        self.audio_s_total += dur
        if self.calls == 1:
            print("ekho tts first: %s %d bytes %.2fs rtf=%.2f"
                  % (ctype, len(data), dur, (ms / 1000.0 / dur) if dur else -1), flush=True)
        return path, ms

    def stats(self):
        return {"tts_calls": self.calls, "tts_errors": self.errors,
                "tts_ms_last": self.ms_last, "tts_ms_total": self.ms_total,
                "tts_audio_s_total": round(self.audio_s_total, 2),
                "tts_rtf_mean": (round(self.ms_total / 1000.0 / self.audio_s_total, 3)
                                 if self.audio_s_total else None),
                "tts_sample_rate": self.sample_rate, "tts_channels": self.channels}


def _wav_info(path, holder):
    try:
        import wave
        w = wave.open(path, "rb")
        s = w.getnframes() / float(w.getframerate())
        holder.sample_rate = w.getframerate()
        holder.channels = w.getnchannels()
        w.close()
        return s
    except Exception:
        return 0.0
