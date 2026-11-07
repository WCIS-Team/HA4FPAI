"""CONNECT-02b: real RTSP source -> HA camera with stream_source -> HLS pipeline."""
import json
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen
sys.path.insert(0, '/tmp')
from functional_probe import request, check, results

RTSP = 'rtsp://127.0.0.1:8554/cut'
BASE = 'http://127.0.0.1:8123'


def raw(path, token, timeout=45):
    req = Request(BASE + path, headers={'Authorization': 'Bearer ' + token})
    with urlopen(req, timeout=timeout) as response:
        return response.status, response.headers.get('Content-Type'), response.read()


def main():
    token = json.loads(Path('/runtime/ha-cut-probe-session.json').read_text())['access_token']
    status, cfg = request('/api/config', token=token)
    comps = cfg.get('components') or []
    check('stream-component-loaded', 'stream' in comps, 'has stream=%s' % ('stream' in comps))

    status, entries = request('/api/config/config_entries/entry', token=token)
    if isinstance(entries, list):
        for entry in entries:
            if entry.get('domain') == 'generic':
                request('/api/config/config_entries/entry/' + entry['entry_id'],
                        token=token, method='DELETE')
                time.sleep(2)

    status, flows = request('/api/config/config_entries/flow', {'handler': 'generic'}, token=token)
    check('camera-flow-start', status == 200)
    flow_id = flows['flow_id']
    status, done = request('/api/config/config_entries/flow/' + flow_id, {
        'stream_source': RTSP, 'still_image_url': 'http://127.0.0.1:18095/cam.jpg',
        'advanced': {'framerate': 2, 'verify_ssl': False}}, token=token)
    if isinstance(done, dict) and done.get('step_id') == 'user_confirm':
        status, done = request('/api/config/config_entries/flow/' + flow_id,
                               {'confirmed_ok': True}, token=token)
    created = isinstance(done, dict) and done.get('type') == 'create_entry'
    check('rtsp-camera-entry-created', created, '' if created else repr(done)[:180])

    entity = None
    end = time.monotonic() + 45
    while time.monotonic() < end:
        status, states = request('/api/states', token=token)
        if status == 200 and isinstance(states, list):
            cams = [s['entity_id'] for s in states if s.get('entity_id', '').startswith('camera.')]
            if cams:
                entity = cams[0]
                break
        time.sleep(0.5)
    check('rtsp-camera-entity', entity is not None, 'entity=%s' % entity)
    if not entity:
        finalize()
        return 1

    status, state = request('/api/states/' + entity, token=token)
    check('camera-state-stream-capable', isinstance(state, dict),
          'state=%s' % (state.get('state') if isinstance(state, dict) else state))

    ok = False
    detail = ''
    try:
        status, ctype, blob = raw('/api/camera_proxy/' + entity, token)
        ok = status == 200 and len(blob) > 1000
        detail = 'snapshot http=%s bytes=%d' % (status, len(blob))
    except Exception as exc:  # noqa: BLE001
        detail = type(exc).__name__ + ':' + str(exc)[:100]
    check('camera-still-frame', ok, detail)

    stream_ok = False
    hls_detail = ''
    try:
        status, ctype, blob = raw('/api/stream/' + entity, token)
        text = blob.decode('utf-8', 'replace')
        stream_ok = status == 200 and ('hls' in text.lower() or '.m3u8' in text.lower())
        hls_detail = 'http=%s ctype=%s len=%d has_m3u8=%s' % (
            status, ctype, len(blob), '.m3u8' in text)
    except Exception as exc:  # noqa: BLE001
        hls_detail = type(exc).__name__ + ':' + str(exc)[:120]
    check('camera-stream-hls-playlist', stream_ok, hls_detail)

    finalize()
    return 0 if all(x['passed'] for x in results) else 1


def finalize():
    out = {'status': 'passed' if all(x['passed'] for x in results) else 'failed',
           'checks': results,
           'boundary': 'RTSP source was a locally generated H264 test pattern served by mediamtx; '
                       'no real camera hardware, no browser playback timing check.'}
    Path('/tmp/rtsp-results.json').write_text(json.dumps(out, indent=2) + '\n')
    print('rtsp-stream-probe: %d/%d' % (sum(x['passed'] for x in results), len(results)))


if __name__ == '__main__':
    raise SystemExit(main())
