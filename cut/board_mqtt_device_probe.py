"""Verify HA sees the board adapter as a real MQTT device (discovery + registry + LWT)."""
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

import aiohttp
sys.path.insert(0, '/tmp')
from functional_probe import request, check, results

BASE = 'http://127.0.0.1:8123'
OUT = Path('/tmp/board-mqtt-device.json')
EXPECTED = [
    'sensor.wcis_board_113_capture_state',
    'sensor.wcis_board_113_captured_frames',
    'sensor.wcis_board_113_frame_age',
    'sensor.wcis_board_113_recorder_mode',
    'sensor.wcis_board_113_recorder_source_ready',
    'sensor.wcis_board_113_recorder_state',
    'sensor.wcis_board_113_recordings',
    'event.wcis_board_113_new_recording',
]


def bearer():
    return json.loads(Path('/runtime/ha-cut-probe-session.json').read_text())['access_token']


def state_of(entity):
    status, body = request('/api/states/' + entity, token=bearer())
    return body if status == 200 and isinstance(body, dict) else None


async def ws_call(payload):
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(BASE + '/api/websocket', timeout=30) as ws:
            await ws.receive_json(timeout=20)
            await ws.send_json({'type': 'auth', 'access_token': bearer()})
            await ws.receive_json(timeout=20)
            await ws.send_json(dict(payload, id=1))
            while True:
                r = await ws.receive_json(timeout=30)
                if r.get('id') == 1:
                    return r


def adapter_pids():
    out = subprocess.run(['sh', '-c',
                          'for p in /proc/[0-9]*; do tr "\\0" " " < $p/cmdline 2>/dev/null | grep -q board_mqtt_adapter && echo ${p#/proc/}; done'],
                         capture_output=True, text=True).stdout.split()
    return [p for p in out if p.isdigit()]


def main():
    deadline = time.time() + 45
    found = {}
    while time.time() < deadline:
        status, states = request('/api/states', token=bearer())
        if isinstance(states, list):
            ids = {s['entity_id'] for s in states}
            found = {e: (e in ids) for e in EXPECTED}
            if all(found.values()):
                break
        time.sleep(2)
    check('discovery-created-entities', all(found.values()),
          'missing=%s' % [k for k, v in found.items() if not v])

    dev = asyncio.run(ws_call({'type': 'config/device_registry/list'}))
    devices = [d for d in (dev.get('result') or [])
               if any('wcis-board-113' in (idf if isinstance(idf, str) else ' '.join(map(str, idf if isinstance(idf, (list, tuple)) else [idf]))) for idf in (d.get('identifiers') or []))]
    check('device-registry-entry', len(devices) == 1,
          'name=%s' % (devices[0].get('name') if devices else None))
    if devices:
        check('device-metadata',
              devices[0].get('manufacturer') == 'WCIS'
              and (devices[0].get('sw_version') or '').startswith('vision-'),
              'manufacturer=%s sw=%s' % (devices[0].get('manufacturer'),
                                          devices[0].get('sw_version')))

    with urlopen('http://192.168.1.113:8093/api/status', timeout=10) as r:
        recorder = json.load(r)
    with urlopen('http://192.168.1.113:8092/api/status', timeout=10) as r:
        stream = json.load(r)
    with urlopen('http://192.168.1.113:8093/api/recordings', timeout=10) as r:
        library = json.load(r)

    mode = state_of('sensor.wcis_board_113_recorder_mode')
    check('value-recorder-mode', bool(mode) and mode.get('state') == recorder.get('mode'),
          'ha=%s board=%s' % (mode and mode.get('state'), recorder.get('mode')))

    captured = state_of('sensor.wcis_board_113_captured_frames')
    try:
        near = abs(int(float(captured.get('state'))) - stream.get('captured', 0)) < 1500
    except (TypeError, ValueError, AttributeError):
        near = False
    check('value-captured-frames', near,
          'ha=%s board=%s' % (captured and captured.get('state'), stream.get('captured')))

    recs = state_of('sensor.wcis_board_113_recordings')
    try:
        rec_ok = int(float(recs.get('state'))) == len(library.get('recordings') or [])
    except (TypeError, ValueError, AttributeError):
        rec_ok = False
    check('value-recordings', rec_ok,
          'ha=%s board=%s' % (recs and recs.get('state'), len(library.get('recordings') or [])))

    avail_entity = state_of('sensor.wcis_board_113_capture_state')
    check('entity-available', bool(avail_entity)
          and avail_entity.get('state') not in ('unavailable', 'unknown'),
          'state=%s' % (avail_entity and avail_entity.get('state')))

    # LWT: kill the adapter without a clean shutdown; the broker must publish "offline".
    pids = adapter_pids()
    check('adapter-running', bool(pids), 'pids=%s' % pids)
    for pid in pids:
        subprocess.run(['kill', '-9', pid])
    went_unavailable = False
    detail = ''
    end = time.time() + 90
    while time.time() < end:
        st = state_of('sensor.wcis_board_113_capture_state')
        detail = st and st.get('state')
        if detail in ('unavailable', None):
            went_unavailable = True
            break
        time.sleep(3)
    check('lwt-marks-unavailable', went_unavailable, 'state=%s' % detail)

    out = {'status': 'passed' if all(x['passed'] for x in results) else 'failed',
           'checks': results,
           'entities': EXPECTED,
           'device': devices[0] if devices else None,
           'board_recorder_mode': recorder.get('mode'),
           'boundary': 'The adapter is an external read-only bridge: it polls the board GET '
                       'endpoints and republishes them over MQTT with its own client certificate. '
                       'No board service was modified. Discovery, device registry and LWT are '
                       'exercised against the board\'s real values.'}
    OUT.write_text(json.dumps(out, indent=2) + '\n')
    print('board-mqtt-device: %d/%d' % (sum(x['passed'] for x in results), len(results)))
    return 0 if all(x['passed'] for x in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
