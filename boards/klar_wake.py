#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""klar_wake.py — Wyoming wake/KWS service (port 10300) on the shared VoiceKit.

Protocol: Describe -> Info; Detect -> AudioStart, AudioChunk+, AudioStop
-> Detection (on hit) or NotDetected.  (wyoming `wake` domain)

Shared env, independent sessions: KeywordSpotter + onnxruntime are created once;
each connection gets its own KwsSession so concurrent wake detectors do not
share transducer state.

Hits are reported as the label after '@' in keywords.txt ("@小克" -> "小克"),
so HA's wake-word slot sees the phrase rather than pinyin tokens.
"""
import argparse
import asyncio
import logging

from voice_common import kit, to_16k, wake_info

from wyoming.audio import AudioChunk, AudioStop
from wyoming.event import Event
from wyoming.info import Describe
from wyoming.server import AsyncEventHandler, AsyncServer
from wyoming.wake import Detect, Detection, NotDetected

_LOGGER = logging.getLogger("klar_wake")


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
            await self.write_event(wake_info(self.cfg).event())
            return True

        if Detect.is_type(event.type):
            self.session = kit().new_kws()
            self.detected = False
            return True

        if AudioChunk.is_type(event.type):
            if self.session is None:
                self.session = kit().new_kws()
            chunk = AudioChunk.from_event(event)
            hits = self.session.accept(to_16k(chunk.audio, chunk.rate))
            if hits:
                self.detected = True
                for h in hits:
                    name = _label(h)
                    _LOGGER.info("wake detected: %r", name)
                    await self.write_event(Detection(name=name).event())
                # streaming detection ends this turn
                return False
            return True

        if AudioStop.is_type(event.type):
            if self.session is not None:
                for h in self.session.end():
                    self.detected = True
                    name = _label(h)
                    _LOGGER.info("wake detected (tail): %r", name)
                    await self.write_event(Detection(name=name).event())
            if not self.detected:
                _LOGGER.info("no wake word in this audio")
                await self.write_event(NotDetected().event())
            self.session = None
            return False

        _LOGGER.debug("unhandled event %s", event.type)
        return True


def main():
    import voice_kit
    cfg = voice_kit.load_config()
    ap = argparse.ArgumentParser()
    ap.add_argument("--uri", default="tcp://%s:%d" % (
        cfg["server"].get("wyoming_bind", "0.0.0.0"),
        int(cfg["server"].get("wake_port", 10300))))
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--no-warm", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if not args.no_warm:
        _LOGGER.info("warming KWS ...")
        kit().new_kws()

    def factory(reader, writer):
        return WakeHandler(reader, writer, cfg)

    server = AsyncServer.from_uri(args.uri)
    _LOGGER.info("klar wake listening on %s", args.uri)
    asyncio.get_event_loop().run_until_complete(server.run(factory))


if __name__ == "__main__":
    main()
