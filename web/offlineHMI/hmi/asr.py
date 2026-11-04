# -*- coding: utf-8 -*-
"""语音侧共用件：WAV 读写／重采样 · RMS 分段器 · 远程 ASR 客户端 · 录音源。

对齐 repos/HA4FPAI/lang/server/archive/webui.py 的两段式设计与 asr_chain.py 的 dBFS 口径：
  粗段(turn) = 停顿容忍, 默认 2000 ms，决定"这一轮说到哪算完"
  细段(fine) = 600 ms VAD 口径，只在粗段内部切，逐段识别再拼

ASR 后端不再用本地 funasr 引擎（那套 asr/engine.py 已删），改走板上现役远程 HTTP：
  POST {base}/api/v1/asr          multipart 字段 audio → {"code":0,"text":...}
  只 ASR 时加 ?llm=0（mimo_bridge 的约定，见 archive/mimo_bridge.py:16）
标准库实现，板上 python3.8 零新增依赖；numpy 仅用于重采样（板上已有）。
"""
from __future__ import annotations

import io
import math
import os
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
import wave

TARGET = 16000


# ------------------------------------------------------------------ WAV / PCM
def read_wav(path) -> "tuple[bytes, int]":
    """任意 PCM wav → (单声道 pcm16 bytes, 原采样率)。8/16/24/32 位都吃。"""
    with wave.open(str(path), "rb") as w:
        ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if sw == 2:
        pcm = raw
        if ch > 1:                                   # 取第一声道
            pcm = b"".join(raw[i:i + 2] for i in range(0, len(raw), 2 * ch))
    elif sw == 1:                                    # u8 → s16
        vals = [(b - 128) * 256 for b in raw]
        pcm = struct.pack("<%dh" % len(vals), *vals)
        if ch > 1:
            pcm = b"".join(pcm[i:i + 2] for i in range(0, len(pcm), 2 * ch))
    elif sw == 3:                                    # 24-bit
        vals = []
        for i in range(0, len(raw), 3 * ch):
            v = int.from_bytes(raw[i:i + 3], "little", signed=True)
            vals.append(v >> 8)
        pcm = struct.pack("<%dh" % len(vals), *vals)
    elif sw == 4:
        vals = struct.unpack("<%di" % (len(raw) // 4), raw)
        vals = [v >> 16 for v in vals]
        if ch > 1:
            vals = vals[::ch]
        pcm = struct.pack("<%dh" % len(vals), *vals)
    else:
        raise ValueError("unsupported sampwidth %d" % sw)
    return pcm, sr


def write_wav(path, pcm: bytes, rate: int = TARGET) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


def to_target_rate(pcm: bytes, sr: int, target: int = TARGET) -> bytes:
    """→ target 采样率单声道 pcm16。整数倍用块平均（免抗锯齿也能听），否则线性插值。"""
    if sr == target or not pcm:
        return pcm
    try:
        import numpy as np
    except ImportError:                              # 无 numpy：只能整数抽点
        if sr % target == 0:
            k = sr // target
            return b"".join(pcm[i:i + 2] for i in range(0, len(pcm), 2 * k))
        raise
    x = np.frombuffer(pcm, dtype="<i2")
    n_out = int(round(len(x) * target / float(sr)))
    if n_out <= 0:
        return b""
    if sr % target == 0:                             # 整数倍：块平均（降噪）
        k = sr // target
        usable = len(x) // k * k
        y = x[:usable].astype("float32").reshape(-1, k).mean(axis=1)
    else:
        src = np.linspace(0, len(x) - 1, n_out, dtype="float32")
        y = np.interp(src, np.arange(len(x), dtype="float32"),
                      x.astype("float32"))
    return np.clip(y, -32768, 32767).astype("<i2").tobytes()


def dbfs(pcm: bytes) -> float:
    """一帧的 RMS dBFS（与 asr_chain.dbfs 同口径：满幅 32768 为 0 dB）。"""
    n = len(pcm) // 2
    if n <= 0:
        return -120.0
    acc = 0
    for v in struct.unpack("<%dh" % n, pcm[:n * 2]):
        acc += v * v
    rms = math.sqrt(acc / float(n))
    if rms <= 0.0:
        return -120.0
    return 20.0 * (math.log10(rms) - math.log10(32768.0))


def iter_frames(pcm: bytes, frame_ms: int, rate: int = TARGET):
    """按 frame_ms 切帧，产出 (帧 pcm16, 该帧 dBFS)。"""
    fb = int(rate * frame_ms / 1000) * 2
    for i in range(0, len(pcm), fb):
        f = pcm[i:i + fb]
        if len(f) < 4:
            break
        yield f, dbfs(f)


# ------------------------------------------------------------------ RMS 分段器
class RmsSegmenter:
    """按 dBFS 门限切段：开口 open_dbfs → 收口 close_dbfs 连续 sil_stop_ms 结束。

    lead_ms 是开口预卷：把开口前这么多音频一起收进段头，防吃轻声字头。
    """

    def __init__(self, frame_ms=100, open_dbfs=-45.0, close_dbfs=-55.0,
                 sil_stop_ms=600, min_seg_ms=300, max_seg_s=20.0, lead_ms=200,
                 rate=TARGET):
        self.frame_ms = frame_ms
        self.open_dbfs, self.close_dbfs = open_dbfs, close_dbfs
        self.sil_frames_stop = max(1, int(sil_stop_ms / frame_ms))
        self.min_bytes = int(rate * min_seg_ms / 1000) * 2
        self.max_bytes = int(rate * max_seg_s) * 2
        self.lead_frames = max(0, int(lead_ms / frame_ms))
        self._lead: list[bytes] = []
        self._buf = bytearray()
        self._sil = 0
        self._active = False

    def feed(self, frame: bytes, level: float):
        """喂一帧；收口时返回该段 pcm16，否则 None。"""
        if not self._active:
            if level >= self.open_dbfs:
                self._active = True
                self._sil = 0
                self._buf = bytearray(b"".join(self._lead))
                self._buf.extend(frame)
                self._lead = []
            else:
                self._lead.append(frame)
                if len(self._lead) > self.lead_frames:
                    self._lead.pop(0)
            return None

        self._buf.extend(frame)
        if level < self.close_dbfs:
            self._sil += 1
        else:
            self._sil = 0

        if len(self._buf) >= self.max_bytes:           # 超长强切
            return self._cut()
        if self._sil >= self.sil_frames_stop:          # 静音收口
            return self._cut(trim_tail=self._sil * self.frame_ms)
        return None

    def _cut(self, trim_tail=0):
        seg = bytes(self._buf)
        if trim_tail:                                  # 去掉收口那段静音尾巴
            keep = len(seg) - int(TARGET * trim_tail / 1000) * 2
            seg = seg[:max(0, keep)]
        self._buf = bytearray()
        self._active = False
        self._sil = 0
        self._lead = []
        if len(seg) < self.min_bytes:
            return None
        return seg

    def flush(self):
        """收尾：把还在缓冲的这段交出去（不到 min_seg 就丢弃）。"""
        if not self._active:
            return None
        return self._cut()


def fine_split(pcm: bytes, **kw):
    """细切：在一段音频内按 VAD 口径切出若干段。全静音时退回整段。"""
    seg = RmsSegmenter(**kw)
    out = []
    for f, lv in iter_frames(pcm, seg.frame_ms, TARGET):
        p = seg.feed(f, lv)
        if p:
            out.append(p)
    p = seg.flush()
    if p:
        out.append(p)
    return out or ([pcm] if pcm else [])


# ------------------------------------------------------------------ 远程 ASR
class RemoteAsr:
    """打板上现役 ASR 口。默认 mimo_bridge：POST {base}/api/v1/asr，字段 audio。"""

    def __init__(self, base: str, timeout: float = 60.0, want_llm: bool = False,
                 field: str = "audio", path: str = "/api/v1/asr"):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.want_llm = want_llm
        self.field = field
        self.path = path

    def _multipart(self, pcm: bytes, name="seg.wav"):
        boundary = "----offlinehmi" + uuid.uuid4().hex
        buf = io.BytesIO()
        buf.write(("--%s\r\n" % boundary).encode())
        buf.write(('Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
                   % (self.field, name)).encode())
        buf.write(b"Content-Type: audio/wav\r\n\r\n")
        buf.write(pcm)
        buf.write(("\r\n--%s--\r\n" % boundary).encode())
        return buf.getvalue(), boundary

    def asr(self, pcm16k: bytes) -> "tuple[str, int]":
        """→ (识别文本, 耗时 ms)。失败抛异常，由调用方显示。"""
        wav = io.BytesIO()
        with wave.open(wav, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(TARGET)
            w.writeframes(pcm16k)
        body, boundary = self._multipart(wav.getvalue())
        url = self.base + self.path + ("" if self.want_llm else "?llm=0")
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            obj = _json(r.read())
        ms = int((time.time() - t0) * 1000)
        return (obj.get("asr_text") or obj.get("text") or "").strip(), ms


def _json(raw: bytes) -> dict:
    import json
    try:
        return json.loads(raw.decode("utf-8", "replace") or "{}")
    except Exception:
        return {"text": raw.decode("utf-8", "replace")[:400]}


class MockAsr:
    """无板离线：按段长返回可辨认的假文本，用于 UI 与流水线自测。"""

    def __init__(self, delay_ms: int = 120):
        self.delay_ms = delay_ms
        self._n = 0

    def asr(self, pcm16k: bytes):
        time.sleep(self.delay_ms / 1000.0)
        self._n += 1
        dur = len(pcm16k) / 2.0 / TARGET
        return ("模拟识别第%d段（%.1fs）" % (self._n, dur), self.delay_ms)


# ------------------------------------------------------------------ 录音源
class ArecordSource:
    """板上麦克风：arecord 采 48k 单声道，再降到 16k（MCP01 是 USB 全速声卡）。"""

    def __init__(self, device: str = "hw:0,0", rate: int = 48000):
        self.device, self.rate = device, rate
        self.proc = None

    def start(self):
        self.proc = subprocess.Popen(
            ["arecord", "-q", "-D", self.device, "-c", "1", "-f", "S16_LE",
             "-r", str(self.rate), "-t", "raw", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def read(self, nbytes_16k: int) -> bytes:
        """读约 nbytes_16k 的 16k 音频（内部按比例多读）。"""
        if not self.proc or self.proc.stdout is None:
            return b""
        k = self.rate // TARGET
        raw = self.proc.stdout.read(nbytes_16k * k)
        if not raw:
            return b""
        return to_target_rate(raw, self.rate)

    def stop(self):
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=3)
            except Exception:
                self.proc.kill()
            self.proc = None


class SynthSource:
    """无麦克风环境（校机/CI）：按时间造"说话 2s + 静音 1s"循环，验流水线。"""

    def __init__(self, rate: int = TARGET):
        self.rate = rate
        self._t = 0

    def start(self):
        pass

    def read(self, nbytes: int) -> bytes:
        import numpy as np
        n = nbytes // 2
        t = (self._t + np.arange(n)) / float(self.rate)
        self._t += n
        talk = (t % 3.0) < 2.0
        sig = np.sin(2 * np.pi * 440 * t) * 8000.0
        sig = np.where(talk, sig, 0.0)
        return np.clip(sig, -32768, 32767).astype("<i2").tobytes()

    def stop(self):
        pass


class StreamWorker(threading.Thread):
    """后台流式：读源 → 粗段分段 → 收口即细切 + ASR → 追加文本。"""

    def __init__(self, source, asr, turn_sil_ms=2000, fine=None):
        super().__init__(daemon=True)
        self.source, self.asr = source, asr
        self.fine = fine or {}
        self.turn = RmsSegmenter(sil_stop_ms=turn_sil_ms, **self.fine)
        self.texts: list[str] = []
        self.rows: list[str] = []
        self.stop_flag = False
        self.error = ""

    def run(self):
        self.source.start()
        try:
            while not self.stop_flag:
                pcm = self.source.read(3200)          # ≈100 ms
                if not pcm:
                    time.sleep(0.05)
                    continue
                for f, lv in iter_frames(pcm, 100, TARGET):
                    piece = self.turn.feed(f, lv)
                    if piece:
                        self._asr_turn(piece)
        except Exception as e:
            self.error = str(e)[:120]
        finally:
            self.source.stop()

    def _asr_turn(self, piece):
        pieces = fine_split(piece, **self.fine)
        texts = []
        for i, p in enumerate(pieces):
            try:
                text, ms = self.asr.asr(p)
            except Exception as e:
                self.error = "ASR 失败: %s" % str(e)[:100]
                continue
            if text:
                texts.append(text)
            self.rows.append("%.1fs %dms %s" % (len(p) / 2.0 / TARGET, ms, text))
        if texts:
            self.texts.append(" ".join(texts))

    def flush(self):
        piece = self.turn.flush()
        if piece:
            self._asr_turn(piece)

    def stop(self):
        self.stop_flag = True
