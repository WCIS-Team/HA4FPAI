import asyncio
import json
import secrets
import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = 'http://127.0.0.1:8123'
RUNTIME = Path('/runtime')
results = []
received = []

def check(name, condition, detail=''):
    row = {'feature': name, 'passed': bool(condition), 'detail': detail}
    results.append(row)
    print(('PASS ' if condition else 'FAIL ') + name + (' ' + detail if detail else ''), flush=True)

def request(path, data=None, token=None, method=None, form=False):
    headers = {}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    body = None
    if data is not None:
        body = urlencode(data).encode() if form else json.dumps(data).encode()
        headers['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
    req = Request(BASE + path, data=body, headers=headers, method=method)
    try:
        with urlopen(req, timeout=20) as r:
            raw = r.read()
            try:
                obj = json.loads(raw)
            except ValueError:
                obj = raw.decode(errors='replace')
            return r.status, obj
    except HTTPError as e:
        return e.code, e.read().decode(errors='replace')[:300]

def wait_state(entity, expected, token, seconds=12):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        st, obj = request('/api/states/' + entity, token=token)
        if st == 200 and obj.get('state') == expected:
            return True
        time.sleep(0.25)
    return False

class Echo(BaseHTTPRequestHandler):
    def do_POST(self):
        received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
        payload = b'{"ok":true}'
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
    def log_message(self, *args):
        pass

async def ws_probe(token):
    import aiohttp
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(BASE + '/api/websocket', timeout=15) as ws:
            first = await ws.receive_json(timeout=15)
            await ws.send_json({'type': 'auth', 'access_token': token})
            auth = await ws.receive_json(timeout=15)
            check('websocket-auth', first['type'] == 'auth_required' and auth['type'] == 'auth_ok')
            await ws.send_json({'id': 1, 'type': 'subscribe_events', 'event_type': 'ha_cut_probe'})
            sub = await ws.receive_json(timeout=15)
            check('event-subscription', sub.get('success') is True)
            nonce = secrets.token_hex(8)
            await asyncio.to_thread(request, '/api/events/ha_cut_probe', {'nonce': nonce}, token)
            event = await ws.receive_json(timeout=15)
            check('event-bus-delivery', event.get('event', {}).get('data', {}).get('nonce') == nonce)
            await ws.send_json({'id': 2, 'type': 'config/entity_registry/list'})
            while True:
                registry = await ws.receive_json(timeout=15)
                if registry.get('id') == 2:
                    break
            check('entity-registry', registry.get('success') is True and len(registry.get('result', [])) > 0)
            await ws.send_json({'id': 3, 'type': 'config/area_registry/list'})
            while True:
                areas = await ws.receive_json(timeout=15)
                if areas.get('id') == 3:
                    break
            check('area-registry-api', areas.get('success') is True)
            await ws.send_json({'id': 4, 'type': 'homeassistant/expose_entity',
                                'assistants': ['conversation'],
                                'entity_ids': ['input_boolean.cut_test'], 'should_expose': True})
            while True:
                exposure = await ws.receive_json(timeout=15)
                if exposure.get('id') == 4:
                    break
            check('explicit-assist-exposure', exposure.get('success') is True)

def main():
    end = time.monotonic() + 180
    while True:
        try:
            st, front = request('/')
            if st == 200:
                break
        except OSError:
            pass
        if time.monotonic() > end:
            raise RuntimeError('HA HTTP startup timeout')
        time.sleep(1)
    check('frontend-http', st == 200 and isinstance(front, str) and '<html' in front.lower())
    check('unauthenticated-api-denied', request('/api/')[0] == 401)
    session_file = RUNTIME / 'ha-cut-probe-session.json'
    if session_file.exists():
        token = json.loads(session_file.read_text())['access_token']
    else:
        password = secrets.token_urlsafe(30)
        st, response = request('/api/onboarding/users', {
            'name': 'HA Small Test', 'username': 'cut_admin', 'password': password,
            'client_id': 'http://127.0.0.1/', 'language': 'en'})
        if st != 200:
            raise RuntimeError('Onboarding failed: ' + str(st) + ' ' + str(response))
        st, auth = request('/auth/token', {'grant_type': 'authorization_code',
                           'code': response['auth_code'], 'client_id': 'http://127.0.0.1/'}, form=True)
        if st != 200:
            raise RuntimeError('Token exchange failed: ' + str(st))
        token = auth['access_token']
        session_file.write_text(json.dumps({'access_token': token, 'refresh_token': auth['refresh_token']}))
        session_file.chmod(0o600)
    check('authenticated-rest-api', request('/api/', token=token)[0] == 200)
    # HTTP can become available before integration setup completes. Wait for
    # the explicit feature set instead of treating the first 200 as readiness.
    required = {'automation','script','scene','input_boolean','timer','counter',
                'template','conversation','assist_pipeline','tts','stt',
                'history','recorder','rest_command'}
    ready_until = time.monotonic() + 120
    while True:
        st, cfg = request('/api/config', token=token)
        components = cfg.get('components', []) if isinstance(cfg, dict) else []
        if required.issubset(set(components)) or time.monotonic() > ready_until:
            break
        time.sleep(0.5)
    check('not-recovery-mode', st == 200 and cfg.get('safe_mode') is not True
          and 'recovery_mode' not in components and required.issubset(set(components)))
    check('default-bundle-not-enabled', 'default_config' not in components)
    import importlib.util
    check('unrelated-vendor-deleted', importlib.util.find_spec('homeassistant.components.tuya') is None)
    st, services = request('/api/services', token=token)
    domains = {x['domain'] for x in services} if st == 200 else set()
    check('service-registry', {'script','scene','automation','timer','counter','input_boolean'}.issubset(domains))
    request('/api/services/input_boolean/turn_off', {'entity_id':'input_boolean.cut_test'}, token)
    check('state-read-and-service-write', wait_state('input_boolean.cut_test', 'off', token))
    request('/api/services/counter/reset', {'entity_id':'counter.cut_counter'}, token)
    time.sleep(0.5)
    request('/api/services/script/cut_turn_on', {}, token)
    check('script-execution', wait_state('input_boolean.cut_test', 'on', token))
    check('automation-state-trigger', wait_state('counter.cut_counter', '1', token))
    check('template-reactivity', wait_state('sensor.cut_mirror', 'on', token))
    request('/api/services/scene/turn_on', {'entity_id':'scene.cut_off'}, token)
    check('scene-execution', wait_state('input_boolean.cut_test', 'off', token))
    request('/api/services/input_number/set_value', {'entity_id':'input_number.cut_level','value':42}, token)
    check('numeric-helper', wait_state('input_number.cut_level', '42.0', token))
    request('/api/services/input_text/set_value', {'entity_id':'input_text.cut_note','value':'ha-small-persist'}, token)
    check('text-helper', wait_state('input_text.cut_note', 'ha-small-persist', token))
    request('/api/services/timer/start', {'entity_id':'timer.cut_timer'}, token)
    check('timer-start', wait_state('timer.cut_timer','active',token))
    check('timer-completion', wait_state('timer.cut_timer','idle',token))
    check('person-entity', request('/api/states/person.cut_person', token=token)[0] == 200)
    check('zone-entity', request('/api/states/zone.cut_zone', token=token)[0] == 200)
    asyncio.run(ws_probe(token))
    echo = ThreadingHTTPServer(('127.0.0.1',18094), Echo)
    threading.Thread(target=echo.serve_forever, daemon=True).start()
    try:
        request('/api/services/rest_command/cut_echo', {'value':'probe'}, token)
        check('external-http-control', received == [{'source':'ha-small','value':'probe'}])
    finally:
        echo.shutdown()
        echo.server_close()
    since = (datetime.now(timezone.utc)-timedelta(minutes=10)).isoformat()
    st, hist = request('/api/history/period/' + since + '?filter_entity_id=input_boolean.cut_test', token=token)
    check('sqlite-history-query', st == 200 and isinstance(hist,list) and bool(hist) and len(hist[0]) >= 2)
    voice = {'conversation','assist_pipeline','stt','tts'}
    check('voice-framework-loaded', voice.issubset(set(components)), 'missing=' + ','.join(sorted(voice - set(components))))
    st, speech = request('/api/conversation/process', {'text':'turn on Cut Test','language':'en'}, token)
    check('conversation-request', st == 200 and isinstance(speech,dict) and 'response' in speech)
    check('conversation-controls-entity', st == 200
          and speech.get('response',{}).get('response_type') == 'action_done'
          and wait_state('input_boolean.cut_test','on',token))
    db = sqlite3.connect('file:/runtime/home-assistant_v2.db?mode=ro', uri=True)
    integrity = db.execute('PRAGMA quick_check').fetchone()[0]
    check('sqlite-integrity', integrity == 'ok')
    db.close()
    result = {'status':'passed' if all(x['passed'] for x in results) else 'failed',
              'checks':results, 'passed':sum(x['passed'] for x in results), 'total':len(results),
              'upstream_version':'2026.10.4','python':sys.version.split()[0],
              'boundary':'No real microphone, Wyoming device, MQTT broker, camera, or board deployment tested.'}
    Path('/tmp/functional-results.json').write_text(json.dumps(result,indent=2)+'\n')
    print('functional: %d/%d' % (result['passed'],result['total']))
    return 0 if result['status']=='passed' else 1

if __name__=='__main__':
    if not (RUNTIME / 'HA_SMALL_TEST_ONLY').is_file():
        raise SystemExit('Refusing to modify an unmarked HA instance: test-only runtime required')
    raise SystemExit(main())
