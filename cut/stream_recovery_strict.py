"""Strict re-check: does HA really re-read the RTSP source after it comes back?"""
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

BASE = 'http://127.0.0.1:8123'
ENTITY = 'camera.127_0_0_1'
MTX = Path('/tmp/mediamtx.log')
OUT = Path('/tmp/stream-recovery-strict.json')


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


def seg_info(url):
    base = url.rsplit('/', 1)[0]
    status, blob = fetch(base + '/playlist.m3u8')
    text = blob.decode('utf-8', 'replace')
    uris = re.findall(r'URI="([^"]+)"', text) + [ln.strip() for ln in text.splitlines()
                                                 if ln.strip() and not ln.startswith('#')]
    media = [u.lstrip('./') for u in uris if u.endswith('.m4s') or 'segment' in u]
    if not media:
        return {'segment': None, 'bytes': 0}
    last = media[-1]
    st, seg = fetch(base + '/' + last)
    seq = re.search(r'#EXT-X-MEDIA-SEQUENCE:(\d+)', text)
    return {'segment': last, 'bytes': len(seg) if st == 200 else 0,
            'media_sequence': seq.group(1) if seq else None}


def camera_state():
    status, body = fetch('/api/states/' + ENTITY) if False else (None, None)
    from urllib.request import Request as R, urlopen as u
    req = R(BASE + '/api/states/' + ENTITY, headers={'Authorization': 'Bearer ' + token()})
    with u(req, timeout=20) as r:
        data = json.load(r)
    return data.get('state'), (data.get('attributes') or {}).get('frontend_stream_type')


def reading_sessions_since(ts):
    if not MTX.exists():
        return 0
    rows = [ln for ln in MTX.read_text(errors='replace').splitlines() if 'is reading from' in ln]
    return len(rows)


def main():
    log = MTX.read_text(errors='replace') if MTX.exists() else ''
    reads_before = len([ln for ln in log.splitlines() if 'is reading from' in ln])
    print('mediamtx read sessions before:', reads_before)

    print('killing publisher:', subprocess.run(
        ['/venv/bin/python', '/tmp/kill_publisher.py'], capture_output=True, text=True
    ).stdout.strip())
    time.sleep(20)

    cmd = ('ffmpeg -hide_banner -loglevel error -re -f lavfi -i testsrc2=size=640x360:rate=15 '
           '-c:v libx264 -preset ultrafast -tune zerolatency -g 30 -pix_fmt yuv420p '
           '-f rtsp -rtsp_transport tcp rtsp://127.0.0.1:8554/cut')
    subprocess.Popen(['sh', '-c', 'exec ' + cmd])
    print('publisher restarted, waiting for republish...')
    time.sleep(20)

    results = {}
    # 1) without touching HA: is the old worker still serving fresh segments?
    url_old = None
    try:
        url_old = asyncio.run(ws_url())
    except Exception as exc:  # noqa: BLE001
        print('ws after restart failed:', type(exc).__name__, str(exc)[:80])

    info = seg_info(url_old) if url_old else {}
    state, fst = camera_state()
    log2 = MTX.read_text(errors='replace') if MTX.exists() else ''
    reads_after = len([ln for ln in log2.splitlines() if 'is reading from' in ln])
    results = {
        'read_sessions_before': reads_before,
        'read_sessions_after': reads_after,
        'new_read_sessions': reads_after - reads_before,
        'segment': info.get('segment'), 'segment_bytes': info.get('bytes'),
        'media_sequence': info.get('media_sequence'),
        'camera_state': state, 'frontend_stream_type': fst,
    }
    results['source_re_read'] = (reads_after - reads_before) > 0
    results['pipeline_produces_media'] = (info.get('bytes') or 0) > 100
    results['camera_available_again'] = state in ('idle', 'streaming')
    results['verdict'] = ('recovered' if results['source_re_read'] and results['pipeline_produces_media']
                          else 'not-recovered-without-intervention')
    OUT.write_text(json.dumps(results, indent=2) + '\n')
    print(json.dumps(results, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
