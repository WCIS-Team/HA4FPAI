import json
from pathlib import Path
import sqlite3
import time
from functional_probe import request

runtime = Path('/runtime')
assert (runtime / 'HA_SMALL_TEST_ONLY').is_file(), 'test-only runtime required'
token = json.loads((runtime / 'ha-cut-probe-session.json').read_text())['access_token']
end = time.monotonic() + 120
while True:
    try:
        status, state = request('/api/states/input_text.cut_note', token=token)
        if status == 200:
            break
    except OSError:
        pass
    if time.monotonic() > end:
        raise RuntimeError('Restart readiness timeout')
    time.sleep(0.5)
checks = []
def check(name, ok):
    checks.append({'feature':name,'passed':bool(ok)})
    print(('PASS ' if ok else 'FAIL ') + name, flush=True)
check('auth-survives-restart', request('/api/', token=token)[0] == 200)
check('helper-restored-after-restart', state.get('state') == 'ha-small-persist')
db = sqlite3.connect('file:/runtime/home-assistant_v2.db?mode=ro', uri=True)
count = db.execute('SELECT count(*) FROM states').fetchone()[0]
check('history-survives-restart', count > 0)
check('sqlite-integrity-after-restart', db.execute('PRAGMA quick_check').fetchone()[0] == 'ok')
db.close()
result={'status':'passed' if all(x['passed'] for x in checks) else 'failed',
        'checks':checks,'passed':sum(x['passed'] for x in checks),'total':len(checks),
        'stored_state_rows':count,'boundary':'Graceful process restart, not board power-cut testing.'}
Path('/tmp/persistence-results.json').write_text(json.dumps(result,indent=2)+'\n')
raise SystemExit(0 if result['status']=='passed' else 1)
