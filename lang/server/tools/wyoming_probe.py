#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wyoming_probe.py — end-to-end Wyoming client used for acceptance testing.

Runs on the board (it imports the same `wyoming` package the services use).

  # ASR: send a 16k wav, print transcript
  python3 wyoming_probe.py asr --wav /path/x.wav --uri tcp://127.0.0.1:10301

  # TTS: synthesize, save wav, print duration
  python3 wyoming_probe.py tts --text "你好世界" --out /tmp/out.wav --uri tcp://127.0.0.1:10302

  # wake: feed a wav, report Detection / NotDetected
  python3 wyoming_probe.py wake --wav /path/x.wav --uri tcp://127.0.0.1:10300

  # describe: dump the Info event each service advertises
  python3 wyoming_probe.py describe --uri tcp://127.0.0.1:10301
"""
import argparse
import asyncio
import sys
import time
import wave

import numpy as np

from wyoming.asr import Transcribe, Transcript, TranscriptChunk, TranscriptStart
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncTcpClient
from wyoming.event import Event
from wyoming.info import Describe, Info
from wyoming.tts import Synthesize, SynthesizeStop
from wyoming.wake import Detect, Detection, NotDetected


def read_wav(path):
    with wave.open(path) as w:
        sr = w.getframerate()
        n = w.getnframes()
        data = w.readframes(n)
    return data, sr


def to16k(pcm, sr):
    if sr == 16000:
        return pcm
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    if sr == 48000:
        acc = x[:-2] + x[1:-1] + x[2:]
        y = (acc / 3.0)[::3]
    else:
        n = int(len(x) * 16000 / sr)
        y = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x)
    return (np.clip(y, -1, 1) * 32767).astype(np.int16).tobytes()


async def cmd_asr(uri, wav):
    pcm, sr = read_wav(wav)
    pcm16 = to16k(pcm, sr)
    chunks = [pcm16[i:i + 320 * 2] for i in range(0, len(pcm16), 320 * 2)]  # 20ms
    t0 = time.time()
    partials = []
    async with AsyncTcpClient.from_uri(uri) as c:
        await c.write_event(Transcribe().event())
        await c.write_event(AudioStart(rate=16000, width=2, channels=1).event())
        for ch in chunks:
            await c.write_event(AudioChunk(rate=16000, width=2, channels=1, audio=ch).event())
        await c.write_event(AudioStop().event())
        final = None
        while True:
            ev = await c.read_event()
            if ev is None:
                break
            if TranscriptChunk.is_type(ev.type):
                partials.append(TranscriptChunk.from_event(ev).text)
            elif Transcript.is_type(ev.type):
                final = Transcript.from_event(ev).text
                break
    dur_s = len(pcm16) / 32000.0
    el = time.time() - t0
    print("ASR final: %r" % (final,))
    print("  audio=%.2fs wall=%.2fs rtf=%.2f partials=%d"
          % (dur_s, el, el / dur_s if dur_s else 0, len(partials)))
    return final


async def cmd_tts(uri, text, out):
    t0 = time.time()
    audio = []
    rate = 22050
    async with AsyncTcpClient.from_uri(uri) as c:
        await c.write_event(Synthesize(text=text).event())
        while True:
            ev = await c.read_event()
            if ev is None:
                break
            if AudioChunk.is_type(ev.type):
                ach = AudioChunk.from_event(ev)
                rate = ach.rate
                audio.append(ach.audio)
            elif SynthesizeStop.is_type(ev.type) or AudioStop.is_type(ev.type):
                if SynthesizeStop.is_type(ev.type):
                    break
    data = b"".join(audio)
    if out:
        with wave.open(out, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(data)
    n = len(data) / 2.0 / rate
    el = time.time() - t0
    print("TTS %r -> %.2fs audio @%d, wall %.2fs, rtf %.2f"
          % (text, n, rate, el, el / n if n else 0))
    if out:
        print("  saved", out)


async def cmd_wake(uri, wav):
    pcm, sr = read_wav(wav)
    pcm16 = to16k(pcm, sr)
    hits, not_detected = [], False
    async with AsyncTcpClient.from_uri(uri) as c:
        await c.write_event(Detect().event())
        await c.write_event(AudioStart(rate=16000, width=2, channels=1).event())
        for i in range(0, len(pcm16), 320 * 2):
            await c.write_event(AudioChunk(rate=16000, width=2, channels=1,
                                           audio=pcm16[i:i + 320 * 2]).event())
        await c.write_event(AudioStop().event())
        while True:
            ev = await c.read_event()
            if ev is None:
                break
            if Detection.is_type(ev.type):
                hits.append(Detection.from_event(ev).name)
                break
            if NotDetected.is_type(ev.type):
                not_detected = True
                break
    print("WAKE hits=%r not_detected=%s" % (hits, not_detected))
    return hits


async def cmd_describe(uri):
    async with AsyncTcpClient.from_uri(uri) as c:
        await c.write_event(Describe().event())
        ev = await c.read_event()
    if ev is None:
        print("no info")
        return
    info = Info.from_event(ev)
    for kind in ("asr", "tts", "wake"):
        progs = getattr(info, kind) or []
        for p in progs:
            print("%s: %s | %s" % (kind, p.name, p.description))
            for m in (getattr(p, "models", None) or []):
                print("   model:", getattr(m, "name", "?"))
            for v in (getattr(p, "voices", None) or []):
                print("   voice:", getattr(v, "name", "?"))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("asr"); a.add_argument("--wav", required=True); a.add_argument("--uri", default="tcp://127.0.0.1:10301")
    t = sub.add_parser("tts"); t.add_argument("--text", required=True); t.add_argument("--out", default=""); t.add_argument("--uri", default="tcp://127.0.0.1:10302")
    w = sub.add_parser("wake"); w.add_argument("--wav", required=True); w.add_argument("--uri", default="tcp://127.0.0.1:10300")
    d = sub.add_parser("describe"); d.add_argument("--uri", required=True)
    args = ap.parse_args()

    loop = asyncio.get_event_loop()
    if args.cmd == "asr":
        loop.run_until_complete(cmd_asr(args.uri, args.wav))
    elif args.cmd == "tts":
        loop.run_until_complete(cmd_tts(args.uri, args.text, args.out))
    elif args.cmd == "wake":
        loop.run_until_complete(cmd_wake(args.uri, args.wav))
    elif args.cmd == "describe":
        loop.run_until_complete(cmd_describe(args.uri))


if __name__ == "__main__":
    main()
