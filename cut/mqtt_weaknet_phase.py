"""One phase of the weak-network measurement (runs inside the HA container).

Phases: baseline | impaired | recovered | entry
Emits one JSON line so the host-side driver can orchestrate netem and collect.
"""
import json
import ssl
import sys
import time
from pathlib import Path
from statistics import mean

import paho.mqtt.client as mqtt
sys.path.insert(0, '/tmp')
from functional_probe import request

BROKER = '10.88.0.67'
PORT = 8883
TOPIC = 'ha-small/weaknet/probe'
CA = '/tmp/mtls/ca.crt'
CERT = '/tmp/mtls/client_ha.crt'
KEY = '/tmp/mtls/client_ha.key'


def bearer():
    return json.loads(Path('/runtime/ha-cut-probe-session.json').read_text())['access_token']


def rtt_samples(count=8):
    got = []
    sub = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='weaknet-sub')
    sub.on_message = lambda c, u, m: got.append(time.monotonic())
    sub.tls_set(ca_certs=CA, certfile=CERT, keyfile=KEY, cert_reqs=ssl.CERT_REQUIRED)
    sub.connect(BROKER, PORT, 20)
    sub.subscribe(TOPIC, qos=1)
    sub.loop_start()
    time.sleep(1.0)

    pub = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='weaknet-pub')
    pub.tls_set(ca_certs=CA, certfile=CERT, keyfile=KEY, cert_reqs=ssl.CERT_REQUIRED)
    pub.connect(BROKER, PORT, 20)
    pub.loop_start()
    time.sleep(0.8)

    samples = []
    for i in range(count):
        sent = time.monotonic()
        try:
            info = pub.publish(TOPIC, 'p%d-%f' % (i, sent), qos=1)
            info.wait_for_publish(6)
        except Exception:  # noqa: BLE001
            continue
        deadline = sent + 8
        while time.monotonic() < deadline:
            if got:
                samples.append((got[-1] - sent) * 1000.0)
                got.clear()
                break
            time.sleep(0.01)
        time.sleep(0.15)

    pub.loop_stop()
    pub.disconnect()
    sub.loop_stop()
    sub.disconnect()
    return samples


def entry_state():
    status, entries = request('/api/config/config_entries/entry', token=bearer())
    for e in entries if isinstance(entries, list) else []:
        if e.get('domain') == 'mqtt':
            return e.get('state')
    return None


def ha_publish(topic, payload):
    status, _ = request('/api/services/mqtt/publish',
                        {'topic': topic, 'payload': payload, 'qos': 1},
                        token=bearer())
    return status


def main():
    phase = sys.argv[1] if len(sys.argv) > 1 else 'baseline'
    if phase == 'entry':
        out = {'phase': 'entry', 'state': entry_state()}
    elif phase == 'publish':
        out = {'phase': 'publish', 'status': ha_publish(TOPIC + '/ha', 'hello')}
    else:
        samples = rtt_samples()
        out = {'phase': phase, 'count': len(samples),
               'avg_ms': round(mean(samples), 1) if samples else None,
               'min_ms': round(min(samples), 1) if samples else None,
               'max_ms': round(max(samples), 1) if samples else None}
    print(json.dumps(out))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())