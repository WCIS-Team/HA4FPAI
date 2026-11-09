#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""simplest_chain_bridge.py —— 本地 ASR+LLM 桥（硅基流动 SiliconFlow）。

一条请求里做两件事：
  收到音频 → SiliconFlow ASR (XingChenAGI/XingChenASR-V3.2-Ultra，OpenAI 兼容 /audio/transcriptions)
            → 识别文本 → SiliconFlow LLM (Qwen/Qwen2.5-7B-Instruct，OpenAI 兼容 /chat/completions)
            → 回复文本 → 返回

板端链路只认一个约定：POST 过来一段 wav，回一个带 text 的 JSON。所以本服务把 funasr 协议
（multipart，字段 audio，回 {"code":0,"text":"..."}）做成主端点，板子那边一行代码都不用改：

  ASR_REMOTE_URL=http://192.168.1.107:18090/api/v1/asr /funasr-lite

端点：
  POST /api/v1/asr              funasr 协议主端点：音频 → ASR → LLM → 回 LLM 文本
  POST /api/v1/asr?llm=0        只做 ASR，回识别文本（调参/排错用）
  POST /v1/audio/transcriptions openai 协议备选端点（multipart 字段 file）
  POST /asr                     同 ?llm=0
  POST /llm                     {"text": "..."} → 只跑 LLM
  POST /reset                   清空多轮上下文
  GET  /health                  板端起链前会探这里
  GET  /stats                   累计计数
  GET  /                        用法页

配置（环境变量）：
  SILICONFLOW_API_KEY   **必填**，只从环境变量读；缺失直接退出（不再有硬编码兜底值）
  SILICONFLOW_BASE_URL  默认 https://api.siliconflow.cn/v1
  ASR_MODEL             默认 XingChenAGI/XingChenASR-V3.2-Ultra
  LLM_MODEL             默认 Qwen/Qwen2.5-7B-Instruct
  ASR_LANG              语言，默认 zh
  BIND / PORT           默认 0.0.0.0 / 18090
  LLM_ENABLE            1＝接 LLM（默认），0＝只做 ASR
  HISTORY_TURNS         多轮上下文保留轮数，默认 2；0＝单轮无记忆
  LLM_REASONING         默认 none（不传 reasoning_effort）；给 low/medium/high 或空串
  LLM_MAX_TOKENS        默认 128（回复要求 ≤80 字，够用）
  SYSTEM_PROMPT         覆盖默认系统提示词
  ASR_TIMEOUT / LLM_TIMEOUT  默认 60 / 60 秒
  LOG_JSONL             逐段日志路径，默认 ./logs/bridge.jsonl

只用标准库（urllib / http.server / 手写 multipart 解析），Windows 和板上 python3.8 都能直接跑，
不依赖 requests，也不依赖 3.13 已删除的 cgi 模块。
"""

import base64
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---------------------------------------------------------------- 配置

API_KEY = (os.environ.get("SILICONFLOW_API_KEY") or "").strip()
BASE_URL = (os.environ.get("SILICONFLOW_BASE_URL") or "https://api.siliconflow.cn/v1").rstrip("/")
ASR_MODEL = os.environ.get("ASR_MODEL") or "XingChenAGI/XingChenASR-V3.2-Ultra"
LLM_MODEL = os.environ.get("LLM_MODEL") or "Qwen/Qwen2.5-7B-Instruct"
ASR_LANG = os.environ.get("ASR_LANG") or "zh"
BIND = os.environ.get("BIND") or "0.0.0.0"
PORT = int(os.environ.get("PORT") or 18090)
LLM_ENABLE = (os.environ.get("LLM_ENABLE") or "1") not in ("0", "false", "False", "no")
HISTORY_TURNS = int(os.environ.get("HISTORY_TURNS") or 2)
ASR_TIMEOUT = float(os.environ.get("ASR_TIMEOUT") or 60)
LLM_TIMEOUT = float(os.environ.get("LLM_TIMEOUT") or 60)
LLM_REASONING = os.environ.get("LLM_REASONING")
if LLM_REASONING is None:
    LLM_REASONING = "none"
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS") or 128)
LOG_JSONL = os.environ.get("LOG_JSONL") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "logs", "bridge.jsonl")

DEFAULT_SYSTEM = (
    "你是一个中文语音助手，你的回答会被直接转成语音念给用户听。"
    "要求：口语化，一般 1 到 2 句话，不超过 80 个字；"
    "不要用 Markdown、列表、表情符号、括号注释、序号；"
    "不要复述用户的问题；不要解释你在做什么，直接给答案；"
    "拿不准就直说不知道。"
)
SYSTEM_PROMPT = os.environ.get("SYSTEM_PROMPT") or DEFAULT_SYSTEM

# ---------------------------------------------------------------- 计数

_LOCK = threading.Lock()
_STATS = {
    "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
    "requests": 0, "ok": 0, "asr_fail": 0, "llm_fail": 0,
    "empty_asr": 0, "asr_ms_sum": 0, "llm_ms_sum": 0, "bytes_in": 0,
}
_HISTORY = []                          # [{role, content}, ...]，多轮上下文


def _stat(key, delta=1):
    with _LOCK:
        _STATS[key] = _STATS.get(key, 0) + delta


def _log(rec):
    try:
        d = os.path.dirname(LOG_JSONL)
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        with open(LOG_JSONL, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------- SiliconFlow 调用

def _post_json(path, payload, timeout):
    """POST 到 SiliconFlow，返回 (obj, ms)。失败抛 RuntimeError（带服务端错误正文）。"""
    url = BASE_URL + path
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + API_KEY,
                 "Accept": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:400]
        except Exception:
            pass
        raise RuntimeError("HTTP %s %s" % (exc.code, detail or exc.reason))
    except Exception as exc:
        raise RuntimeError("%r" % (exc,))
    ms = int((time.time() - t0) * 1000)
    try:
        obj = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        raise RuntimeError("返回不是 JSON：%s" % raw[:200])
    return obj, ms


def _content_of(obj):
    """从 chat.completion 里取 choices[0].message.content；顺带兜底别的形状。"""
    try:
        msg = obj["choices"][0]["message"]
        txt = msg.get("content")
        if isinstance(txt, list):
            txt = "".join(p.get("text", "") for p in txt if isinstance(p, dict))
        return (txt or "").strip()
    except Exception:
        return ""


def siliconflow_asr(wav_bytes, mime="audio/wav"):
    """音频 → 文本。使用 SiliconFlow 的 /audio/transcriptions (OpenAI 兼容)。
    直接 multipart 上传，不经过 Base64，省流量省时间。
    返回 (text, ms)。"""
    # 参考 curl 的 multipart 格式（实测 SiliconFlow 对 audio/wav 的 part 头会回 400）：
    # - boundary: 28+ dashes + 随机串
    # - file part: Content-Type: application/octet-stream
    # - model part: 单独的 form-data 字段
    boundary = "------------------------" + uuid.uuid4().hex[:16]
    body = (
        ("--%s\r\n" % boundary).encode()
        + b'Content-Disposition: form-data; name="file"; filename="seg.wav"\r\n'
        + b"Content-Type: application/octet-stream\r\n\r\n"
        + wav_bytes
        + ("\r\n--%s\r\n" % boundary).encode()
        + b'Content-Disposition: form-data; name="model"\r\n\r\n'
        + ASR_MODEL.encode()
        + ("\r\n--%s--\r\n" % boundary).encode()
    )
    url = BASE_URL + "/audio/transcriptions"
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary,
                 "Authorization": "Bearer " + API_KEY,
                 "Accept": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=ASR_TIMEOUT) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:400]
        except Exception:
            pass
        raise RuntimeError("HTTP %s %s" % (exc.code, detail or exc.reason))
    except Exception as exc:
        raise RuntimeError("%r" % (exc,))
    ms = int((time.time() - t0) * 1000)
    try:
        obj = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        raise RuntimeError("ASR 返回不是 JSON：%s" % raw[:200])
    # SiliconFlow /audio/transcriptions 返回 OpenAI 格式: {"text": "..."}
    return (obj.get("text") or "").strip(), ms


def siliconflow_llm(user_text):
    """识别文本 → 回复文本。返回 (reply, ms, ok, err)。LLM 挂了不抛，降级回识别文本。"""
    with _LOCK:
        msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
        if HISTORY_TURNS > 0:
            msgs += _HISTORY[-(HISTORY_TURNS * 2):]
        msgs.append({"role": "user", "content": user_text})
    payload = {
        "model": LLM_MODEL,
        "messages": msgs,
        "max_completion_tokens": LLM_MAX_TOKENS,
        "temperature": 0.6,
        "top_p": 0.95,
        "stream": False,
        "frequency_penalty": 0,
        "presence_penalty": 0,
    }
    if LLM_REASONING:
        payload["reasoning_effort"] = LLM_REASONING
    try:
        obj, ms = _post_json("/chat/completions", payload, LLM_TIMEOUT)
    except Exception as exc:
        _stat("llm_fail")
        return user_text, 0, False, str(exc)[:300]
    reply = _content_of(obj)
    if not reply:
        _stat("llm_fail")
        return user_text, ms, False, "LLM 返回空内容"
    if HISTORY_TURNS > 0:
        with _LOCK:
            _HISTORY.append({"role": "user", "content": user_text})
            _HISTORY.append({"role": "assistant", "content": reply})
            keep = HISTORY_TURNS * 2
            if len(_HISTORY) > keep:
                del _HISTORY[:len(_HISTORY) - keep]
    return reply, ms, True, ""


# ---------------------------------------------------------------- multipart

def _sniff_mime(data):
    """按文件头判格式：板端只发 wav，但手测时可能塞 mp3。"""
    if len(data) > 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    if data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
        return "audio/mpeg"
    return "audio/wav"


def parse_multipart(body, boundary):
    """极简 multipart/form-data 解析：返回 {name: (filename, ctype, data)}。

    Python 3.13 删了 cgi 模块，板上 3.8 又没有统一实现，所以自己切。
    按 "--boundary" 切块，块头找 \r\n\r\n，前面是头、后面是数据（去掉尾随 \r\n）。
    """
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
        name = m.group(1)
        fm = re.search(r'filename="([^"]*)"', htxt)
        cm = re.search(r"Content-Type:\s*([^\r\n;]+)", htxt, re.I)
        out[name] = (fm.group(1) if fm else "", (cm.group(1).strip() if cm else ""), data)
    return out


def pick_audio(body, ctype):
    """从请求里把音频抠出来：multipart（audio/file/wav 字段）、raw body、JSON base64。"""
    if ctype and "multipart/form-data" in ctype.lower():
        m = re.search(r'boundary="?([^";]+)"?', ctype, re.I)
        if not m:
            raise RuntimeError("multipart 缺 boundary")
        fields = parse_multipart(body, m.group(1))
        for key in ("audio", "file", "wav", "audio_file", "upload"):
            if key in fields and fields[key][2]:
                fn, ct, data = fields[key]
                return data, (ct or _sniff_mime(data))
        raise RuntimeError("multipart 里没有音频字段，收到的是 %s" % list(fields))
    if ctype and "application/json" in ctype.lower():
        obj = json.loads(body.decode("utf-8", "replace") or "{}")
        for key in ("audio_b64", "data", "audio"):
            if isinstance(obj.get(key), str) and obj[key]:
                s = obj[key]
                if s.startswith("data:"):
                    s = s.split(",", 1)[-1]
                return base64.b64decode(s), _sniff_mime(base64.b64decode(s))
        raise RuntimeError("JSON 里没有 audio_b64/data 字段")
    if body:
        return body, _sniff_mime(body)          # 裸 body 当音频
    raise RuntimeError("请求体是空的")


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "simplest-chain-bridge/1.0"
    protocol_version = "HTTP/1.1"

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
            return self._send(200, {"ok": True, "service": "simplest-chain-bridge",
                                    "asr": ASR_MODEL, "llm": LLM_MODEL,
                                    "llm_enable": LLM_ENABLE,
                                    "history_turns": HISTORY_TURNS,
                                    "uptime_s": int(time.time() - _STATS_T0)})
        if path == "/stats":
            with _LOCK:
                snap = dict(_STATS)
                snap["history_len"] = len(_HISTORY)
            return self._send(200, snap)
        if path == "/":
            return self._send(200, HELP_TEXT, "text/plain; charset=utf-8")
        return self._send(404, {"code": 404, "msg": "no such path: " + path})

    # ---- POST
    def do_POST(self):
        raw_path = self.path
        path = raw_path.split("?")[0].rstrip("/")
        query = raw_path.split("?", 1)[1] if "?" in raw_path else ""
        want_llm = LLM_ENABLE and "llm=0" not in query

        try:
            body = self._read_body()
        except Exception as exc:
            return self._send(400, {"code": 1, "msg": "读请求体失败: %r" % (exc,)})

        _stat("requests")
        _stat("bytes_in", len(body))
        ctype = self.headers.get("Content-Type") or ""

        if path == "/reset":
            with _LOCK:
                n = len(_HISTORY)
                del _HISTORY[:]
            return self._send(200, {"code": 0, "cleared": n})

        if path == "/llm":
            try:
                obj = json.loads(body.decode("utf-8", "replace") or "{}")
                txt = (obj.get("text") or "").strip()
            except Exception:
                txt = body.decode("utf-8", "replace").strip()
            if not txt:
                return self._send(400, {"code": 1, "msg": "缺 text"})
            reply, ms, ok, err = siliconflow_llm(txt)
            return self._send(200 if ok else 502,
                              {"code": 0 if ok else 1, "text": reply,
                               "llm_ms": ms, "error": err})

        if path not in ("/api/v1/asr", "/v1/audio/transcriptions", "/asr", ""):
            return self._send(404, {"code": 404, "msg": "no such path: " + path})

        t0 = time.time()
        try:
            audio, mime = pick_audio(body, ctype)
        except Exception as exc:
            _stat("asr_fail")
            return self._send(400, {"code": 1, "msg": "取音频失败: %s" % exc})

        if not audio:
            _stat("asr_fail")
            return self._send(400, {"code": 1, "msg": "音频为空"})

        # 1) ASR
        try:
            asr_text, asr_ms = siliconflow_asr(audio, mime)
        except Exception as exc:
            _stat("asr_fail")
            _log({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "stage": "asr",
                  "error": str(exc)[:400], "bytes": len(audio)})
            return self._send(502, {"code": 1, "msg": "ASR 失败: %s" % str(exc)[:300]})
        _stat("asr_ms_sum", asr_ms)

        if not asr_text:
            _stat("empty_asr")
            _log({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "stage": "empty",
                  "bytes": len(audio), "asr_ms": asr_ms})
            return self._send(200, {"code": 0, "text": "", "asr_text": "",
                                    "asr_ms": asr_ms, "llm_ms": 0,
                                    "total_ms": int((time.time() - t0) * 1000),
                                    "note": "ASR 没识别出内容"})

        # 2) LLM
        llm_ms, llm_ok, llm_err, reply = 0, True, "", asr_text
        if want_llm:
            reply, llm_ms, llm_ok, llm_err = siliconflow_llm(asr_text)
            _stat("llm_ms_sum", llm_ms)
        total = int((time.time() - t0) * 1000)

        _stat("ok")
        rec = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "stage": "ok",
               "bytes": len(audio), "mime": mime, "asr_text": asr_text,
               "llm_text": reply if want_llm else None, "llm_ok": llm_ok,
               "asr_ms": asr_ms, "llm_ms": llm_ms, "total_ms": total}
        if llm_err:
            rec["llm_error"] = llm_err
        _log(rec)

        out = {"code": 0, "text": reply, "asr_text": asr_text,
               "asr_ms": asr_ms, "llm_ms": llm_ms, "total_ms": total,
               "llm_enable": want_llm, "llm_ok": llm_ok}
        if llm_err:
            out["llm_error"] = llm_err
        return self._send(200, out)


HELP_TEXT = """simplest-chain-bridge —— 本地 ASR + LLM 桥（硅基流动 SiliconFlow，给 113 板 ASR_REMOTE_URL 用）

板端起链：
  ASR_REMOTE_URL=http://<本机IP>:%d/api/v1/asr ASR_REMOTE_TIMEOUT=60 /funasr-lite

端点：
  POST /api/v1/asr              音频 → ASR → LLM → 回 LLM 文本（板端主用）
  POST /api/v1/asr?llm=0        只做 ASR
  POST /v1/audio/transcriptions openai 协议备选（字段 file）
  POST /llm                     {"text":"..."} 只跑 LLM
  POST /reset                   清空多轮上下文
  GET  /health  /stats  /

手测：
  curl -s -F audio=@seg.wav http://127.0.0.1:%d/api/v1/asr
  curl -s -F audio=@seg.wav "http://127.0.0.1:%d/api/v1/asr?llm=0"
  curl -s -H 'Content-Type: application/json' -d '{"text":"你好"}' http://127.0.0.1:%d/llm
""" % (PORT, PORT, PORT, PORT)


_STATS_T0 = time.time()


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if not API_KEY:
        sys.stderr.write("缺 SILICONFLOW_API_KEY\n")
        return 2

    srv = ThreadingHTTPServer((BIND, PORT), Handler)
    srv.daemon_threads = True

    probe = "ok"
    try:
        t = siliconflow_llm("在吗")
        probe = "llm=%s %dms" % ("ok" if t[2] else "FAIL:" + t[3][:80], t[1])
    except Exception as exc:
        probe = "FAIL:%r" % (exc,)

    print("=" * 68)
    print("siliconflow-bridge 监听 http://%s:%d" % (BIND, PORT))
    for ip in _local_ips():
        print("  板端填:  ASR_REMOTE_URL=http://%s:%d/api/v1/asr" % (ip, PORT))
    print("  ASR 模型 %s (lang=%s)   LLM 模型 %s   LLM_ENABLE=%s  多轮=%d 轮"
          % (ASR_MODEL, ASR_LANG, LLM_MODEL, LLM_ENABLE, HISTORY_TURNS))
    print("  自检: %s" % probe)
    print("  日志: %s" % LOG_JSONL)
    print("=" * 68)
    sys.stdout.flush()

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        srv.server_close()
    return 0


def _local_ips():
    """列出本机可用于局域网访问的 IPv4，板子要填的就是其中之一。"""
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.append(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except Exception:
        pass
    return ips or ["127.0.0.1"]


if __name__ == "__main__":
    sys.exit(main())
