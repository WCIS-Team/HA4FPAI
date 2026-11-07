"""Get the stream URL the way the frontend does: websocket camera/stream command."""
import asyncio
import json
import sys
from pathlib import Path
import aiohttp

BASE = 'http://127.0.0.1:8123'
t = json.loads(Path('/runtime/ha-cut-probe-session.json').read_text())['access_token']


async def main():
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(BASE + '/api/websocket', timeout=30) as ws:
            await ws.receive_json(timeout=20)
            await ws.send_json({'type': 'auth', 'access_token': t})
            auth = await ws.receive_json(timeout=20)
            print('auth', auth['type'])
            for cid, msg in enumerate([
                {'id': 1, 'type': 'camera/stream', 'entity_id': 'camera.127_0_0_1', 'format': 'hls'},
                {'id': 2, 'type': 'camera/capabilities', 'entity_id': 'camera.127_0_0_1'},
            ], start=1):
                await ws.send_json(msg)
                while True:
                    reply = await ws.receive_json(timeout=30)
                    if reply.get('id') == cid:
                        print(msg['type'], '->', json.dumps(reply)[:300])
                        break


asyncio.run(main())
