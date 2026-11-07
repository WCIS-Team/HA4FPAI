"""CONNECT-02: camera pipeline with a synthetic JPEG source (loopback).
Boundary: synthetic still image only, no real camera / RTSP / HLS streaming.
"""
import json
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen
sys.path.insert(0, '/tmp')
from functional_probe import request, check, results

BASE = 'http://127.0.0.1:8123'
URL = 'http://127.0.0.1:18095/cam.jpg'


def raw(path, token):
    req = Request(BASE + path, headers={'Authorization': 'Bearer ' + token})
    with urlopen(req, timeout=30) as response:
        return response.status, response.headers.get('Content-Type'), response.read()


def main():
    token = json.loads(Path('/runtime/ha-cut-probe-session.json').read_text())['access_token']
    st, entries = request('/api/config/config_entries/entry', token=token)
    if isinstance(entries, list):
        for entry in entries:
            if entry.get('domain') == 'generic':
                request('/api/config/config_entries/entry/' + entry['entry_id'], token=token, method='DELETE')
                time.sleep(2)
    st, flows = request('/api/config/config_entries/flow', {'handler': 'generic'}, token=token)
    check('camera-flow-start', st == 200)
    flow_id = flows['flow_id']
    st, done = request('/api/config/config_entries/flow/' + flow_id, {
        'still_image_url': URL, 'advanced': {'framerate': 2, 'verify_ssl': False}}, token=token)
    if isinstance(done, dict) and done.get('step_id') == 'user_confirm':
        check('camera-preview-confirm-step', True)
        st, done = request('/api/config/config_entries/flow/' + flow_id, {'confirmed_ok': True}, token=token)
    created = isinstance(done, dict) and done.get('type') == 'create_entry'
    check('camera-entry-created', created, '' if created else repr(done)[:200])
    entity = None
    end = time.monotonic() + 60
    while time.monotonic() < end:
        st, states = request('/api/states', token=token)
        if st == 200 and isinstance(states, list):
            cams = [s['entity_id'] for s in states
                    if s.get('entity_id', '').startswith('camera.')]
            if cams:
                entity = cams[0]
                break
        time.sleep(0.5)
    check('camera-entity-available', entity is not None, 'entity=%s' % entity)
    ok = False
    detail = ''
    if entity:
        try:
            st, body = request('/api/states/' + entity, token=token)
            pic = (body.get('attributes') or {}).get('entity_picture') if isinstance(body, dict) else None
            status, ctype, blob = raw(pic or ('/api/camera_proxy/' + entity), token)
            ok = status == 200 and blob[:2] == b'\xff\xd8' and len(blob) > 1000
            detail = 'http=%s ctype=%s bytes=%d via=%s' % (status, ctype, len(blob), pic)
            if ok:
                Path('/tmp/camera-snapshot.jpg').write_bytes(blob)
        except Exception as exc:  # noqa: BLE001
            detail = type(exc).__name__ + ':' + str(exc)[:120]
    check('camera-snapshot-jpeg', ok, detail)
    result = {'status': 'passed' if all(x['passed'] for x in results) else 'failed',
              'checks': results, 'entity': entity,
              'boundary': 'Synthetic JPEG over loopback. Real camera, RTSP, HLS stream and '
                          'browser playback not tested.'}
    Path('/tmp/camera-results.json').write_text(json.dumps(result, indent=2) + '\n')
    print('camera-probe: %d/%d' % (sum(x['passed'] for x in results), len(results)))
    return 0 if all(x['passed'] for x in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
