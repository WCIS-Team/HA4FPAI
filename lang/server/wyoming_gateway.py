#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wyoming_gateway.py —— Wyoming 面：同进程起 wake(:10300)/asr(:10301)/tts(:10302) 三服务。

**取代** `HA4FPAI/boards/voice_gateway.py`：协议不变（HA 原生 Wyoming 集成零改动），
但底层不再用 voice_kit，而是调用本服务 `core.Chain` 的可替换后端（`chain.stage(...)`），
配置全从 `lang/config/` 读（`server.yaml` 的 `wake_port/asr_port/tts_port` 等）。

与 `app.py` 的 HTTP 面**并存**：HTTP 给自定义集成与自测，Wyoming 给 HA 上层。

## 本版本的取舍（重要）
Wyoming 是**分块推流**，而本服务后端契约是"整段"⇒ 这里把 AudioChunk **累积**到 `AudioStop`
再一次性出 `Transcript` / `Detection`。**不支持部分转写**（不发中间 TranscriptChunk）。
要真流式，得给后端加流式接口（另立，不在本版）。

## 依赖
`wyoming` 包（板上与 HA 侧有；本机 PC 未装，所以本文件只在板上跑，`app.py`/`smoke` 都不 import 它）。
"""
import argparse
import asyncio
import array
import logging
import time

from cfg import Config
from core import Chain, WORK_RATE
from backends import audioio

from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.event import Event
from wyoming.info import (AsrModel, AsrProgram, Attribution, Describe, Info,
                          TtsProgram, TtsVoice, WakeModel, WakeProgram)
from wyoming.server import AsyncEventHandler, AsyncServer
from wyoming.tts import Synthesize, SynthesizeStart, SynthesizeStop
from wyoming.wake import Detect, Detection, NotDetected

LOG = logging.getLogger("lang-wyoming")
SHERPA_ATTR = Attribution(name="k2-fsa/sherpa-onnx",
                          url="https://github.com/k2-fsa/sherpa-onnx")


def _pcm16(samples):
    a = array.array("h", (max(-32768, min(32767, int(v * 32768.0))) for v in samples))
    return a.tobytes()


def _to_work(samples, rate):
    return audioio.to_work_rate(samples, rate, WORK_RATE)


# ---------------------------------------------------------------- info（Describe 回包）
def asr_info(cfg):
    tier = cfg.get("chain", "asr")
    label = cfg.profile("asr", tier).get("label") or tier
    return Info(asr=[AsrProgram(
        name="lang-server-sherpa", attribution=SHERPA_ATTR, installed=True,
        description="lang/server 可替换 ASR 后端（默认本地 zipformer；可切外部 API）",
        version="1", models=[AsrModel(
            name=label, attribution=SHERPA_ATTR, installed=True,
            description=label, version=None, languages=["zh", "en"])],
        supports_transcript_streaming=False, requires_external_vad=False)],
        tts=[], handle=[], intent=[], wake=[], mic=[], snd=[])


def tts_info(cfg):
    tier = cfg.get("chain", "tts")
    p = cfg.profile("tts", tier)
    name = p.get("label") or p.get("kind") or tier
    return Info(tts=[TtsProgram(
        name="lang-server-tts", attribution=SHERPA_ATTR, installed=True,
        description="lang/server 可替换 TTS 后端（本地 matcha/vits 或外部服务）",
        version="1", voices=[TtsVoice(
            name=name, attribution=SHERPA_ATTR, installed=True,
            description=name, version=None, languages=["zh-CN"])],
        supports_synthesize_streaming=False)],
        asr=[], handle=[], intent=[], wake=[], mic=[], snd=[])


def wake_info(cfg):
    tier = cfg.get("chain", "kws")
    p = cfg.profile("kws", tier)
    phrases = []
    kf = p.get("keywords_file")
    try:
        import os
        if kf and not os.path.isabs(kf) and p.get("model_dir"):
            kf = os.path.join(p["model_dir"], kf)
        if kf and os.path.exists(kf):
            with open(kf, "r", encoding="utf-8") as f:
                phrases = [ln.split("@")[-1].strip() for ln in f if "@" in ln]
    except OSError:
        pass
    return Info(wake=[WakeProgram(
        name="lang-server-kws", attribution=SHERPA_ATTR, installed=True,
        description="lang/server KWS（zipformer）", version="1",
        models=[WakeModel(
            name=tier, attribution=SHERPA_ATTR, installed=True,
            description="keyword spotter", version=None,
            languages=["zh", "en"],
            phrase=", ".join(phrases) if phrases else None)])],
        asr=[], tts=[], handle=[], intent=[], mic=[], snd=[])


# ---------------------------------------------------------------- handlers
class _Buf(object):
    """把分块 int16le 音频累积起来，AudioStop 时一次取整段 16k float。"""

    def __init__(self):
        self.rate = None
        self.buf = bytearray()

    def feed(self, chunk):
        self.rate = chunk.rate
        self.buf.extend(chunk.audio)

    def take_work(self):
        if not self.buf:
            return []
        samples, rate = audioio.decode_payload(bytes(self.buf), rate_hint=self.rate or WORK_RATE)
        samples, _ = _to_work(samples, rate)
        self.buf = bytearray()
        return samples


class AsrHandler(AsyncEventHandler):
    def __init__(self, reader, writer, cfg, chain):
        super().__init__(reader, writer)
        self.cfg, self.chain, self.buf = cfg, chain, _Buf()

    async def handle_event(self, event):
        if Describe.is_type(event.type):
            await self.write_event(asr_info(self.cfg).event())
            return True
        if Transcribe.is_type(event.type):
            self.buf = _Buf()
            return True
        if AudioChunk.is_type(event.type):
            self.buf.feed(AudioChunk.from_event(event))
            return True
        if AudioStop.is_type(event.type):
            samples = self.buf.take_work()
            text = ""
            if samples:
                inst = self.chain.stage("asr")
                if inst is not None:
                    with inst.lock:
                        text, _meta = inst.run([samples])
            await self.write_event(Transcript(text=text or "").event())
            LOG.info("ASR final=%r", (text or "")[:40])
            return False
        return True


class WakeHandler(AsyncEventHandler):
    def __init__(self, reader, writer, cfg, chain):
        super().__init__(reader, writer)
        self.cfg, self.chain, self.buf = cfg, chain, _Buf()

    async def handle_event(self, event):
        if Describe.is_type(event.type):
            await self.write_event(wake_info(self.cfg).event())
            return True
        if Detect.is_type(event.type):
            self.buf = _Buf()
            return True
        if AudioChunk.is_type(event.type):
            self.buf.feed(AudioChunk.from_event(event))
            return True
        if AudioStop.is_type(event.type):
            samples = self.buf.take_work()
            hit, tag = False, ""
            if samples:
                inst = self.chain.stage("kws")
                if inst is not None:
                    with inst.lock:
                        hit, tag = inst.run([samples])
            if hit:
                await self.write_event(Detection(name=(tag or "wake")).event())
            else:
                await self.write_event(NotDetected().event())
            return False
        return True


class TtsHandler(AsyncEventHandler):
    def __init__(self, reader, writer, cfg, chain):
        super().__init__(reader, writer)
        self.cfg, self.chain = cfg, chain

    async def handle_event(self, event):
        if Describe.is_type(event.type):
            await self.write_event(tts_info(self.cfg).event())
            return True
        if Synthesize.is_type(event.type):
            req = Synthesize.from_event(event)
            await self.write_event(SynthesizeStart().event())

            # 分词器切短 → 逐块 TTS → 拼接 → 可选输出重采样（她定的链尾两环）
            tok = self.chain.stage("tokenizer")
            chunks = [req.text]
            if tok is not None:
                try:
                    chunks = [c for c in tok.run(req.text) if c.strip()] or [req.text]
                except Exception:
                    chunks = [req.text]
            tts = self.chain.stage("tts")
            pieces = []
            if tts is not None:
                for ch in chunks:
                    with tts.lock:
                        smp, rt = tts.run(ch)
                    if smp:
                        pieces.append((smp, rt))
            samples, rate = audioio.concat(
                pieces, gap_s=float(self.cfg.get("server", "chunk_gap_ms", "80") or 80) / 1000.0,
                out_rate=int(self.cfg.get("server", "out_rate", "0") or 0))

            # 客户端可在 context 里要 out_rate（wyoming 惯例）
            ctx = getattr(req, "context", None) or {}
            try:
                want = int(ctx.get("out_rate") or ctx.get("sample_rate") or 0)
            except (TypeError, ValueError):
                want = 0
            if want in (16000, 22050, 24000, 44100, 48000) and want != rate and samples:
                samples = audioio.resample_linear(samples, rate, want)
                rate = want

            if not samples:
                rate = rate or 22050
            pcm = _pcm16(samples)
            await self.write_event(AudioStart(rate=int(rate), width=2, channels=1).event())
            step = max(2, int(rate * 0.05) * 2)        # 50 ms 一块（字节）
            for i in range(0, len(pcm), step):
                await self.write_event(AudioChunk(
                    rate=int(rate), width=2, channels=1, audio=pcm[i:i + step]).event())
            await self.write_event(AudioStop().event())
            await self.write_event(SynthesizeStop().event())
            LOG.info("TTS %r -> %.2fs @%d", req.text, len(samples) / float(rate or 1), rate)
            return False
        return True


async def serve_all(cfg, chain, only=None):
    srv = cfg.section("server")
    want = {
        "wake": ("tcp://%s:%s" % (srv.get("wyoming_bind", "0.0.0.0"), srv.get("wake_port", "10300")),
                 WakeHandler),
        "asr": ("tcp://%s:%s" % (srv.get("wyoming_bind", "0.0.0.0"), srv.get("asr_port", "10301")),
                AsrHandler),
        "tts": ("tcp://%s:%s" % (srv.get("wyoming_bind", "0.0.0.0"), srv.get("tts_port", "10302")),
                TtsHandler),
    }
    if only:
        want = {k: v for k, v in want.items() if k == only}

    started = []
    for name, (uri, cls) in want.items():
        def factory(reader, writer, _cls=cls):
            return _cls(reader, writer, cfg, chain)
        server = AsyncServer.from_uri(uri)
        await server.start(factory)
        started.append((name, uri, server))
        LOG.info("%s listening on %s", name, uri)
    LOG.info("wyoming up: %s", ", ".join(n for n, _, _ in started))
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        for _n, _uri, server in started:
            try:
                await server.stop()
            except Exception:                     # noqa: BLE001
                pass


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="lang/server 的 Wyoming 面（wake/asr/tts）")
    ap.add_argument("--only", choices=["wake", "asr", "tts"])
    ap.add_argument("--config", default=None)
    ap.add_argument("--no-warm", action="store_true", help="不预热 sherpa-onnx 运行时")
    args = ap.parse_args()

    cfg = Config(args.config)
    chain = Chain(cfg)
    LOG.info("config=%s  chain: asr=%s tts=%s kws=%s",
             cfg.path, chain.active("asr"), chain.active("tts"), chain.active("kws"))
    if not args.no_warm:
        w = chain.warm()                               # 唯一 onnx 运行时先拉起，四模型共用
        LOG.info("warm: sherpa=%s stages=%s %dms",
                 "ok" if w.get("sherpa") else "FAIL(%s)" % w.get("sherpa_error", "?"),
                 w.get("stages"), w.get("elapsed_ms", 0))
    asyncio.get_event_loop().run_until_complete(serve_all(cfg, chain, only=args.only))


if __name__ == "__main__":
    main()
