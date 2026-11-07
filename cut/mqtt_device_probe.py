"""CONNECT-03: MQTT broker + discovered device: state/control/reconnect.

Simulated device = retained discovery + topics driven by mosquitto_pub/sub.
Boundary: loopback broker, no real hardware.
"""
import json
import secrets
import subprocess
import time
from pathlib import Path
import sys
sys.path.insert(0, '/tmp')
from functional_probe import request, check, results

CFG = 'homeassistant/switch/ha_cut_mq/config'
SET = 'ha-cut/mq/set'
STATE = 'ha-cut/mq/state'
AVAIL = 'ha-cut/mq/avail'


def pub(topic, payload, retain=False):
    args = ['mosquitto_pub', '-h', '127.0.0.1', '-t', topic, '-m', payload, '-V', 'mqttv5']
    if retain:
        args.append('-r')
    return subprocess.run(args, capture_output=True, text=True, timeout=15).returncode


def state_of(token, entity):
    st, body = request('/api/states/' + entity, token=token)
    return body.get('state') if st == 200 and isinstance(body, dict) else None


def wait_state(token, entity, want, seconds=25):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if state_of(token, entity) == want:
            return True
        time.sleep(0.5)
    return False


def finish():
    Path('/tmp/mqtt-results.json').write_text(json.dumps({
        'status': 'passed' if all(x['passed'] for x in results) else 'failed',
        'checks': results,
        'boundary': 'Loopback broker with a simulated MQTT device (retained discovery). '
                    'Real hardware, TLS/auth profiles and QoS2 not tested.'}, indent=2) + '\n')
    print('mqtt-device-probe: %d/%d' % (sum(x['passed'] for x in results), len(results)))


def main():
    token = json.loads(Path('/runtime/ha-cut-probe-session.json').read_text())['access_token']
    # Idempotent reset: remove any earlier mqtt config entry from a failed run.
    st, entries = request('/api/config/config_entries/entry', token=token)
    if isinstance(entries, list):
        for entry in entries:
            if entry.get('domain') == 'mqtt':
                request('/api/config/config_entries/entry/' + entry['entry_id'], token=token, method='DELETE')
                time.sleep(2)
    st, flows = request('/api/config/config_entries/flow', {'handler': 'mqtt'}, token=token)
    check('config-flow-start', st == 200)
    st, done = request('/api/config/config_entries/flow/' + flows['flow_id'], {
        'broker': '127.0.0.1', 'port': 1883, 'protocol': '3.1.1',
        'other_settings': {'set_client_cert': False, 'set_ca_cert': 'off'}}, token=token)
    ok = st == 200 and isinstance(done, dict) and done.get('type') == 'create_entry'
    check('config-entry-created', ok, '' if ok else str(done)[:160])
    loaded = False
    end = time.monotonic() + 60
    while time.monotonic() < end:
        st, cfg = request('/api/config', token=token)
        if st == 200 and 'mqtt' in (cfg.get('components') or []):
            loaded = True
            break
        time.sleep(0.5)
    check('mqtt-component-loaded', loaded)

    discovery = json.dumps({'name': 'ha_cut_mq', 'unique_id': 'ha_cut_mq_1',
                            'command_topic': SET, 'state_topic': STATE,
                            'availability_topic': AVAIL, 'payload_on': 'ON',
                            'payload_off': 'OFF'})
    check('discovery-publish', pub(CFG, discovery, retain=True) == 0)
    pub(AVAIL, 'online', retain=True)
    end = time.monotonic() + 30
    appeared = False
    while time.monotonic() < end:
        if state_of(token, 'switch.ha_cut_mq') is not None:
            appeared = True
            break
        time.sleep(0.5)
    check('discovery-entity-created', appeared)
    check('state-topic-updates', pub(STATE, 'ON', retain=True) == 0 and wait_state(token, 'switch.ha_cut_mq', 'on'))
    proc = subprocess.Popen(['mosquitto_sub', '-h', '127.0.0.1', '-t', SET, '-C', '1', '-W', '12', '-V', 'mqttv5'],
                            stdout=subprocess.PIPE, text=True)
    time.sleep(0.8)
    st, _ = request('/api/services/switch/turn_off', {'entity_id': 'switch.ha_cut_mq'}, token=token)
    out, _ = proc.communicate(timeout=20)
    check('control-command-to-device', st == 200 and out.strip() == 'OFF', 'cmd=%r' % out.strip()[:30])
    check('state-follows-device', pub(STATE, 'OFF', retain=True) == 0 and wait_state(token, 'switch.ha_cut_mq', 'off'))

    subprocess.run(['pkill', '-x', 'mosquitto'], capture_output=True)
    time.sleep(1.5)
    restarted = subprocess.run(['/usr/sbin/mosquitto', '-d'], capture_output=True).returncode == 0
    time.sleep(3)
    pub(STATE, 'ON', retain=True)
    pub(AVAIL, 'online', retain=True)
    check('broker-restart-and-resubscribe', restarted and wait_state(token, 'switch.ha_cut_mq', 'on', 30))
    finish()
    return 0 if all(x['passed'] for x in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
