"""CONNECT-02b2 (partial): HLS stream recovery after source interruption."""
import asyncio
import gzip
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

import aiohttp
sys.path.insert(0, '/tmp')
from functional_probe import request, check, results

BASE = 'http://127.0.0.1:8123'
ENTITY = 'camera.127_0_0_1'
OUT = Path('/tmp/stream-recovery.json')


def token():
    return json.loads(Path('/runtime/ha-cut-probe-session.json').read_text())['access_token']


def fetch(path, timeout=45):
    req = Request(BASE + path, headers={'Authorization': 'Bearer ' + token()})
    with urlopen(req, timeout=timeout) as r:
        blob = r.read()
        if blob[:2] == b'\x1f\x8b':
            blob = gzip.decompress(blob)
        return r.status, blob


async def ws_url():
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(BASE + '/api/websocket', timeout=30) as ws:
            await ws.receive_json(timeout=20)
            await ws.send_json({'type': 'auth', 'access_token': token()})
            await ws.receive_json(timeout=20)
            await ws.send_json({'id': 1, 'type': 'camera/stream',
                                'entity_id': ENTITY, 'format': 'hls'})
            while True:
                r = await ws.receive_json(timeout=45)
                if r.get('id') == 1:
                    return r.get('result', {}).get('url')


def segment_bytes(url):
    """Total bytes of the first listed media segment for a playlist URL."""
    base = url.rsplit('/', 1)[0]
    status, blob = fetch(url)
    text = blob.decode('utf-8', 'replace')
    uris = re.findall(r'URI="([^"]+)"', text) + [ln.strip() for ln in text.splitlines()
                                                 if ln.strip() and not ln.startswith('#')]
    for uri in uris:
        if uri.endswith('.m4s') or 'segment' in uri:
            status, seg = fetch(base + '/' + uri.lstrip('./'))
            if status == 200 and len(seg) > 100:
                return len(seg), uri
    return 0, None


def publisher(action):
    if action == 'stop':
        return subprocess.run(['/venv/bin/python', '/tmp/kill_publisher.py'],
                              capture_output=True, text=True).stdout.strip()
    cmd = ('ffmpeg -hide_banner -loglevel error -re -f lavfi -i testsrc2=size=640x360:rate=15 '
           '-c:v libx264 -preset ultrafast -tune zerolatency -g 30 -pix_fmt yuv420p '
           '-f rtsp -rtsp_transport tcp rtsp://127.0.0.1:8554/cut')
    subprocess.Popen(['sh', '-c', 'exec ' + cmd])
    return 'restarted'


def main():
    url = asyncio.run(ws_url())
    check('stream-url-issued', bool(url), str(url)[:60])
    if not url:
        finalize()
        return 1
    base = url.rsplit('/', 1)[0]

    def serving():
        try:
            n, uri = segment_bytes(base + '/playlist.m3u8')
            return n
        except Exception:  # noqa: BLE001
            return 0

    before = serving()
    check('serving-before-interruption', before > 100, 'segment_bytes=%d' % before)

    print('stopping publisher:', publisher('stop'), flush=True)
    time.sleep(8)

    print('restarting publisher:', publisher('start'), flush=True)
    recovered = 0
    end = time.time() + 180
    while time.time() < end:
        recovered = serving()
        if recovered > 100:
            break
        time.sleep(4)
    check('stream-recovered-after-restart', recovered > 100, 'segment_bytes=%d' % recovered)

    status, state = request('/api/states/' + ENTITY, token=token())
    check('camera-entity-state-sane', status == 200 and
          state.get('state') in ('idle', 'streaming'), 'state=%s' % state.get('state'))

    finalize()
    return 0 if all(x['passed'] for x in results) else 1


def finalize():
    out = {'status': 'passed' if all(x['passed'] for x in results) else 'failed',
           'checks': results,
           'boundary': 'Synthetic H264 test pattern over local RTSP (mediamtx). Verifies HA '
                       're-establishes the HLS pipeline after the source publisher dies and returns. '
                       'No real camera, no weak-network, no browser smoothness measurement.'}
    OUT.write_text(json.dumps(out, indent=2) + '\n')
    print('stream-recovery: %d/%d' % (sum(x['passed'] for x in results), len(results)))


if __name__ == '__main__':
    raise SystemExit(main())
