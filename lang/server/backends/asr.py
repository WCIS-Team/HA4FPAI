#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""asr.py —— ASR 档。契约：run([seg]) -> (text, [seg_meta])；单段失败记 meta 不断链。

remote-api  现役口子（asr/remote_asr.py 的协议形状原样移植，自包含不跨包 import）：
              multipart  HTTP multipart/form-data 字段 audio → {"code":0,"text":...}（code 非 0 视为失败）
                         （旧名 funasr 已废，传了会 ValueError 提示改用 multipart；注意 FunASR 官方
                          runtime 走 WebSocket，不是这个）
              openai  multipart 字段 file + model    → {"text":...}
              json    {"audio_b64","format","sample_rate"} → {"text":...}
            环境变量沿用既有约定：ASR_REMOTE_URL/PROTO/FIELD/MODEL/TOKEN/TIMEOUT/RETRY/EXTRA。
            board 侧 remote_asr.health() 探 /health 的姿势也保留（本服务 /health 恒 200）。
echo        自检档：不外发，回固定多句文本（tests 与无声卡环境用）。
"""

import base64
import json
import re
import time
import urllib.error
import urllib.request
import uuid

from core import Stage, WORK_RATE

from backends import audioio

TAG_RE = re.compile(r"<\|[^|]*\|>")
TEXT_KEYS = ("text", "preds", "sentence", "transcription", "result", "data", "output", "asr_text")


def _env(key, default=None):
    v = __import__("os").environ.get(key)
    return v if v not in (None, "") else default


def _strip_tags(s):
    return TAG_RE.sub("", s or "").strip()


def _extract(obj, depth=0):
    """各家五花八门的返回里抠文本：str / {"text"} / {"result":[...]} / 列表都能吃。"""
    if obj is None or depth > 6:
        return None
    if isinstance(obj, str):
        return _strip_tags(obj)
    if isinstance(obj, dict):
        for k in TEXT_KEYS:
            if k in obj:
                got = _extract(obj[k], depth + 1)
                if got:
                    return got
        return None
    if isinstance(obj, (list, tuple)):
        for item in obj:
            got = _extract(item, depth + 1)
            if got:
                return got
    return None


def _multipart(fields):
    """fields: [(name, filename, ctype, bytes)] → (body, content_type)。标准库手搓。"""
    b = uuid.uuid4().hex
    parts = []
    for name, fn, ct, data in fields:
        parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"%s\r\n"
                      "Content-Type: %s\r\n\r\n" %
                      (b, name, ("; filename=\"%s\"" % fn) if fn else "",
                       ct or "application/octet-stream")).encode("utf-8"))
        parts.append(data)
        parts.append(b"\r\n")
    parts.append(("--%s--\r\n" % b).encode("utf-8"))
    return b"".join(parts), "multipart/form-data; boundary=%s" % b


def _join(texts):
    """中文段直接拼，夹了拉丁文才补空格（整句识别拼回的口径）。"""
    out = ""
    for t in texts:
        t = (t or "").strip()
        if not t:
            continue
        if out and out[-1].isascii() and out[-1].isalpha() and t[0].isascii() and t[0].isalpha():
            out += " "
        out += t
    return out


class RemoteApi(Stage):
    name = "remote-api"

    def __init__(self, *a, **kw):
        Stage.__init__(self, *a, **kw)
        p = dict(self.cfg.online("asr"))          # 在线 ASR 统一在 lang/config/online.yaml
        p.update(self.profile or {})              # 档内若有同名键则覆盖
        self.url = _env("ASR_REMOTE_URL") or p.get("url", "")
        if not self.url:
            raise ValueError("remote-api 档要给 endpoint：asr_profiles.remote-api.url "
                             "或环境变量 ASR_REMOTE_URL")
        self.proto = (_env("ASR_REMOTE_PROTO") or p.get("proto", "multipart") or "multipart").lower()
        if self.proto == "funasr":      # 2026-11-04 更名：funasr 已废，明确提示迁移
            raise ValueError("ASR_REMOTE_PROTO=funasr 已更名，请改用 multipart"
                             "（同一行为：HTTP multipart/form-data 字段 audio，回 {code,text}）")
        if self.proto not in ("multipart", "openai", "json"):
            raise ValueError("ASR_REMOTE_PROTO 只认 multipart|openai|json，给的是 %s" % self.proto)
        self.field = _env("ASR_REMOTE_FIELD") or p.get("field", "") or \
            ("file" if self.proto == "openai" else "audio")
        self.model = _env("ASR_REMOTE_MODEL") or p.get("model", "")
        self.token = _env("ASR_REMOTE_TOKEN") or p.get("api_key") or p.get("token", "")
        self.timeout = float(_env("ASR_REMOTE_TIMEOUT") or p.get("timeout", 30) or 30)
        self.retry = int(_env("ASR_REMOTE_RETRY") if _env("ASR_REMOTE_RETRY") is not None
                         else p.get("retry", 1))
        extra = _env("ASR_REMOTE_EXTRA") or p.get("extra", "")
        self.extra = [kv.split("=", 1) for kv in re.split(r"[,\s]+", extra) if "=" in kv]

    # ---- 三种协议 -----------------------------------------------------------
    def _post(self, body, ctype):
        req = urllib.request.Request(self.url, data=body, method="POST",
                                     headers={"Content-Type": ctype,
                                              "Accept": "application/json"})
        if self.token:
            req.add_header("Authorization", "Bearer " + self.token)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace") or "{}")

    def _one(self, wav_bytes):
        sr = audioio.decode_payload(wav_bytes)[1]
        if self.proto == "json":
            payload = {"audio_b64": base64.b64encode(wav_bytes).decode("ascii"),
                       "format": "wav", "sample_rate": sr}
            for k, v in self.extra:
                payload[k] = v
            obj = self._post(json.dumps(payload).encode("utf-8"), "application/json")
        else:
            fields = [(self.field, "seg.wav", "audio/wav", wav_bytes)]
            if self.proto == "openai":
                fields.append(("model", "", "text/plain", self.model.encode("utf-8")))
            for k, v in self.extra:
                fields.append((k, "", "text/plain", str(v).encode("utf-8")))
            body, ctype = _multipart(fields)
            obj = self._post(body, ctype)
        text = _extract(obj)
        if self.proto == "multipart" and isinstance(obj, dict) and int(obj.get("code", 0) or 0) != 0:
            raise RuntimeError(("远端 code=%s msg=%s"
                                % (obj.get("code"), obj.get("msg", "")))[:160])
        if text is None:
            raise RuntimeError("回包里抠不到文本：%s" % json.dumps(obj, ensure_ascii=False)[:160])
        return text

    # ---- 契约 ---------------------------------------------------------------
    def run(self, segs):
        texts, meta = [], []
        for i, seg in enumerate(segs):
            wav = audioio.encode_wav(seg, WORK_RATE)
            t0, err, text = time.time(), None, ""
            for attempt in range(self.retry + 1):        # 失败重试，末次失败只记不抛
                try:
                    text = self._one(wav)
                    break
                except Exception as exc:
                    err = ("%s: %s" % (type(exc).__name__, exc))[:200]
                    if attempt >= self.retry:
                        break
                    time.sleep(0.2)
            meta.append({"seg": i, "ms": int((time.time() - t0) * 1000),
                         "status": "error" if err else "ok",
                         **({"error": err} if err else {})})
            texts.append(text)
        return _join(texts), meta


class Echo(Stage):
    """自检档：不外发。回固定多句文本，给分词器/TTS 留活干。"""
    name = "echo"

    def run(self, segs):
        text = ("你好，这里是链路自检。第二句用来测切分？"
                "最后再来一句长一点的，看看分词器会不会把它切开，顺便把 TTS 的峰值压下去。")
        return text, [{"seg": i, "ms": 0, "status": "echo"} for i in range(len(segs))]


class SherpaAsr(Stage):
    """本地 sherpa-onnx 流式 zipformer 转写（移植 `HA4FPAI/boards/voice_kit.py` 的 AsrSession）。

    **共用 env**：recognizer 走 sherpa-onnx 自带的 onnxruntime（与 VAD/KWS/TTS 同一份），
    与 `shared.ort()`（pip 的 onnxruntime）是两份不同的库——各归各的家族，不混用。
    每段新建一个 stream（独立解码状态），跑完整段再出文本。

    配置（`asr_profiles.<档名>`）：tokens / encoder / decoder / joiner / num_threads（默认 2）
    / sample_rate（16000）/ feature_dim（80）/ decoding_method（greedy_search）。
    板上取值见 `repos/HA4FPAI/lang/config/asr.yaml` 的 asr_profiles（路径在 /mnt/data/models/asr）。
    """
    name = "sherpa"

    def __init__(self, *a, **kw):
        Stage.__init__(self, *a, **kw)
        sherpa_onnx = self.shared.sherpa()       # 唯一运行时（与 VAD/KWS/TTS 共用）
        p = self.profile
        need = ("tokens", "encoder", "decoder", "joiner")
        missing = [k for k in need if not p.get(k)]
        if missing:
            raise ValueError("sherpa-asr 档缺 %s（asr_profiles.%s）"
                             % (missing, self.name))
        import os
        for k in need:
            if not os.path.isfile(p[k]):
                raise ValueError("sherpa-asr 模型文件不在：%s = %s" % (k, p[k]))
        self.rec = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=p["tokens"], encoder=p["encoder"], decoder=p["decoder"],
            joiner=p["joiner"],
            num_threads=int(p.get("num_threads", 2) or 2),
            sample_rate=int(p.get("sample_rate", WORK_RATE) or WORK_RATE),
            feature_dim=int(p.get("feature_dim", 80) or 80),
            decoding_method=p.get("decoding_method", "greedy_search"))

    def run(self, segs):
        import numpy as np
        texts, meta = [], []
        for i, seg in enumerate(segs):
            t0 = time.time()
            st = self.rec.create_stream()
            st.accept_waveform(WORK_RATE, np.asarray(seg, dtype=np.float32))
            st.input_finished()
            while self.rec.is_ready(st):
                self.rec.decode_stream(st)
            text = (self.rec.get_result(st) or "").strip()
            texts.append(text)
            meta.append({"seg": i, "ms": int((time.time() - t0) * 1000),
                         "status": "ok" if text else "empty"})
        return _join(texts), meta
