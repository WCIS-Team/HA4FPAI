#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""常驻 Piper TTS 服务：模型只装载一次，之后每句一次 HTTP。

为什么不用 piper 自带的 CLI：CLI 是一次性进程，每说一句都要重新装载 63 MB 的 onnx（板上实测
23~27 s），链路里没法用。piper-tts 1.8.0 还带了 flask 版 http_server，但这个 conda env 里没装
flask，所以这里用 stdlib 起一个最小服务，接口只有两条。

跑在哪个 python：**必须**是装了 piper-tts 的那个 conda env
（/root/mf3/envs/tts/bin/python，py3.11 + onnxruntime 1.31.0）。链路的 python 不需要装 piper。

端点：
  GET  /health                     → {"ok":true,"model":...,"load_s":...,"calls":n,"ms_last":..}
  POST /tts                        → audio/wav
       body JSON {"text": "...", "length_scale": 1.0}   或 form: text=...&length_scale=...
       text 为空直接回 204（不合成）

板上实测（zh_CN-huayan-medium，4 核）：装载 23~27 s、VmHWM 165 MB、RTF 1.25~1.79。
"""

import argparse
import io
import json
import sys
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {"model": "", "load_s": None, "calls": 0, "ms_last": None, "ms_total": 0,
         "errors": 0, "started": time.time()}
VOICE = None
LOCK = threading.Lock()


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):
        sys.stderr.write("[piper-tts] %s %s\n" % (self.command, self.path))

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self):
        if self.path.startswith("/health"):
            self._json({"ok": VOICE is not None, "model": STATE["model"],
                        "load_s": STATE["load_s"], "calls": STATE["calls"],
                        "ms_last": STATE["ms_last"], "errors": STATE["errors"],
                        "uptime_s": round(time.time() - STATE["started"], 1)})
        else:
            self._json({"code": 404, "msg": "no such path"}, 404)

    def do_POST(self):
        if not self.path.startswith("/tts"):
            self._json({"code": 404, "msg": "no such path"}, 404)
            return
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8", "replace")
        ctype = (self.headers.get("Content-Type") or "").lower()
        text, length_scale = "", None
        try:
            if "json" in ctype:
                obj = json.loads(raw or "{}")
                text = (obj.get("text") or "").strip()
                length_scale = obj.get("length_scale")
            else:
                from urllib.parse import parse_qs
                q = parse_qs(raw)
                text = (q.get("text") or [""])[0].strip()
                length_scale = (q.get("length_scale") or [None])[0]
        except Exception as exc:
            self._json({"code": 400, "msg": "bad body: %r" % (exc,)}, 400)
            return
        if not text:
            self._send(204, b"", "audio/wav")
            return
        t0 = time.time()
        try:
            buf = io.BytesIO()
            cfg = _synth_cfg(length_scale)
            with LOCK:                      # ORT session 不保证线程安全，串行合成
                wf = wave.open(buf, "wb")
                VOICE.synthesize_wav(text, wf, syn_config=cfg)
                wf.close()
            ms = int((time.time() - t0) * 1000)
            with LOCK:
                STATE["calls"] += 1
                STATE["ms_last"] = ms
                STATE["ms_total"] += ms
            self._send(200, buf.getvalue(), "audio/wav")
        except Exception as exc:
            with LOCK:
                STATE["errors"] += 1
            self._json({"code": 500, "msg": repr(exc)[:300]}, 500)


def _synth_cfg(length_scale):
    from piper import SynthesisConfig
    if length_scale in (None, "", 0):
        return SynthesisConfig()
    return SynthesisConfig(length_scale=float(length_scale))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5002)
    ap.add_argument("--model", default="/root/ttsbench/models/piper/zh_CN-huayan-medium.onnx")
    ap.add_argument("--length-scale", type=float, default=1.0,
                    help="<1 说得更快；板上 RTF 1.79，想追上实时可以试 0.8")
    args = ap.parse_args()

    global VOICE
    t0 = time.time()
    from piper import PiperVoice
    VOICE = PiperVoice.load(args.model)
    load_s = round(time.time() - t0, 2)
    STATE["model"] = args.model
    STATE["load_s"] = load_s
    sys.stderr.write("[piper-tts] model=%s load_s=%.1f default_length_scale=%s\n"
                     % (args.model, load_s, args.length_scale))
    srv = ThreadingHTTPServer((args.host, args.port), H)
    sys.stderr.write("[piper-tts] listening %s:%d\n" % (args.host, args.port))
    srv.serve_forever()


if __name__ == "__main__":
    main()
