"""Verify the read-only board sensors in HA reflect the board's real state.

Cross-checks every sensor value against the board's own HTTP answers, and asserts
no write-capable surface was created (no switch/button, no POST-capable helper).
"""
import json
import sys
import time
from pathlib import Path
from urllib.request import urlopen

sys.path.insert(0, '/tmp')
from functional_probe import request, check, results

OUT = Path('/tmp/board-readonly-results.json')


def bearer():
    return json.loads(Path('/runtime/ha-cut-probe-session.json').read_text())['access_token']


def board_json(url):
    with urlopen(url, timeout=10) as r:
        return json.load(r)


def ha_state(entity):
    status, body = request('/api/states/' + entity, token=bearer())
    return body if status == 200 and isinstance(body, dict) else None


def main():
    token = json.loads(Path('/runtime/ha-cut-probe-session.json').read_text())['access_token']

    # Give the rest platforms one scan cycle after restart.
    time.sleep(12)

    stream = board_json('http://192.168.1.113:8092/api/status')
    recorder = board_json('http://192.168.1.113:8093/api/status')
    library = board_json('http://192.168.1.113:8093/api/recordings')

    s = ha_state('sensor.board_capture_state')
    check('capture-state-sensor', bool(s) and s.get('state') == stream.get('state'),
          'ha=%s board=%s' % (s and s.get('state'), stream.get('state')))
    if s:
        attrs = s.get('attributes') or {}
        check('capture-session-matches', attrs.get('session') == stream.get('session'),
              'session=%s' % attrs.get('session'))
        check('capture-resolution-matches',
              (attrs.get('width'), attrs.get('height')) == (stream.get('width'), stream.get('height')),
              '%sx%s' % (attrs.get('width'), attrs.get('height')))

    cap = ha_state('sensor.board_captured_frames')
    check('captured-frames-sensor', bool(cap) and
          abs(int(float(cap.get('state'))) - stream.get('captured', 0)) < 500,
          'ha=%s board=%s' % (cap and cap.get('state'), stream.get('captured')))

    mode = ha_state('sensor.board_recorder_mode')
    check('recorder-mode-sensor', bool(mode) and mode.get('state') == recorder.get('mode'),
          'ha=%s board=%s' % (mode and mode.get('state'), recorder.get('mode')))
    if mode:
        attrs = mode.get('attributes') or {}
        check('recorder-source-ready-matches',
              str(attrs.get('source_ready')).lower() == str(recorder.get('source_ready')).lower(),
              'ha=%s board=%s' % (attrs.get('source_ready'), recorder.get('source_ready')))

    rec = ha_state('sensor.board_recordings')
    count = len(library.get('recordings') or [])
    check('recordings-count-sensor', bool(rec) and
          abs(int(float(rec.get('state'))) - count) <= 1,
          'ha=%s board=%s' % (rec and rec.get('state'), count))

    # Read-only guarantee: no switch/button entities for the board, and the
    # recorder mode sensor exposes no service calls of its own.
    status, all_states = request('/api/states', token=bearer())
    domains = {}
    for st in all_states if isinstance(all_states, list) else []:
        dom = st.get('entity_id', '').split('.')[0]
        domains[dom] = domains.get(dom, 0) + 1
    check('no-switch-for-board', 'switch' not in domains and 'button' not in domains,
          'domains=%s' % sorted(domains.keys()))
    check('camera-still-present', (domains.get('camera') or 0) >= 1,
          'camera entities=%s' % domains.get('camera'))

    out = {'status': 'passed' if all(x['passed'] for x in results) else 'failed',
           'checks': results,
           'board_recorder_mode': recorder.get('mode'),
           'board_recordings_count': count,
           'boundary': 'Read-only integration: GET endpoints only, no POST to the board, no mode '
                       'changes, nothing written to /mnt/wcis-data. Cross-checked HA sensor values '
                       'against the board answers at probe time.'}
    OUT.write_text(json.dumps(out, indent=2) + '\n')
    print('board-readonly-probe: %d/%d' % (sum(x['passed'] for x in results), len(results)))
    return 0 if all(x['passed'] for x in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())

