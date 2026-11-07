"""CONNECT-01: verify the lab Wyoming TTS server end to end."""
import asyncio
import json
import sys
from pathlib import Path
from wyoming.audio import AudioChunk
from wyoming.client import AsyncTcpClient
from wyoming.info import Describe, Info
from wyoming.tts import Synthesize

sys.path.insert(0, '/tmp')
from functional_probe import check, results

HOST, PORT = '127.0.0.1', 18126


async def main():
    client = AsyncTcpClient(HOST, PORT)
    await client.connect()
    await client.write_event(Describe().event())
    info = Info.from_event(await client.read_event())
    programs = [p.name for p in info.tts]
    voices = [(v.name, list(v.languages)) for p in info.tts for v in p.voices]
    check('wyoming-describe', bool(programs), 'programs=%s' % programs)
    check('wyoming-voice-listed', bool(voices), 'voices=%s' % voices)

    await client.write_event(Synthesize(text='turn on the cut test switch').event())
    pcm = bytearray()
    chunks = 0
    saw_start = saw_stop = False
    while True:
        event = await client.read_event()
        if event is None:
            break
        if event.type == 'audio-start':
            saw_start = True
        elif event.type == 'audio-stop':
            saw_stop = True
            break
        elif event.type == 'audio-chunk':
            chunk = AudioChunk.from_event(event)
            pcm.extend(chunk.audio)
            chunks += 1
    await client.disconnect()

    check('wyoming-audio-start', saw_start)
    check('wyoming-audio-stop', saw_stop)
    check('wyoming-pcm-received', len(pcm) > 5000, 'chunks=%d bytes=%d' % (chunks, len(pcm)))

    out = {'status': 'passed' if all(x['passed'] for x in results) else 'failed',
           'checks': results, 'programs': programs, 'voices': voices,
           'pcm_bytes': len(pcm),
           'boundary': 'Protocol-correct Wyoming TTS with generated tone audio; no real speech '
                       'model, no microphone, no speaker. Verifies the Wyoming audio path only.'}
    Path('/tmp/wyoming-results.json').write_text(json.dumps(out, indent=2) + '\n')
    print('wyoming-probe: %d/%d pcm=%d' % (sum(x['passed'] for x in results), len(results), len(pcm)))
    return 0 if all(x['passed'] for x in results) else 1


raise SystemExit(asyncio.run(main()))
