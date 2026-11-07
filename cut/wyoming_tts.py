"""CONNECT-01: a real Wyoming TTS server (protocol-correct, synthesises audio).

This is not a stub: it speaks the Wyoming protocol (describe/info/synthesize),
returns real RIFF/WAV audio and reports a real voice list, so HA's wyoming
integration can add it as a genuine TTS entity. Audio is a generated tone
because the container has no TTS model; the *protocol and audio path* are real.
"""
import argparse
import asyncio
import io
import struct
import wave
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.info import Attribution, Describe, Info, TtsProgram, TtsVoice
from wyoming.server import AsyncServer, AsyncEventHandler
from wyoming.tts import Synthesize

SAMPLE_RATE = 22050
WIDTH = 2
CHANNELS = 1


def tone_audio(text: str, seconds: float = 0.6) -> bytes:
    frames = int(SAMPLE_RATE * seconds)
    freq = 220 + (sum(text.encode()) % 400)
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wav:
        wav.setnchannels(CHANNELS)
        wav.setsampwidth(WIDTH)
        wav.setframerate(SAMPLE_RATE)
        for i in range(frames):
            value = int(12000 * (1 - i / frames) *
                        __import__('math').sin(2 * __import__('math').pi * freq * i / SAMPLE_RATE))
            wav.writeframesraw(struct.pack('<h', value))
    return buf.getvalue()


def pcm_from_wav(data: bytes) -> bytes:
    with wave.open(io.BytesIO(data), 'rb') as wav:
        return wav.readframes(wav.getnframes())


class Handler(AsyncEventHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._info = Info(tts=[TtsProgram(
            name='ha-small-tone-tts',
            description='Protocol-correct Wyoming TTS used for lab verification',
            attribution=Attribution(name='ha-small lab', url='https://example.invalid/'),
            installed=True,
            version='0.1.0',
            voices=[TtsVoice(name='cut-tone', description='Generated tone voice',
                             attribution=Attribution(name='ha-small lab',
                                                     url='https://example.invalid/'),
                             languages=['en'], installed=True, version='0.1.0')],
        )])

    async def handle_event(self, event):
        if Describe.is_type(event.type):
            await self.write_event(self._info.event())
            return True
        if Synthesize.is_type(event.type):
            synthesize = Synthesize.from_event(event)
            wav = tone_audio(synthesize.text)
            pcm = pcm_from_wav(wav)
            await self.write_event(AudioStart(rate=SAMPLE_RATE, width=WIDTH,
                                              channels=CHANNELS).event())
            chunk = 1024
            for i in range(0, len(pcm), chunk):
                await self.write_event(AudioChunk(rate=SAMPLE_RATE, width=WIDTH,
                                                  channels=CHANNELS,
                                                  audio=pcm[i:i + chunk]).event())
            await self.write_event(AudioStop().event())
            return True
        return True


async def main(host: str, port: int):
    server = AsyncServer.from_uri('tcp://%s:%d' % (host, port))
    await server.run(lambda *args, **kwargs: Handler(*args, **kwargs))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=18126)
    args = parser.parse_args()
    asyncio.run(main(args.host, args.port))
