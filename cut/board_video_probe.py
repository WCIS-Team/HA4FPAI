"""Check whether HA's HLS/stream layer can consume the board's fMP4 segments.

The board (192.168.1.113:8092) exposes a rolling HLS window: /h264/init.mp4 plus
12 one-second .m4s segments listed in /h264/manifest.json. HA's generic camera
expects a normal HLS server, so the question is whether the board's own playlist
path exists or whether we must assemble one locally.
"""
import json
import urllib.request
from urllib.error import HTTPError

BASE = 'http://192.168.1.113:8092'


def get(path, timeout=10):
    req = urllib.request.Request(BASE + path)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), r.headers.get('Content-Type')
    except HTTPError as exc:
        return exc.code, exc.read()[:120], exc.headers.get('Content-Type')
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)[:120].encode(), None


def main():
    out = {}

    status, body, ctype = get('/h264/manifest.json')
    manifest = json.loads(body)
    segs = manifest.get('segments', [])
    out['manifest'] = {'session': manifest.get('session'), 'codec': manifest.get('codec'),
                        'width': manifest.get('width'), 'height': manifest.get('height'),
                        'init': manifest.get('init'), 'segment_count': len(segs)}

    status, body, ctype = get('/h264/' + manifest['init'])
    out['init'] = {'http': status, 'bytes': len(body), 'type': ctype}

    status, body, ctype = get('/h264/' + segs[-1]['name'])
    out['segment'] = {'http': status, 'bytes': len(body), 'type': ctype,
                      'name': segs[-1]['name']}

    # Is there ANY playlist the board serves? Try the common spellings.
    probes = {}
    for path in ['/h264/playlist.m3u8', '/h264/master_playlist.m3u8', '/h264/index.m3u8',
                 '/playlist.m3u8', '/master_playlist.m3u8', '/h264/live.m3u8',
                 '/h264/stream.m3u8', '/api/stream.m3u8']:
        status, body, ctype = get(path)
        probes[path] = {'http': status, 'body': body[:80].decode('utf-8', 'replace')}
    out['playlist_probes'] = probes

    print(json.dumps(out, indent=2, ensure_ascii=False))
    return out


if __name__ == '__main__':
    main()
