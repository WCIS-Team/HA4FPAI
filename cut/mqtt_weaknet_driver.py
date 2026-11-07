"""Host-side driver for the MQTT weak-network test.

Runs netem on the broker container (podman is only reachable from the host) and
orchestrates the measurement phases inside the HA container.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

OUT = Path(r'S:\work\WCIS\workenv\ha-small\results\mqtt-weaknet-results.json')
BROKER_C = 'ha-small-mqtt'
HA_C = 'ha-small-dev'
PHASE = '/tmp/mqtt_weaknet_phase.py'


def podman_exec(container, cmd, timeout=180):
    return subprocess.run(['podman', 'exec', container, 'sh', '-c', cmd],
                          capture_output=True, text=True, timeout=timeout)


def phase(name):
    r = podman_exec(HA_C, '/venv/bin/python %s %s' % (PHASE, name), timeout=300)
    for line in r.stdout.strip().splitlines():
        line = line.strip()
        if line.startswith('{'):
            return json.loads(line)
    return {'phase': name, 'error': (r.stdout + r.stderr).strip()[-200:]}


def netem_apply():
    podman_exec(BROKER_C, 'tc qdisc del dev eth0 root 2>/dev/null; true')
    r = podman_exec(BROKER_C, 'tc qdisc add dev eth0 root netem delay 120ms 30ms loss 5%')
    return podman_exec(BROKER_C, 'tc qdisc show dev eth0').stdout.strip(), r


def netem_clear():
    podman_exec(BROKER_C, 'tc qdisc del dev eth0 root 2>/dev/null; true')
    return podman_exec(BROKER_C, 'tc qdisc show dev eth0').stdout.strip()


def main():
    checks = []

    def check(name, ok, detail=''):
        checks.append({'check': name, 'passed': bool(ok), 'detail': detail})
        print(('PASS ' if ok else 'FAIL ') + name + (' ' + detail if detail else ''), flush=True)

    print('clearing shaping...', flush=True)
    netem_clear()

    base = phase('baseline')
    check('baseline-round-trip', (base.get('count') or 0) >= 6,
          'count=%s avg=%sms' % (base.get('count'), base.get('avg_ms')))

    show, _ = netem_apply()
    check('netem-applied', 'netem' in show and 'loss 5%' in show, show[:110])

    impaired = phase('impaired')
    check('round-trip-survives-impairment', (impaired.get('count') or 0) >= 4,
          'count=%s avg=%sms max=%sms' % (impaired.get('count'), impaired.get('avg_ms'),
                                          impaired.get('max_ms')))
    base_avg = base.get('avg_ms') or 0
    imp_avg = impaired.get('avg_ms') or 0
    check('latency-increased', imp_avg > base_avg + 50,
          'base=%sms impaired=%sms (+%sms)' % (base_avg, imp_avg, round(imp_avg - base_avg, 1)))

    entry = phase('entry')
    check('ha-entry-loaded-under-loss', entry.get('state') == 'loaded',
          'state=%s' % entry.get('state'))

    print('restarting broker under shaping...', flush=True)
    podman_exec(BROKER_C, 'pkill mosquitto 2>/dev/null; true')
    time.sleep(4)
    podman_exec(BROKER_C, 'mosquitto -c /etc/mosquitto/mosq-mtls.conf -d')
    time.sleep(2)

    recovered = False
    detail = ''
    end = time.time() + 180
    while time.time() < end:
        st = phase('entry').get('state')
        detail = st
        if st == 'loaded':
            pub = phase('publish')
            if pub.get('status') == 200:
                recovered = True
                break
        time.sleep(5)
    check('ha-reconnected-after-broker-restart', recovered, 'state=%s' % detail)

    show = netem_clear()
    check('netem-cleared', 'netem' not in show, show[:80])

    after = phase('recovered')
    check('recovered-to-baseline', (after.get('count') or 0) >= 6,
          'count=%s avg=%sms' % (after.get('count'), after.get('avg_ms')))

    result = {
        'status': 'passed' if all(c['passed'] for c in checks) else 'failed',
        'checks': checks,
        'measurements': {'baseline': base, 'impaired': impaired, 'recovered': after},
        'netem': 'delay 120ms 30ms loss 5% on the broker container egress (eth0)',
        'boundary': 'Only the broker->client direction is shaped, because the running HA '
                    'container cannot gain NET_ADMIN without being recreated. Loss is random, so '
                    'sample counts vary. No WAN congestion, no MTU/duplication/duplication-reorder '
                    'modelling, no long-run (hours) soak.',
    }
    OUT.write_text(json.dumps(result, indent=2) + '\n')
    print('\nresult: %s (base=%sms impaired=%sms recovered=%sms)'
          % (result['status'], base.get('avg_ms'), impaired.get('avg_ms'), after.get('avg_ms')))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())