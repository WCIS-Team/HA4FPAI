#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""klar_asr.py — Wyoming ASR service (port 10301) on the shared VoiceKit.

Protocol: Describe -> Info; Transcribe -> AudioStart, AudioChunk+, AudioStop
-> TranscriptStart, TranscriptChunk*, Transcript  (wyoming `asr` domain).

Shared env: the recognizer + its onnxruntime are created once in VoiceKit.
Independent sessions: every connection gets its own AsrSession, so two HA
pipelines streaming at the same time never share decoder state.
"""
import argparse
import asyncio
import logging
import time

from voice_common import asr_info, kit, to_16k

from wyoming.asr import Transcribe, Transcript, TranscriptChunk, TranscriptStart
from wyoming.audio import AudioChunk, AudioStop
from wyoming.event import Event
from wyoming.info import Describe
from wyoming.server import AsyncEventHandler, AsyncServer

_LOGGER = logging.getLogger("klar_asr")


class AsrHandler(AsyncEventHandler):
    def __init__(self, reader, writer, cfg):
        super().__init__(reader, writer)
        self.cfg = cfg
        self.session = None
        self._t0 = None

    async def handle_event(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            await self.write_event(asr_info(self.cfg).event())
            return True

        if Transcribe.is_type(event.type):
            tr = Transcribe.from_event(event)
            self.session = kit().new_asr()
            self._t0 = time.time()
            _LOGGER.info("transcribe name=%s lang=%s", tr.name, tr.language)
            return True

        if AudioChunk.is_type(event.type):
            if self.session is None:
                self.session = kit().new_asr()
                self._t0 = time.time()
            chunk = AudioChunk.from_event(event)
            text = self.session.accept(to_16k(chunk.audio, chunk.rate))
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
            _LOGGER.info("final=%r in %dms", text, took)
            return False

        _LOGGER.debug("unhandled event %s", event.type)
        return True


def main():
    import voice_kit
    cfg = voice_kit.load_config()
    ap = argparse.ArgumentParser()
    ap.add_argument("--uri", default="tcp://%s:%d" % (
        cfg["server"].get("wyoming_bind", "0.0.0.0"),
        int(cfg["server"].get("asr_port", 10301))))
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--no-warm", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if not args.no_warm:
        _LOGGER.info("warming ASR ...")
        t0 = time.time()
        kit().new_asr()
        _LOGGER.info("ASR warm in %.1fs", time.time() - t0)

    def factory(reader, writer):
        return AsrHandler(reader, writer, cfg)

    server = AsyncServer.from_uri(args.uri)
    _LOGGER.info("klar ASR listening on %s", args.uri)
    asyncio.get_event_loop().run_until_complete(server.run(factory))


if __name__ == "__main__":
    main()
