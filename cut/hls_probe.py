"""CONNECT-02b final: HLS master -> variant -> init.mp4 + media segment."""
import asyncio
import gzip
import json
import re
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen
import aiohttp
sys.path.insert(0, '/tmp')
from functional_probe import check, results

BASE = 'http://127.0.0.1:8123'
ENTITY = 'camera.127_0_0_1'
OUT = Path('/tmp/rtsp-results.json')
TOK = Path('/runtime/ha-cut-probe-session.json')


def token():
    return json.loads(TOK.read_text())['access_token']


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


def uris(text):
    """Every URI="..." plus bare media lines, as relative paths."""
    found = re.findall(r'URI="([^"]+)"', text)
    found += [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith('#')]
    return [u.lstrip('./') for u in found]


def main():
    url = asyncio.run(ws_url())
    check('frontend-stream-url-issued', bool(url) and '/api/hls/' in (url or ''), 'url=%s' % url)
    if not url:
        finalize()
        return 1
    base_dir = url.rsplit('/', 1)[0]

    master = ''
    for _ in range(30):
        try:
            status, blob = fetch(url)
            master = blob.decode('utf-8', 'replace')
            if status == 200 and master.startswith('#EXTM3U'):
                break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2)
    check('hls-master-playlist', master.startswith('#EXTM3U'), 'bytes=%d' % len(master))

    variants = [u for u in uris(master)]
    vfile = variants[0] if variants else 'playlist.m3u8'
    variant = ''
    for _ in range(20):
        try:
            status, blob = fetch(base_dir + '/' + vfile)
            variant = blob.decode('utf-8', 'replace')
            if status == 200 and variant.startswith('#EXTM3U'):
                break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2)
    check('hls-variant-playlist', variant.startswith('#EXTM3U'),
          'file=%s bytes=%d' % (vfile, len(variant)))

    media = uris(variant)
    init_ok = seg_ok = False
    init_detail = seg_detail = 'none found'
    for uri in media:
        target = base_dir + '/' + uri
        try:
            status, blob = fetch(target)
            if status == 200 and len(blob) > 100:
                if uri.endswith('.mp4') or 'init' in uri:
                    if not init_ok:
                        init_ok = True
                        init_detail = 'http=%s bytes=%d file=%s' % (status, len(blob), uri)
                elif uri.endswith(('.m4s', '.ts', '.mp4')) or 'segment' in uri:
                    if not seg_ok:
                        seg_ok = True
                        seg_detail = 'http=%s bytes=%d file=%s' % (status, len(blob), uri)
        except Exception as exc:  # noqa: BLE001
            seg_detail = '%s:%s uri=%s' % (type(exc).__name__, str(exc)[:60], uri)
        if init_ok and seg_ok:
            break
        time.sleep(1)
    check('hls-init-segment', init_ok, init_detail)
    check('hls-media-segment-bytes', seg_ok, seg_detail)

    finalize()
    return 0 if all(x['passed'] for x in results) else 1


def finalize():
    out = {'status': 'passed' if all(x['passed'] for x in results) else 'failed',
           'checks': results,
           'boundary': 'Generated H264 test pattern on local RTSP (mediamtx) through HA stream/'
                       'ffmpeg to HLS fMP4. No real camera hardware; no browser playback '
                       'smoothness measurement.'}
    OUT.write_text(json.dumps(out, indent=2) + '\n')
    print('hls-probe: %d/%d' % (sum(x['passed'] for x in results), len(results)))


if __name__ == '__main__':
    raise SystemExit(main())
