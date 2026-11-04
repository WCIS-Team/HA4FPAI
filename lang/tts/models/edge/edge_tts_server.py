#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""edge_tts_server.py —— 常驻 Edge 中文 TTS 服务（可选，独立于 server 也能用）。

用途：给不装在 server 进程里的场景用（例如板上单独跑一台，server 的 `tts: http` 指过来），
或直接手测。**server 自己的 `chain.tts: edge` 档是直接 import 同目录 `edge_tts_client`，
不需要这个服务**；两者并存。

端点：
  GET  /health                     → {"ok":true,"voice":...,"ffmpeg":bool,"calls":n}
  GET  /voices                     → 常用中文音色列表
  POST /tts  {"text": "...", "voice": "...", "rate": "+0%", "format": "wav"|"mp3"}
             → audio/wav（默认）或 audio/mpeg（format=mp3）
  GET  /                           → 用法页

起法：
  python -X utf8 lang/tts/edge/edge_tts_server.py [--port 5003] [--voice zh-CN-XiaoxiaoNeural]
       [--proxy http://127.0.0.1:7897] [--ffmpeg /path/to/ffmpeg]
"""

import argparse
import json
import os
import shutil
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import edge_tts_client as client

STATE = {"voice": client.DEFAULT_VOICE, "calls": 0, "errors": 0,
         "ms_last": None, "started": time.time(), "ffmpeg": "", "proxy": "",
         "decoder": "miniaudio"}
LOCK = threading.Lock()

VOICES = [
    "zh-CN-XiaoxiaoNeural", "zh-CN-XiaoyiNeural",
    "zh-CN-YunxiNeural", "zh-CN-YunjianNeural", "zh-CN-YunyangNeural",
    "zh-CN-liaoning-XiaobeiNeural", "zh-CN-shaanxi-XiaoniNeural",
    "zh-HK-HiuMaanNeural", "zh-TW-HsiaoChenNeural",
]


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):
        sys.stderr.write("[edge-tts] %s %s\n" % (self.command, self.path))

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self):
        if self.path.startswith("/health"):
            self._json({"ok": True, "voice": STATE["voice"],
                        "ffmpeg": bool(STATE["ffmpeg"]), "calls": STATE["calls"],
                        "errors": STATE["errors"], "ms_last": STATE["ms_last"],
                        "decoder": client.LAST_DECODER or STATE["decoder"],
                        "uptime_s": round(time.time() - STATE["started"], 1)})
        elif self.path.startswith("/voices"):
            self._json({"voices": VOICES, "default": STATE["voice"]})
        elif self.path.rstrip("/") in ("", "/"):
            self._send(200, HELP_TEXT.encode("utf-8"), "text/plain; charset=utf-8")
        else:
            self._json({"code": 404, "msg": "no such path"}, 404)

    def do_POST(self):
        if not self.path.startswith("/tts"):
            return self._json({"code": 404, "msg": "no such path"}, 404)
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8", "replace")
        text, voice, rate, fmt = "", STATE["voice"], "+0%", "wav"
        try:
            if "json" in (self.headers.get("Content-Type") or "").lower():
                obj = json.loads(raw or "{}")
                text = (obj.get("text") or "").strip()
                voice = obj.get("voice") or voice
                rate = obj.get("rate") or rate
                fmt = (obj.get("format") or fmt).lower()
            else:
                from urllib.parse import parse_qs
                q = parse_qs(raw)
                text = (q.get("text") or [""])[0].strip()
                voice = (q.get("voice") or [voice])[0]
                rate = (q.get("rate") or [rate])[0]
                fmt = (q.get("format") or [fmt])[0].lower()
        except Exception as exc:
            return self._json({"code": 400, "msg": "bad body: %r" % (exc,)}, 400)
        if not text:
            return self._json({"code": 400, "msg": "缺 text"}, 400)
        t0 = time.time()
        try:
            data = client.synthesize_media(text, voice=voice, rate=rate,
                                           proxy=STATE["proxy"],
                                           ffmpeg_bin=STATE["ffmpeg"], fmt=fmt,
                                           decoder=STATE["decoder"])
            with LOCK:
                STATE["calls"] += 1
                STATE["ms_last"] = int((time.time() - t0) * 1000)
            ctype = "audio/mpeg" if fmt == "mp3" else "audio/wav"
            self._send(200, data, ctype)
        except Exception as exc:
            with LOCK:
                STATE["errors"] += 1
            self._json({"code": 500, "msg": repr(exc)[:300]}, 500)


HELP_TEXT = """edge-tts-server —— Edge 中文 TTS（在线）

  GET  /health            健康 + ffmpeg 是否可用
  GET  /voices            常用中文音色
  POST /tts               {"text":"...","voice":"zh-CN-XiaoxiaoNeural","rate":"+0%","format":"wav"}

手测：
  curl -s -H 'Content-Type: application/json' -d '{"text":"你好，世界"}' http://127.0.0.1:5003/tts -o out.wav
"""


def main():
    ap = argparse.ArgumentParser(description="Edge 中文 TTS 常驻服务")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5003)
    ap.add_argument("--voice", default=client.DEFAULT_VOICE)
    ap.add_argument("--proxy", default=os.environ.get("EDGE_TTS_PROXY", ""))
    ap.add_argument("--ffmpeg", default=os.environ.get("FFMPEG_BIN", ""))
    args = ap.parse_args()

    STATE["voice"] = args.voice
    STATE["ffmpeg"] = args.ffmpeg or (shutil.which("ffmpeg") or "")
    STATE["proxy"] = args.proxy
    sys.stderr.write("[edge-tts] voice=%s ffmpeg=%r proxy=%r listening %s:%d\n"
                     % (args.voice, STATE["ffmpeg"] or "(none)", args.proxy or "(none)",
                        args.host, args.port))
    srv = ThreadingHTTPServer((args.host, args.port), H)
    srv.daemon_threads = True
    srv.serve_forever()


if __name__ == "__main__":
    main()
