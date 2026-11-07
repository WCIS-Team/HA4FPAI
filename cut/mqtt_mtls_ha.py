"""Point HA's mqtt entry at the mTLS broker (client certificate required).

mqtt is single_config_entry, so the existing entry is replaced. The client
certificate/key and CA are uploaded through /api/file_upload because the config
flow's FileSelector fields take a file_id, not a path.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, '/tmp')
from functional_probe import request, check, results

OUT = Path('/tmp/mqtt-mtls-ha.json')
BROKER = '10.88.0.67'
CERT_DIR = Path('/runtime/mtls')


def bearer():
    return json.loads(Path('/runtime/ha-cut-probe-session.json').read_text())['access_token']


def upload(path):
    """Upload a file to HA and return its file_id."""
    from urllib.request import Request, urlopen
    boundary = 'haSmallMqtt7954'
    data = Path(path).read_bytes()
    name = Path(path).name
    body = (('--' + boundary + '\r\n'
             'Content-Disposition: form-data; name="file"; filename="%s"\r\n'
             'Content-Type: application/octet-stream\r\n\r\n' % name).encode()
            + data + ('\r\n--' + boundary + '--\r\n').encode())
    req = Request('http://127.0.0.1:8123/api/file_upload', data=body, method='POST',
                  headers={'Authorization': 'Bearer ' + bearer(),
                           'Content-Type': 'multipart/form-data; boundary=' + boundary})
    with urlopen(req, timeout=30) as resp:
        return json.load(resp)['file_id']


def main():
    token = bearer()
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    for f in ('ca.crt', 'client_ha.crt', 'client_ha.key'):
        (CERT_DIR / f).write_bytes((Path('/tmp/mtls') / f).read_bytes())

    ca_id = upload(CERT_DIR / 'ca.crt')
    cert_id = upload(CERT_DIR / 'client_ha.crt')
    key_id = upload(CERT_DIR / 'client_ha.key')
    check('certs-uploaded', all((ca_id, cert_id, key_id)),
          'ca=%s cert=%s key=%s' % (ca_id, cert_id, key_id))

    status, entries = request('/api/config/config_entries/entry', token=token)
    for e in entries if isinstance(entries, list) else []:
        if e.get('domain') == 'mqtt':
            request('/api/config/config_entries/entry/' + e['entry_id'], token=token,
                    method='DELETE')
    time.sleep(3)

    status, flow = request('/api/config/config_entries/flow', {'handler': 'mqtt'}, token)
    fid = flow.get('flow_id') if isinstance(flow, dict) else None
    check('flow-started', bool(fid), str(flow)[:70])
    if not fid:
        finalize()
        return 1

    payload = {'broker': BROKER, 'port': 8883,
               'other_settings': {
                   'set_client_cert': True,
                   'client_cert': cert_id,
                   'client_key': key_id,
                   'set_ca_cert': 'custom',
                   'certificate': ca_id,
                   'tls_insecure': False,
                   'transport': 'tcp'}}
    status, res = request('/api/config/config_entries/flow/' + fid, payload, token)
    created = isinstance(res, dict) and res.get('type') == 'create_entry'
    check('mtls-entry-created', created, 'type=%s errors=%s' % (
        res.get('type') if isinstance(res, dict) else res,
        res.get('errors') if isinstance(res, dict) else str(res)[:130]))
    if not created:
        finalize()
        return 1

    loaded = False
    detail = ''
    end = time.monotonic() + 60
    while time.monotonic() < end:
        status, entries = request('/api/config/config_entries/entry', token=token)
        rows = [e for e in (entries if isinstance(entries, list) else [])
                if e.get('domain') == 'mqtt']
        detail = str([(e.get('title'), e.get('state'), e.get('reason')) for e in rows])
        if any(e.get('state') == 'loaded' for e in rows):
            loaded = True
            break
        time.sleep(2)
    check('mtls-entry-loaded', loaded, detail)

    # Traffic must actually cross the mTLS link.
    import paho.mqtt.client as mqtt
    import ssl
    got = []
    sub = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id='probe-mtls-sub')
    sub.on_message = lambda c, u, m: got.append(m.payload.decode('utf-8', 'replace'))
    sub.tls_set(ca_certs=str(CERT_DIR / 'ca.crt'),
                certfile=str(CERT_DIR / 'client_ha.crt'),
                keyfile=str(CERT_DIR / 'client_ha.key'),
                cert_reqs=ssl.CERT_REQUIRED)
    sub.connect(BROKER, 8883, 15)
    sub.subscribe('ha-small/mtls/probe', qos=2)
    sub.loop_start()
    time.sleep(1.5)
    request('/api/services/mqtt/publish',
            {'topic': 'ha-small/mtls/probe', 'payload': 'over-mtls', 'qos': 2}, token=token)
    end = time.monotonic() + 20
    while time.monotonic() < end and not got:
        time.sleep(0.4)
    sub.loop_stop()
    sub.disconnect()
    check('mtls-traffic-flows', bool(got), 'received=%s' % got[:2])

    finalize()
    return 0 if all(x['passed'] for x in results) else 1


def finalize():
    out = {'status': 'passed' if all(x['passed'] for x in results) else 'failed',
           'checks': results, 'broker': '%s:8883 (mTLS)' % BROKER,
           'boundary': 'Self-signed CA with per-client certificates; broker requires a client '
                       'certificate (verified: no-cert handshake is rejected with '
                       'TLSV13_ALERT_CERTIFICATE_REQUIRED). Lab broker on the podman bridge, not a '
                       'production broker, and the certificates are lab-grade (30 days, no CRL/OCSP).'}
    OUT.write_text(json.dumps(out, indent=2) + '\n')
    print('mqtt-mtls-ha: %d/%d' % (sum(x['passed'] for x in results), len(results)))


if __name__ == '__main__':
    raise SystemExit(main())
