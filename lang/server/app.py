#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""app.py —— lang/server 的 HTTP 层（纯标准库，py3.8/py3.13 通跑，mimo_bridge 同款骨架）。

它管三件事（她定 2026-11-04）：
  1. 链路①：音频 → sar（前处理/重采样）→ vad → kws（留空）→ asr
  2. 链路②：llm（板上 NPU 槽位 npuserv / 云 API，可切）
  3. 链路③：分词器（长句切短压 TTS 峰值）→ tts → 合成（拼接）→ SAR（输出重采样）
各级组件可替换（lang/config/server.yaml 的 chain 节选档，backends/REGISTRY 注册），
本服务是**编排层**——HA4FPAI / npuserv / piper / ekho / 云 API 都以后端身份接入。

端点：
  POST /api/v1/chain            主口：音频进，全链跑，JSON 回（?format=wav 回裸 wav）
  POST /api/v1/asr              funasr 兼容（字段 audio，回 {"code":0,"text"}）——板上
                                remote_asr / 旧 mimo_bridge 客户端原样能指；?llm=1 才带 LLM
  POST /v1/audio/transcriptions OpenAI 兼容（字段 file）→ {"text"}
  POST /llm   {"text"}          只跑 LLM（?tts=1 连 TTS）
  POST /tts   {"text"}          只跑 分词器→TTS→合成（默认回 wav；?format=json 回 b64）
  POST /reset [?session=]       清 LLM 多轮上下文（不给 session 清全部）
  GET  /health /stats /components /
配置：lang/config/ 目录（一组件一 YAML；LANG_CONFIG_DIR / LANG_CONFIG 可指定）；环境变量优先，键名沿用既有约定
（ASR_REMOTE_* / MIMO_* / TTS_* / BIND / PORT）。密钥不进 config（D-27）。

起法：python -X utf8 server/app.py [--host H] [--port P] [--check]
默认端口 18091（18090 是 mimo_bridge、5002 piper、2046 ekho、18080 npuserv，都避开）。
"""

import argparse
import json
import re
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pathlib import Path

if __package__ in (None, ""):                     # 直接运行：python server/app.py
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from cfg import Config, as_bool, as_int
    from core import Chain
else:
    from .cfg import Config, as_bool, as_int
    from .core import Chain

_STATS = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "requests": 0, "ok": 0,
          "errors": 0, "bytes_in": 0, "last_total_ms": None}
_LOCK = threading.Lock()
_CHAIN = None                                     # 全局一份 Chain（组件惰性装载）


def _stat(key, delta=1):
    with _LOCK:
        _STATS[key] = _STATS.get(key, 0) + delta


def _config_section(stage):
    """给 /components 用：该级 active 档与可选项。"""
    from backends import REGISTRY
    return {"active": _CHAIN.active(stage) or "none",
            "available": sorted(list(REGISTRY[stage].keys()) + ["none"])}


# ---------------------------------------------------------------- multipart（mimo_bridge 同款）
def parse_multipart(body, boundary):
    """极简 multipart/form-data：{name: (filename, ctype, data)}。3.13 删了 cgi，手搓。"""
    out = {}
    sep = b"--" + boundary.encode("latin-1")
    for chunk in body.split(sep):
        if not chunk or chunk in (b"--\r\n", b"--", b"\r\n"):
            continue
        chunk = chunk.lstrip(b"\r\n")
        if chunk.startswith(b"--"):
            continue
        head, _, data = chunk.partition(b"\r\n\r\n")
        if not _:
            continue
        if data.endswith(b"\r\n"):
            data = data[:-2]
        htxt = head.decode("latin-1", "replace")
        m = re.search(r'name="([^"]*)"', htxt)
        if not m:
            continue
        fm = re.search(r'filename="([^"]*)"', htxt)
        cm = re.search(r"Content-Type:\s*([^\r\n;]+)", htxt, re.I)
        out[m.group(1)] = (fm.group(1) if fm else "",
                           cm.group(1).strip() if cm else "", data)
    return out


def pick_audio(body, ctype):
    """请求体 → 音频字节：multipart（audio/file/wav…）、JSON b64、裸 body。"""
    ctype = ctype or ""
    if "multipart/form-data" in ctype.lower():
        m = re.search(r'boundary="?([^";]+)"?', ctype, re.I)
        if not m:
            raise RuntimeError("multipart 缺 boundary")
        fields = parse_multipart(body, m.group(1))
        for key in ("audio", "file", "wav", "audio_file", "upload"):
            if key in fields and fields[key][2]:
                return fields[key][2]
        raise RuntimeError("multipart 里没有音频字段，收到的是 %s" % list(fields))
    if "application/json" in ctype.lower():
        obj = json.loads(body.decode("utf-8", "replace") or "{}")
        for key in ("audio_b64", "data", "audio"):
            s = obj.get(key)
            if isinstance(s, str) and s:
                if s.startswith("data:"):
                    s = s.split(",", 1)[-1]
                import base64
                return base64.b64decode(s)
        raise RuntimeError("JSON 里没有 audio_b64/data 字段")
    if body:
        return body                                # 裸 body 当音频（板端 raw 流姿势）
    raise RuntimeError("请求体是空的")


def parse_opts(query, defaults):
    """query 串 → 链参数。布尔：1/0；out_rate 整数；session/format 字符串；
    `tts_tier=<档>` 或直接 `tts=edge`（非布尔值即当作 tts 档名）可临时换 TTS 档。"""
    q = urllib.parse.parse_qs(query or "")
    def one(k, d=None):
        return q.get(k, [None])[0] if k in q else d
    raw_tts = one("tts")
    tts_tier = one("tts_tier")
    if raw_tts not in (None, "", "0", "1", "true", "false", "yes", "no", "on", "off"):
        tts_tier = raw_tts                       # ?tts=edge 之类：当档名
    return {
        "llm": as_bool(one("llm"), defaults.get("llm", True)),
        "tts": as_bool(raw_tts, defaults.get("tts", True)),
        "tts_tier": tts_tier,
        "out_rate": as_int(one("out_rate"), defaults.get("out_rate", 0)),
        "session": one("session") or "default",
        "format": one("format", defaults.get("format")),
    }


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "lang-server/0.1"
    protocol_version = "HTTP/1.1"

    # ---- 工具
    def _send(self, code, obj, ctype="application/json; charset=utf-8"):
        body = obj if isinstance(obj, bytes) else \
            json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return b""
        buf = bytearray()
        while len(buf) < n:
            got = self.rfile.read(min(65536, n - len(buf)))
            if not got:
                break
            buf += got
        return bytes(buf)

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    # ---- GET
    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path == "/health":
            return self._send(200, {
                "ok": True, "service": "lang-server",
                "config": _CHAIN.cfg.path, "uptime_s": int(time.time() - _T0),
                "stages": {s: _CHAIN.active(s) or "none" for s in Chain.STAGES}})
        if path == "/stats":
            with _LOCK:
                return self._send(200, dict(_STATS))
        if path == "/components":
            return self._send(200, {s: _config_section(s) for s in Chain.STAGES})
        if path == "/":
            return self._send(200, HELP_TEXT, "text/plain; charset=utf-8")
        return self._send(404, {"code": 404, "msg": "no such path: " + path})

    # ---- POST
    def do_POST(self):
        raw_path = self.path
        path = raw_path.split("?")[0].rstrip("/") or "/"
        query = raw_path.split("?", 1)[1] if "?" in raw_path else ""
        _stat("requests")
        try:
            body = self._read_body()
            _stat("bytes_in", len(body))
            ctype = self.headers.get("Content-Type") or ""

            if path == "/reset":
                q = urllib.parse.parse_qs(query)
                sid = q.get("session", [None])[0]
                _CHAIN.reset_session(sid)
                return self._send(200, {"code": 0, "cleared": sid or "all"})

            if path in ("/api/v1/chain", "/api/v1/asr", "/v1/audio/transcriptions"):
                opts = parse_opts(query, {
                    "llm": path == "/api/v1/chain",      # 兼容口默认纯 ASR（板上姿势）
                    "tts": path == "/api/v1/chain",
                    "out_rate": 0})
                audio = pick_audio(body, ctype)
                out = _CHAIN.run_audio(audio, opts=opts)
                if out.get("_wav"):
                    wav, out2 = out["_wav"], dict(out)
                    del out2["_wav"]
                    _stat("ok" if out.get("code") == 0 else "errors")
                    return self._send(200, wav, "audio/wav")
                if path == "/api/v1/chain":
                    _stat("ok" if out.get("code") == 0 else "errors")
                    return self._send(200, out)
                if path == "/v1/audio/transcriptions":   # OpenAI 形状
                    _stat("ok")
                    return self._send(200, {"text": out.get("asr_text", "")})
                asr_ms = (out.get("stages_ms") or {}).get("asr") or 0
                _stat("ok" if out.get("code") == 0 else "errors")
                return self._send(200, {"code": out.get("code", 0),
                                        "text": out.get("asr_text", ""),
                                        "asr_ms": asr_ms,
                                        "total_ms": out.get("total_ms"),
                                        **({"msg": out["msg"]} if out.get("msg") else {})})

            if path in ("/llm", "/tts"):
                opts = parse_opts(query, {"llm": path == "/llm", "tts": path == "/tts",
                                          "out_rate": 0,
                                          "format": "wav" if path == "/tts" else None})
                if "application/json" in (ctype or "").lower():
                    obj = json.loads(body.decode("utf-8", "replace") or "{}")
                    text = (obj.get("text") or "").strip()
                else:
                    text = body.decode("utf-8", "replace").strip()
                if not text:
                    return self._send(400, {"code": 1, "msg": "缺 text"})
                out = _CHAIN.run_text(text, opts=opts)
                _stat("ok" if out.get("code") == 0 else "errors")
                if out.get("_wav"):
                    return self._send(200, out["_wav"], "audio/wav")
                return self._send(200, out)

            return self._send(404, {"code": 404, "msg": "no such path: " + path})
        except Exception as exc:
            _stat("errors")
            return self._send(500, {"code": 1, "msg": ("%s: %s" % (type(exc).__name__, exc))[:300]})


_T0 = time.time()

HELP_TEXT = """lang-server —— 语音对话服务的编排层（链路① sar→vad→kws→asr ② llm ③ 分词器→tts→合成）

手测（默认端口 18091）：
  curl -s -F audio=@seg.wav "http://127.0.0.1:18091/api/v1/chain?format=wav" -o out.wav
  curl -s -F audio=@seg.wav "http://127.0.0.1:18091/api/v1/chain"
  curl -s -F audio=@seg.wav "http://127.0.0.1:18091/api/v1/asr"          # funasr 兼容
  curl -s -F file=@seg.wav "http://127.0.0.1:18091/v1/audio/transcriptions"
  curl -s -H 'Content-Type: application/json' -d '{"text":"你好"}' http://127.0.0.1:18091/llm
  curl -s -H 'Content-Type: application/json' -d '{"text":"你好"}' http://127.0.0.1:18091/tts -o out.wav
  curl -s http://127.0.0.1:18091/components

换组件：改 lang/config/server.yaml 的 chain 节（档位清单 curl /components 或看 backends/__init__.py）。
"""


def main():
    global _CHAIN
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # py3.7+；板上有就用
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="lang-server：语音对话服务编排层")
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--config", default=None, help="显式指定配置目录（lang/config）或单文件")
    ap.add_argument("--check", action="store_true", help="打印装配结果就退出（不监听）")
    ap.add_argument("--no-warm", action="store_true",
                    help="不在启动时预热 sherpa-onnx 运行时与模型")
    args = ap.parse_args()

    global _CHAIN                       # ⚠️ 不写 global 会让 Handler 永远看到 None
    cfg = Config(args.config)
    _CHAIN = Chain(cfg)
    srv_sec = cfg.section("server")
    host = args.host or srv_sec.get("bind") or "0.0.0.0"
    port = args.port or int(srv_sec.get("port", 0) or 0) or 18091

    lines = ["lang-server 装配（config=%s）" % (cfg.path or "未找到，全用内置默认")]
    for s in Chain.STAGES:
        try:
            lines.append("  %-9s = %s" % (s, _CHAIN.active(s) or "none"))
        except Exception as exc:
            lines.append("  %-9s = <错误 %s>" % (s, exc))
    print("\n".join(lines))

    # 启动即热：先把 sherpa-onnx（唯一 onnx 运行时）拉起来，再装 active 的 sherpa 档，
    # 让 VAD/KWS/ASR/TTS 共用同一份 runtime（她 2026-11-04 的省资源口径）。
    if not args.no_warm and not args.check:
        w = _CHAIN.warm()
        print("  预热: sherpa=%s  stages=%s  %dms"
              % ("ok" if w.get("sherpa") else "FAIL(%s)" % w.get("sherpa_error", "?"),
                 w.get("stages") or "{}", w.get("elapsed_ms", 0)))
    if args.check:
        return 0

    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    print("=" * 68)
    print("lang-server 监听 http://%s:%d  （/health /components /stats /）" % (host, port))
    print("=" * 68)
    sys.stdout.flush()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
