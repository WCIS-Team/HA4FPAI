#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""远程 ASR 后端：把"声学模型"这一档从板上挪到 HTTP 后面，链路其余部分不动。

链路对后端只有一个约定：be(pcm16_bytes) -> (text, ms)。本文件把这个约定翻译成一次 HTTP
请求——分段、PcmRing、队列、心跳、jsonl、仿实时节奏全部还是本地那套（asr_chain.py 未改语义）。

板上没有本地权重，所以本档**不需要 model_dir**，也不做 model_ready 检查：有没有权重由远端决定。

三种协议（opts["proto"] / 环境变量 ASR_REMOTE_PROTO）：
  funasr  FunASR runtime 的 HTTP 服务：multipart POST {url}，字段名默认 audio，
          回 {"code":0,"text":"..."}（code 非 0 视为失败）
  openai  OpenAI 兼容 /v1/audio/transcriptions：multipart 字段 file + model，回 {"text":"..."}
  json    自定义 JSON：{"audio_b64": "...", "format": "wav", "sample_rate": 16000}，回 {"text":"..."}

配置（环境变量优先，opts 里的同名键覆盖环境变量）：
  ASR_REMOTE_URL      必填，完整 endpoint，例如 http://192.168.1.102:10095/api/v1/asr
  ASR_REMOTE_PROTO    funasr | openai | json        默认 funasr
  ASR_REMOTE_FIELD    上传字段名                     默认 audio(funasr) / file(openai)
  ASR_REMOTE_MODEL    openai 协议的 model 名         默认 funasr
  ASR_REMOTE_TOKEN    非空则带 Authorization: Bearer <token>
  ASR_REMOTE_TIMEOUT  单次请求超时（秒）             默认 30
  ASR_REMOTE_RETRY    失败重试次数（不含首次）       默认 1
  ASR_REMOTE_EXTRA    额外字段，形如 "language=zh,itn=1"（空格分隔也行）
  ASR_REMOTE_VERBOSE  1＝每次请求往 stderr 打一行状态码/字节数/耗时

失败语义：单段失败不抛到链路外层——AsrWorker 会把 asr_status 记成 error 并继续跑下一段，
jsonl 里同时留 error 字符串；asr_ms 记的是这次往返的真实耗时（含重试），因为板端要量的就是它。
"""

import base64
import io
import json
import os
import re
import sys
import time
import uuid
import wave

TAG_RE = re.compile(r"<\|[^|]*\|>")

TEXT_KEYS = ("text", "preds", "sentence", "transcription", "result", "data", "output", "asr_text")


def _envf(key, default=None):
    v = os.environ.get(key)
    return v if v not in (None, "") else default


def _pick(opts, key, env, default):
    """opts 里的值优先，其次环境变量，最后默认值。

    这里不用 `opts.get(key) or default`：retry=0、timeout=0 这类"合法的假值"会被 or 吃掉，
    10-27 自测就撞上过一次（--asr-retry 0 实际按 1 跑了两遍请求）。
    """
    for v in (opts.get(key), os.environ.get(env)):
        if v is not None and v != "":
            return v
    return default


def _strip_tags(s):
    """远端可能回 SenseVoice 那串 <|zh|><|NEUTRAL|><|Speech|><|withitn|>，剥掉只留正文。"""
    return TAG_RE.sub("", s).strip()


def _extract(obj, depth=0):
    """从各家五花八门的返回里抠出文本：str / {"text"} / {"result":[{...}]} / 列表都能吃。"""
    if obj is None or depth > 6:
        return None
    if isinstance(obj, str):
        return _strip_tags(obj)
    if isinstance(obj, dict):
        for k in TEXT_KEYS:
            if k in obj:
                got = _extract(obj[k], depth + 1)
                if got is not None:
                    return got
        return None
    if isinstance(obj, (list, tuple)):
        if len(obj) == 2 and isinstance(obj[0], str) and isinstance(obj[1], (list, tuple)):
            return _strip_tags(obj[0])          # Paraformer 的 (整句, 逐字表)
        parts = [_extract(x, depth + 1) for x in obj]
        parts = [p for p in parts if p]
        return " ".join(parts) if parts else None
    return str(obj)


def _wav_bytes(pcm16, rate=16000):
    buf = io.BytesIO()
    w = wave.open(buf, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(rate)
    w.writeframes(pcm16)
    w.close()
    return buf.getvalue()


def _multipart(fields, files):
    boundary = "----funasrremote%s" % uuid.uuid4().hex
    out = bytearray()
    for k, v in fields.items():
        out += ("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                % (boundary, k, v)).encode("utf-8")
    for k, (fname, data, ctype) in files.items():
        out += ("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\n"
                "Content-Type: %s\r\n\r\n" % (boundary, k, fname, ctype)).encode("utf-8")
        out += data
        out += b"\r\n"
    out += ("--%s--\r\n" % boundary).encode("utf-8")
    return bytes(out), "multipart/form-data; boundary=%s" % boundary


class RemoteAsr(object):
    name = "remote-api"

    def __init__(self, opts=None):
        opts = opts or {}
        self.url = str(_pick(opts, "url", "ASR_REMOTE_URL", "")).strip()
        if not self.url:
            raise SystemExit("remote-api 档要 ASR_REMOTE_URL（或 --asr-url）；"
                             "板端示例：ASR_REMOTE_URL=http://192.168.1.102:10095/api/v1/asr")
        self.proto = str(_pick(opts, "proto", "ASR_REMOTE_PROTO", "funasr")).strip().lower()
        if self.proto not in ("funasr", "openai", "json"):
            raise SystemExit("ASR_REMOTE_PROTO 只认 funasr|openai|json，给的是 %s" % self.proto)
        self.field = str(_pick(opts, "field", "ASR_REMOTE_FIELD", "")
                         or ("file" if self.proto == "openai" else "audio"))
        self.model = _pick(opts, "model", "ASR_REMOTE_MODEL", "funasr")
        self.token = _pick(opts, "token", "ASR_REMOTE_TOKEN", "")
        self.timeout = float(_pick(opts, "timeout", "ASR_REMOTE_TIMEOUT", 30))
        self.retry = int(_pick(opts, "retry", "ASR_REMOTE_RETRY", 1))
        self.verbose = str(_pick(opts, "verbose", "ASR_REMOTE_VERBOSE", "0")) in ("1", "true", "True")
        self.rate = int(_pick(opts, "rate", "ASR_REMOTE_RATE", 16000))
        self.extra = self._parse_extra(_pick(opts, "extra", "ASR_REMOTE_EXTRA", ""))
        self.calls = 0
        self.errors = 0
        self.last_ms = None
        self.last_status = None
        self.bytes_sent = 0

    @staticmethod
    def _parse_extra(s):
        """"language=zh,itn=1" 或 "language=zh itn=1" → dict；空串给空 dict。"""
        got = {}
        if not s:
            return got
        for item in re.split(r"[,\s]+", s.strip()):
            if not item:
                continue
            if "=" in item:
                k, v = item.split("=", 1)
                got[k.strip()] = v.strip()
        return got

    def describe(self):
        return {"url": self.url, "proto": self.proto, "field": self.field,
                "timeout": self.timeout, "retry": self.retry,
                "auth": bool(self.token), "extra": sorted(self.extra)}

    def stats(self):
        return {"calls": self.calls, "errors": self.errors,
                "last_ms": self.last_ms, "last_http": self.last_status,
                "bytes_sent": self.bytes_sent, "url": self.url, "proto": self.proto}

    def health(self, timeout=3.0):
        """起链前探一下远端活着没有。探不到只记读数，不拦启动——远端可能后起。"""
        base = self.url.split("/api/")[0].rstrip("/")
        target = base + "/health"
        t0 = time.time()
        try:
            req = self._request(target, method="GET")
            with _open(req, timeout) as resp:
                body = resp.read(400).decode("utf-8", "replace")
            return {"ok": True, "target": target, "http": resp.status,
                    "ms": int((time.time() - t0) * 1000), "body": body[:200]}
        except Exception as exc:
            return {"ok": False, "target": target, "error": repr(exc)[:200],
                    "ms": int((time.time() - t0) * 1000)}

    def _request(self, url, method="POST", body=None, ctype=None):
        from urllib.request import Request
        headers = {"User-Agent": "funasr-board-chain/1.0"}
        if ctype:
            headers["Content-Type"] = ctype
        if self.token:
            headers["Authorization"] = "Bearer %s" % self.token
        return Request(url, data=body, headers=headers, method=method)

    def _payload(self, pcm16):
        """按协议拼 (body, content-type)。"""
        wav = _wav_bytes(pcm16, self.rate)
        if self.proto == "json":
            obj = {"audio_b64": base64.b64encode(wav).decode("ascii"),
                   "format": "wav", "sample_rate": self.rate}
            obj.update(self.extra)
            return json.dumps(obj).encode("utf-8"), "application/json"
        fields = dict(self.extra)
        if self.proto == "openai":
            fields.setdefault("model", self.model)
            fields.setdefault("response_format", "json")
        return _multipart(fields, {self.field: ("seg.wav", wav, "audio/wav")})

    def __call__(self, pcm16_bytes):
        body, ctype = self._payload(pcm16_bytes)
        t0 = time.time()
        last_err = None
        for attempt in range(self.retry + 1):
            try:
                req = self._request(self.url, body=body, ctype=ctype)
                with _open(req, self.timeout) as resp:
                    raw = resp.read()
                    self.last_status = resp.status
                text = self._parse(raw)
                self.calls += 1
                self.bytes_sent += len(body)
                self.last_ms = int((time.time() - t0) * 1000)
                if self.verbose:
                    sys.stderr.write("[remote-api] http=%s bytes=%d ms=%d text=%r\n"
                                     % (self.last_status, len(body), self.last_ms, text[:40]))
                return text, self.last_ms
            except Exception as exc:
                last_err = exc
                if attempt < self.retry:
                    time.sleep(0.2)
        self.errors += 1
        self.last_ms = int((time.time() - t0) * 1000)
        raise RuntimeError("远程 ASR 失败(%d 次, %s): %r" % (self.retry + 1, self.url, last_err))

    def _parse(self, raw):
        txt = raw.decode("utf-8", "replace").strip()
        if not txt:
            raise RuntimeError("远端返回空 body")
        try:
            obj = json.loads(txt)
        except ValueError:
            return _strip_tags(txt)        # 有些服务直接回纯文本
        if isinstance(obj, dict):
            code = obj.get("code")
            if code is not None and str(code) not in ("0", "200", "None"):
                raise RuntimeError("远端 code=%s msg=%s" % (code, str(obj.get("msg") or obj.get("message"))[:120]))
        got = _extract(obj)
        if got is None:
            raise RuntimeError("远端返回里没找到文本字段: %s" % txt[:160])
        return got

    def close(self):
        pass


def _open(req, timeout):
    """urllib 的 with 用法包装：单独抽出来是为了 health() 和 __call__ 共用超时口径。"""
    from urllib.request import urlopen
    return urlopen(req, timeout=timeout)
