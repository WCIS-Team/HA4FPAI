#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""假远端 ASR：只为了证明"口子"通，不做识别。

链路要验证的是三件事：段音频能不能按协议送出去、往返耗时进不进 asr_ms、失败会不会把链带崩。
这三件都不需要真模型，所以这里起一个假服务：收 multipart，量出音频时长，回一句带时长的话。

回文本刻意做成"整句无标点"，好让本地 CT_Transformer 有活干——标点档是否真的串在 ASR 之后，
看 jsonl 里 text 与 text_raw 的差别就能一眼确认。

用法（板端，用系统 python 就行，不用 venv）：
  python3 /root/funasr/asr/mock_asr_server.py --port 18095 --latency 0.15
  curl -s -F "audio=@/root/funasr/testdata/a067.wav" http://127.0.0.1:18095/api/v1/asr
端点：POST /api/v1/asr（funasr 协议）、POST /v1/audio/transcriptions（openai 协议）、GET /health
"""

import argparse
import io
import json
import re
import sys
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def wav_duration(data):
    try:
        w = wave.open(io.BytesIO(data), "rb")
        n = w.getnframes() / float(w.getframerate())
        w.close()
        return n
    except Exception:
        return len(data) / 32000.0


def pick_audio(body, ctype):
    """从 multipart 里抠出第一个文件块；抠不到就整包当音频（JSON 协议走这条）。"""
    m = re.search(r"boundary=([^;]+)", ctype or "")
    if not m:
        try:
            obj = json.loads(body.decode("utf-8", "replace"))
            import base64
            return base64.b64decode(obj.get("audio_b64", ""))
        except Exception:
            return body
    boundary = m.group(1).strip().strip('"').encode()
    for part in body.split(b"--" + boundary):
        if b"filename=" in part:
            idx = part.find(b"\r\n\r\n")
            if idx > 0:
                return part[idx + 4:].rstrip(b"\r\n-")
    return b""


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    latency = 0.15
    n = 0

    def log_message(self, fmt, *a):
        sys.stderr.write("[mock] %s %s\n" % (self.command, self.path))

    def _json(self, obj, code=200):
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.startswith("/health"):
            self._json({"code": 0, "status": "ok", "mock": True, "served": H.n})
        else:
            self._json({"code": 404, "msg": "no such path"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        audio = pick_audio(body, self.headers.get("Content-Type"))
        dur = wav_duration(audio)
        time.sleep(H.latency)
        H.n += 1
        text = ("这是模拟远端识别第%d段时长%.1f秒" % (H.n, dur))
        if self.path.startswith("/v1/audio/transcriptions"):
            self._json({"text": text})
        else:
            self._json({"code": 0, "text": text, "mock": True,
                        "bytes": len(audio), "dur_s": round(dur, 2)})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=18095)
    ap.add_argument("--latency", type=float, default=0.15)
    args = ap.parse_args()
    H.latency = args.latency
    srv = ThreadingHTTPServer((args.host, args.port), H)
    sys.stderr.write("[mock] listening %s:%d latency=%.2fs\n" % (args.host, args.port, args.latency))
    srv.serve_forever()


if __name__ == "__main__":
    main()
