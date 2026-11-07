"""Point a generic camera at the board's real camera through the local HLS bridge.

generic's config flow is two-step:
  step 1: stream_source, still_image_url, username, password, advanced{...}
  step 2: device name / manufacturer / model / configuration_url
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, '/tmp')
from functional_probe import request, check, results

OUT = Path('/tmp/board-camera-results.json')
BRIDGE = 'http://127.0.0.1:8099'


def main():
    token = json.loads(Path('/runtime/ha-cut-probe-session.json').read_text())['access_token']

    status, cfg = request('/api/config', token=token)
    check('generic-component-loaded', 'generic' in (cfg.get('components') or []))

    # Drop the synthetic camera so the board one is unambiguous.
    status, entries = request('/api/config/config_entries/entry', token=token)
    for e in entries if isinstance(entries, list) else []:
        if e.get('domain') == 'generic' and str(e.get('title', '')).startswith('127_0_0_1'):
            request('/api/config/config_entries/entry/' + e['entry_id'], token=token, method='DELETE')
    time.sleep(3)

    status, flow = request('/api/config/config_entries/flow', {'handler': 'generic'}, token)
    fid = flow.get('flow_id') if isinstance(flow, dict) else None
    check('flow-started', bool(fid), str(flow)[:80])
    if not fid:
        finalize()
        return 1

    step1 = {
        'stream_source': BRIDGE + '/hls/playlist.m3u8',
        'still_image_url': BRIDGE + '/snapshot.jpg',
        'advanced': {'framerate': 30.0, 'verify_ssl': False,
                     'rtsp_transport': 'tcp', 'authentication': 'basic'},
    }
    try:
        status, res = request('/api/config/config_entries/flow/' + fid, step1, token)
    except Exception as exc:  # noqa: BLE001
        check('flow-step1-accepted', False, '%s: %s' % (type(exc).__name__, str(exc)[:100]))
        finalize()
        return 1
    step2 = isinstance(res, dict) and res.get('type') == 'form'
    check('flow-step1-accepted', step2,
          'type=%s errors=%s' % (res.get('type') if isinstance(res, dict) else res,
                                 res.get('errors') if isinstance(res, dict) else str(res)[:110]))
    if not step2:
        finalize()
        return 1

    device = {'confirmed_ok': True}
    status, res = request('/api/config/config_entries/flow/' + fid, device, token)
    created = isinstance(res, dict) and res.get('type') == 'create_entry'
    check('board-camera-created', created, 'type=%s errors=%s' % (
        res.get('type') if isinstance(res, dict) else res,
        res.get('errors') if isinstance(res, dict) else str(res)[:120]))
    if not created:
        finalize()
        return 1

    entity = None
    end = time.monotonic() + 60
    while time.monotonic() < end:
        status, states = request('/api/states', token=token)
        if isinstance(states, list):
            cams = [s['entity_id'] for s in states
                    if s.get('entity_id', '').startswith('camera.')]
            if cams:
                entity = cams[0]
                break
        time.sleep(1)
    check('board-camera-entity', entity is not None, 'entity=%s' % entity)
    if not entity:
        finalize()
        return 1

    status, state = request('/api/states/' + entity, token=token)
    attrs = state.get('attributes') or {}
    check('board-camera-streams', bool((attrs.get('supported_features') or 0) & 2),
          'supported_features=%s' % attrs.get('supported_features'))
    check('board-camera-idle-or-streaming', state.get('state') in ('idle', 'streaming'),
          'state=%s' % state.get('state'))

    finalize()
    return 0 if all(x['passed'] for x in results) else 1


def finalize():
    out = {'status': 'passed' if all(x['passed'] for x in results) else 'failed',
           'checks': results,
           'source': 'real camera on board 192.168.1.113 (SDI -> PL -> PS -> VPU), exposed through '
                     'the board fragment API and re-published as HLS by a local read-only bridge.',
           'boundary': 'HA runs on this workstation (the board only has Python 3.8). The bridge is '
                       'a read-only client of the board: it opens no capture instance and never '
                       'touches the SDK/DMA/VPU. Frame latency and browser smoothness not measured.'}
    OUT.write_text(json.dumps(out, indent=2) + '\n')
    print('board-camera-probe: %d/%d' % (sum(x['passed'] for x in results), len(results)))


if __name__ == '__main__':
    raise SystemExit(main())

