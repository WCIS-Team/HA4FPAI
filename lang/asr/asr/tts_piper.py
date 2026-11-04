#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""链路侧的 Piper 客户端：把"文本"变成"落盘的 wav"，接口和远程 ASR 那层同风格。

链路只认一句：tts.say(text, seq) -> (wav_path, ms)。实现是往常驻的 piper 服务 POST 一次
（服务见 asr/piper_tts_server.py，跑在装了 piper-tts 的 conda env 里）。

为什么不做进程内合成：链路跑在 /root/funasr/venv（py3.8 + onnxruntime 给 FSMN-VAD 用），
piper-tts 1.8.0 要 py3.9+，而且它是 63 MB 的 ONNX 会话，装载一次 23~27 s——只能常驻、不能每句重装。
所以这里走 HTTP，和 ASR 的口子一个形状：板上链路本身不 import onnxruntime 之外的任何东西。

配置（环境变量或 opts）：
  TTS_PIPER_URL       服务地址，默认 http://127.0.0.1:5002
  TTS_PIPER_TIMEOUT   单句超时秒，默认 120（RTF 1.8 时一句 20 s 的文本要 36 s）
  TTS_LENGTH_SCALE    语速系数，<1 更快；默认 1.0
"""

import json
import os
import time


def _pick(opts, key, env, default):
    for v in (opts.get(key), os.environ.get(env)):
        if v is not None and v != "":
            return v
    return default


class PiperTts(object):
    name = "piper"

    def __init__(self, opts=None):
        opts = opts or {}
        self.url = str(_pick(opts, "url", "TTS_PIPER_URL", "http://127.0.0.1:5002")).rstrip("/")
        self.timeout = float(_pick(opts, "timeout", "TTS_PIPER_TIMEOUT", 120))
        self.length_scale = float(_pick(opts, "length_scale", "TTS_LENGTH_SCALE", 1.0))
        self.out_dir = str(_pick(opts, "out_dir", "TTS_OUT_DIR", "/root/funasr/tts"))
        self.calls = 0
        self.errors = 0
        self.ms_total = 0
        self.ms_last = None
        self.audio_s_total = 0.0
        os.makedirs(self.out_dir, exist_ok=True)

    def describe(self):
        return {"url": self.url, "timeout": self.timeout,
                "length_scale": self.length_scale, "out_dir": self.out_dir}

    def health(self, timeout=5.0):
        from urllib.request import Request, urlopen
        t0 = time.time()
        try:
            req = Request(self.url + "/health", method="GET")
            with urlopen(req, timeout=timeout) as resp:
                body = resp.read(600).decode("utf-8", "replace")
            return {"ok": True, "ms": int((time.time() - t0) * 1000), "body": body[:300]}
        except Exception as exc:
            return {"ok": False, "error": repr(exc)[:200],
                    "ms": int((time.time() - t0) * 1000), "url": self.url}

    def say(self, text, seq, stamp=""):
        """合成一句并落盘；返回 (path, ms)。失败抛异常，由 AsrWorker 记 tts_status=error。"""
        from urllib.request import Request, urlopen
        text = (text or "").strip()
        if not text:
            return None, 0
        payload = json.dumps({"text": text, "length_scale": self.length_scale}).encode("utf-8")
        req = Request(self.url + "/tts", data=payload,
                      headers={"Content-Type": "application/json"}, method="POST")
        t0 = time.time()
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                if resp.status == 204:
                    return None, int((time.time() - t0) * 1000)
                data = resp.read()
        except Exception:
            self.errors += 1
            raise
        ms = int((time.time() - t0) * 1000)
        name = "tts_%s_%04d.wav" % (stamp or time.strftime("%Y%m%dT%H%M%SZ"), seq)
        path = os.path.join(self.out_dir, name)
        tmp = path + ".part"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
        self.calls += 1
        self.ms_last = ms
        self.ms_total += ms
        self.audio_s_total += _wav_seconds(path)
        return path, ms

    def stats(self):
        return {"tts_calls": self.calls, "tts_errors": self.errors,
                "tts_ms_last": self.ms_last, "tts_ms_total": self.ms_total,
                "tts_audio_s_total": round(self.audio_s_total, 2),
                "tts_rtf_mean": (round(self.ms_total / 1000.0 / self.audio_s_total, 3)
                                 if self.audio_s_total else None)}


def _wav_seconds(path):
    try:
        import wave
        w = wave.open(path, "rb")
        s = w.getnframes() / float(w.getframerate())
        w.close()
        return s
    except Exception:
        return 0.0
