#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""板上自测：打 mimo_bridge 的三个端点（板上没 curl，用 urllib 自己拼 multipart）。"""
import json
import sys
import time
import uuid
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:18090"
WAV = sys.argv[2] if len(sys.argv) > 2 else "/root/funasr/asr/probe1.wav"


def multipart(data, field="audio", fname="seg.wav", ctype="audio/wav"):
    b = "----probe%s" % uuid.uuid4().hex
    body = ("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\n"
            "Content-Type: %s\r\n\r\n" % (b, field, fname, ctype)).encode()
    body += data + ("\r\n--%s--\r\n" % b).encode()
    return body, "multipart/form-data; boundary=%s" % b


def call(path, body=None, ctype=None, timeout=90):
    req = urllib.request.Request(BASE + path, data=body,
                                 headers={"Content-Type": ctype} if ctype else {})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            code = r.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        code = e.code
    return code, raw, int((time.time() - t0) * 1000)


print("== GET /health")
print(call("/health"))

raw_wav = open(WAV, "rb").read()
body, ct = multipart(raw_wav)

print("\n== POST /api/v1/asr  (ASR + LLM，板端主用)")
c, r, ms = call("/api/v1/asr", body, ct)
print("http=%s wall=%dms" % (c, ms))
print(r)

print("\n== POST /api/v1/asr?llm=0  (只 ASR)")
c, r, ms = call("/api/v1/asr?llm=0", body, ct)
print("http=%s wall=%dms" % (c, ms))
print(r)

print("\n== POST /v1/audio/transcriptions  (openai 协议备选)")
body2, ct2 = multipart(raw_wav, field="file", fname="a.wav")
c, r, ms = call("/v1/audio/transcriptions", body2, ct2)
print("http=%s wall=%dms" % (c, ms))
print(r)

print("\n== POST /llm  (只跑 LLM)")
c, r, ms = call("/llm", json.dumps({"text": "今天天气怎么样"}).encode(), "application/json")
print("http=%s wall=%dms" % (c, ms))
print(r)

print("\n== POST /reset")
print(call("/reset", b"{}", "application/json"))

print("\n== GET /stats")
print(call("/stats")[1])
