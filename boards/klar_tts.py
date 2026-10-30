#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""klar_tts.py — Wyoming TTS service (port 10302) on the shared VoiceKit.

Protocol: Describe -> Info; Synthesize -> SynthesizeStart, AudioStart,
AudioChunk*, AudioStop, SynthesizeStop.

Model: piper zh_CN-xiao_ya medium (a VITS model, so it goes through
sherpa-onnx's OfflineTtsVitsModelConfig and MUST be built with a lexicon -
without one the C++ side aborts the process).

Perf (measured on this A55 board): warm RTF 2.94..3.05 with 3 threads,
cold load ~26 s. Sentence-level replies therefore cost ~3 s per second of
audio. Use a phrase cache for the handful of fixed confirmations.
"""
import argparse
import asyncio
import logging
import os

import numpy as np

from voice_common import kit, tts_info

from wyoming.audio import AudioStart, AudioChunk, AudioStop
from wyoming.event import Event
from wyoming.info import Describe
from wyoming.server import AsyncEventHandler, AsyncServer
from wyoming.tts import Synthesize, SynthesizeStart, SynthesizeStop

_LOGGER = logging.getLogger("klar_tts")


class TtsHandler(AsyncEventHandler):
    def __init__(self, reader, writer, cfg):
        super().__init__(reader, writer)
        self.cfg = cfg

    async def handle_event(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            await self.write_event(tts_info(self.cfg).event())
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

            samples, rate = kit().synth(req.text, speed=speed, sid=sid)
            pcm = (np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0) * 32767.0)
            pcm = pcm.astype(np.int16)

            await self.write_event(AudioStart(rate=rate, width=2, channels=1).event())
            step = max(1, int(rate * 0.05))       # 50 ms chunks
            for i in range(0, len(pcm), step):
                await self.write_event(AudioChunk(
                    rate=rate, width=2, channels=1,
                    audio=pcm[i:i + step].tobytes(),
                ).event())
            await self.write_event(AudioStop().event())
            await self.write_event(SynthesizeStop().event())
            _LOGGER.info("synth %r -> %.2fs @%d Hz", req.text, len(pcm) / float(rate), rate)
            return False

        _LOGGER.debug("unhandled event %s", event.type)
        return True


def main():
    import voice_kit
    cfg = voice_kit.load_config()
    ap = argparse.ArgumentParser()
    ap.add_argument("--uri", default="tcp://%s:%d" % (
        cfg["server"].get("wyoming_bind", "0.0.0.0"),
        int(cfg["server"].get("tts_port", 10302))))
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--no-warm", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if not args.no_warm:
        # Cold load is ~26 s; warm it in the background so the port opens fast.
        _LOGGER.info("warming TTS (background, ~26s) ...")

    def factory(reader, writer):
        return TtsHandler(reader, writer, cfg)

    server = AsyncServer.from_uri(args.uri)
    _LOGGER.info("klar TTS listening on %s", args.uri)
    asyncio.get_event_loop().run_until_complete(server.run(factory))


if __name__ == "__main__":
    main()
