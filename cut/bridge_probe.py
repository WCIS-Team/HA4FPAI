"""CONNECT-04: HA rest_command -> HA4FPAI control service (/v1/commands) end to end."""
import json
import secrets
import sqlite3
import sys
import time
from pathlib import Path
sys.path.insert(0, '/tmp')
from functional_probe import request, check, results

DB = '/tmp/bridge/business.sqlite3'
ACTION = 'camera.open_view'


def main():
    token = json.loads(Path('/runtime/ha-cut-probe-session.json').read_text())['access_token']
    st, cfg = request('/api/config', token=token)
    if st != 200:
        check('session-valid', False, 'status=%s' % st)
        finalize()
        return 1
    check('session-valid', True)
    end = time.monotonic() + 60
    ready = False
    while time.monotonic() < end:
        st, services = request('/api/services', token=token)
        if st == 200 and any(d.get('domain') == 'rest_command' for d in services):
            ready = any('ha_bridge_probe' in (d.get('services') or {})
                        for d in services if d.get('domain') == 'rest_command')
            if ready:
                break
        time.sleep(1)
    check('bridge-service-registered', ready)
    db = sqlite3.connect(DB)
    before = db.execute('select count(*) from commands').fetchone()[0]
    key = 'ha-probe-' + secrets.token_hex(4)
    st, _ = request('/api/services/rest_command/ha_bridge_probe',
                    {'action': ACTION, 'key': key}, token=token)
    check('rest-command-accepted', st == 200, 'status=%s' % st)
    row = None
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        row = db.execute('select action, client_id, status from commands '
                         'order by rowid desc limit 1').fetchone()
        if row and db.execute('select count(*) from commands').fetchone()[0] > before:
            break
        time.sleep(0.5)
    after = db.execute('select count(*) from commands').fetchone()[0]
    check('command-persisted-in-control-db', after == before + 1 and row is not None,
          'before=%d after=%d row=%s' % (before, after, row))
    check('command-action-matches', bool(row) and row[0] == ACTION and row[1] == 'ha-small',
          'row=%s' % (row,))
    db.close()
    finalize()
    return 0 if all(x['passed'] for x in results) else 1


def finalize():
    result = {'status': 'passed' if all(x['passed'] for x in results) else 'failed',
              'checks': results,
              'boundary': 'Control service ran locally in the lab container with a fresh store; '
                          'no board deployment, no real device actions executed (command row only).'}
    Path('/tmp/bridge-results.json').write_text(json.dumps(result, indent=2) + '\n')
    print('bridge-probe: %d/%d' % (sum(x['passed'] for x in results), len(results)))


if __name__ == '__main__':
    raise SystemExit(main())
