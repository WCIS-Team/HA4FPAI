#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""voice_gateway.py — P0：把 ASR/TTS/KWS 三个 Wyoming 服务合成**一个进程**。

## 为什么要合并

合并前是三个独立 Python 进程，每个都背一份解释器 + numpy + onnxruntime 句柄：

    实测 RSS（合并前）
      klar-voice-tts   199.8 MB
      klar-voice-asr   113.0 MB
      klar-voice-wake   55.5 MB
      ---------------------------
      合计             373.7 MB

其中约 **2 × 30 MB** 是纯粹重复的运行时底座（第二个、第三个进程各自再分配一份）。
合并后三个服务共用一个 VoiceKit 单例 → 同一份 sherpa-onnx + 同一份 onnxruntime，
预计省 **~60 MB**。

注意：这不改变「共享 env / 独立 session」的语义——
  * 模型 + onnxruntime 仍然是进程级唯一（共享）
  * 每条 Wyoming 连接仍然各自 new_asr() / new_kws() / VadSession（独立）

## 端口不变

合并后仍然监听三个独立端口，对 HA/客户端完全透明：
    10300 wake / 10301 asr / 10302 tts

## 48 kHz 转码（重要）

输入（麦克风）和输出（喇叭）都可能是 48 kHz，必须处理好：

  输入 48k → 16k：decimate3-mavg3（3 抽样平均再抽取 3）
      —— 这是本板既有的验证过的做法（voice_kit.resample_48_to_16）

  输出 22050 Hz（matcha 模型的采样率）→ 48k：
      —— 之前缺这一环；Wyoming 客户端拿到 22050 数据如果要送 48k 声卡
         还得自己升。这里补上「按客户端请求格式输出」的能力：
         若客户端在 AudioStart 里声明了 rate=48000，就把 TTS 输出重采样到 48k。

  重采样实现：22050 → 48000 用线性插值（音频质量够用；若要更好可换 polyphase）。
  因为 22050 与 48000 不是整数倍，必须做真插值，不能简单重复采样。

## 启动

    python3 voice_gateway.py            # 起全部三个端口
    python3 voice_gateway.py --only asr # 只起一个（调试用）
"""
import argparse
import asyncio
import logging
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import voice_kit  # noqa: E402
import voice_common as vc  # noqa: E402

from wyoming.asr import Transcribe, Transcript, TranscriptChunk  # noqa: E402
from wyoming.audio import AudioChunk, AudioStart, AudioStop  # noqa: E402
from wyoming.event import Event  # noqa: E402
from wyoming.info import Describe  # noqa: E402
from wyoming.server import AsyncEventHandler, AsyncServer  # noqa: E402
from wyoming.tts import Synthesize, SynthesizeStart, SynthesizeStop  # noqa: E402
from wyoming.wake import Detect, Detection, NotDetected  # noqa: E402

LOG = logging.getLogger("voice_gateway")


# ---------------------------------------------------------------------------
# 重采样：TTS 输出 → 客户端期望的采样率
# ---------------------------------------------------------------------------
def resample_linear(x, src_rate, dst_rate):
    """通用线性重采样（float32 in/out）。22050→48000 这类非整数倍必须走这里。"""
    x = np.asarray(x, dtype=np.float32)
    if src_rate == dst_rate or x.size == 0:
        return x
    n_out = int(round(len(x) * dst_rate / float(src_rate)))
    if n_out <= 0:
        return np.zeros(0, dtype=np.float32)
    # 用原信号的采样位置做线性插值
    src_idx = np.arange(len(x), dtype=np.float64)
    dst_idx = np.linspace(0.0, len(x) - 1.0, n_out, dtype=np.float64)
    return np.interp(dst_idx, src_idx, x).astype(np.float32)


def to_16k_pcm16(data, rate):
    """Wyoming int16le bytes @rate -> float32 mono @16k（板侧统一入口）。"""
    return vc.to_16k(data, rate)


# ---------------------------------------------------------------------------
# ASR handler（每个连接一个独立 AsrSession）
# ---------------------------------------------------------------------------
class AsrHandler(AsyncEventHandler):
    def __init__(self, reader, writer, cfg):
        super().__init__(reader, writer)
        self.cfg = cfg
        self.session = None
        self._t0 = None

    async def handle_event(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            await self.write_event(vc.asr_info(self.cfg).event())
            return True
        if Transcribe.is_type(event.type):
            self.session = vc.kit().new_asr()
            self._t0 = time.time()
            return True
        if AudioChunk.is_type(event.type):
            if self.session is None:
                self.session = vc.kit().new_asr()
                self._t0 = time.time()
            ch = AudioChunk.from_event(event)
            text = self.session.accept(to_16k_pcm16(ch.audio, ch.rate))
            if text:
                await self.write_event(TranscriptChunk(text=text).event())
            return True
        if AudioStop.is_type(event.type):
            if self.session is None:
                await self.write_event(Transcript(text="").event())
                return False
            text = self.session.end()
            took = int((time.time() - self._t0) * 1000) if self._t0 else -1
            self.session = None
            await self.write_event(Transcript(text=text or "").event())
            LOG.info("ASR final=%r in %dms", text, took)
            return False
        return True


# ---------------------------------------------------------------------------
# TTS handler（输出按客户端要求的采样率重采样）
# ---------------------------------------------------------------------------
class TtsHandler(AsyncEventHandler):
    def __init__(self, reader, writer, cfg):
        super().__init__(reader, writer)
        self.cfg = cfg
        self._req_rate = None       # 客户端在 AudioStart 之前可能不声明，这里自己定

    async def handle_event(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            await self.write_event(vc.tts_info(self.cfg).event())
            return True
        if Synthesize.is_type(event.type):
            req = Synthesize.from_event(event)
            await self.write_event(SynthesizeStart().event())

            t = self.cfg["tts"]
            speed = float(t.get("speed", 1.0))
            sid = int(t.get("sid", 0))
            voice = getattr(req, "voice", None)
            if voice is not None and getattr(voice, "speaker", None):
                try:
                    sid = int(voice.speaker)
                except (TypeError, ValueError):
                    pass

            samples, rate = vc.kit().synth(req.text, speed=speed, sid=sid)
            samples = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)

            # 48k 输出支持：客户端可从 context 里带 out_rate=48000
            ctx = getattr(req, "context", None) or {}
            want = None
            try:
                want = int(ctx.get("out_rate") or ctx.get("sample_rate") or 0)
            except (TypeError, ValueError):
                want = None
            out_rate = want if want in (16000, 22050, 24000, 44100, 48000) else rate
            if out_rate != rate:
                samples = resample_linear(samples, rate, out_rate)
                LOG.info("TTS resample %d -> %d Hz (%d samples)", rate, out_rate, len(samples))

            pcm = (samples * 32767.0).astype(np.int16)
            await self.write_event(
                AudioStart(rate=out_rate, width=2, channels=1).event())
            step = max(1, int(out_rate * 0.05))
            for i in range(0, len(pcm), step):
                await self.write_event(AudioChunk(
                    rate=out_rate, width=2, channels=1,
                    audio=pcm[i:i + step].tobytes()).event())
            await self.write_event(AudioStop().event())
            await self.write_event(SynthesizeStop().event())
            LOG.info("TTS %r -> %.2fs @%d", req.text, len(pcm) / float(out_rate), out_rate)
            return False
        return True


# ---------------------------------------------------------------------------
# Wake/KWS handler
# ---------------------------------------------------------------------------
def _label(hit):
    return hit.split("@")[-1].strip() if "@" in hit else hit.strip()


class WakeHandler(AsyncEventHandler):
    def __init__(self, reader, writer, cfg):
        super().__init__(reader, writer)
        self.cfg = cfg
        self.session = None
        self.detected = False

    async def handle_event(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            await self.write_event(vc.wake_info(self.cfg).event())
            return True
        if Detect.is_type(event.type):
            self.session = vc.kit().new_kws()
            self.detected = False
            return True
        if AudioChunk.is_type(event.type):
            if self.session is None:
                self.session = vc.kit().new_kws()
            ch = AudioChunk.from_event(event)
            hits = self.session.accept(to_16k_pcm16(ch.audio, ch.rate))
            if hits:
                self.detected = True
                for h in hits:
                    LOG.info("wake: %r", _label(h))
                    await self.write_event(Detection(name=_label(h)).event())
                return False
            return True
        if AudioStop.is_type(event.type):
            if self.session is not None:
                for h in self.session.end():
                    self.detected = True
                    await self.write_event(Detection(name=_label(h)).event())
            if not self.detected:
                await self.write_event(NotDetected().event())
            self.session = None
            return False
        return True


# ---------------------------------------------------------------------------
# 一个进程里起三个 server
# ---------------------------------------------------------------------------
async def serve_all(cfg, only=None):
    srv = cfg["server"]
    want = {
        "wake": ("tcp://%s:%d" % (srv.get("wyoming_bind", "0.0.0.0"), int(srv.get("wake_port", 10300))),
                 WakeHandler),
        "asr":  ("tcp://%s:%d" % (srv.get("wyoming_bind", "0.0.0.0"), int(srv.get("asr_port", 10301))),
                 AsrHandler),
        "tts":  ("tcp://%s:%d" % (srv.get("wyoming_bind", "0.0.0.0"), int(srv.get("tts_port", 10302))),
                 TtsHandler),
    }
    if only:
        want = {k: v for k, v in want.items() if k == only}

    started = []

    def make_factory(cls):
        def factory(reader, writer):
            return cls(reader, writer, cfg)
        return factory

    for name, (uri, cls) in want.items():
        server = AsyncServer.from_uri(uri)
        await server.start(make_factory(cls))
        started.append((name, uri, server))
        LOG.info("%s listening on %s", name, uri)

    LOG.info("all servers up: %s", ", ".join(n for n, _, _ in started))
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        for name, uri, server in started:
            try:
                await server.stop()
            except Exception:                    # noqa: BLE001
                pass


def main():
    cfg = voice_kit.load_config()
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["wake", "asr", "tts"], help="只起一个服务（调试）")
    ap.add_argument("--no-warm", action="store_true", help="不预热模型")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("VmRSS:"):
                LOG.info("baseline RSS before model load: %.1f MB",
                         int(line.split()[1]) / 1024.0)
                break

    if not args.no_warm:
        t0 = time.time()
        # 共享同一个 kit：三套模型在同一进程里，只付一份运行时
        if not args.only or args.only == "wake":
            vc.kit().new_kws()
        if not args.only or args.only == "asr":
            vc.kit().new_asr()
        LOG.info("kws+asr warm in %.1fs", time.time() - t0)

    loop = asyncio.get_event_loop()
    loop.run_until_complete(serve_all(cfg, only=args.only))


if __name__ == "__main__":
    main()
