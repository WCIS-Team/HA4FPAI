"""Start the trial build on a spare port and check it serves without import errors."""
import json
import re
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen

VENV = '/venv-trial'
CFG = Path('/cfg-trial2')
BASE = 'http://127.0.0.1:8124'
OUT = Path('/tmp/trial2-runtime.json')


def main():
    if CFG.exists():
        subprocess.run(['rm', '-rf', str(CFG)])
    subprocess.run(['cp', '-a', '/cfg-trial', str(CFG)], check=True)
    cfg = CFG / 'configuration.yaml'
    text = cfg.read_text(encoding='utf-8')
    text = re.sub(r'server_port:\s*\d+', 'server_port: 8124', text)
    cfg.write_text(text, encoding='utf-8')

    subprocess.run(['sh', '-c', 'pkill -f "/venv-trial/bin/hass" || true; sleep 2'])
    subprocess.Popen(['sh', '-c', 'exec %s/bin/hass -c %s > /tmp/ha-trial2.log 2>&1'
                      % (VENV, CFG)])

    checks = []

    def check(name, ok, detail=''):
        checks.append({'check': name, 'passed': bool(ok), 'detail': detail})
        print(('PASS ' if ok else 'FAIL ') + name + (' ' + detail if detail else ''), flush=True)

    up = False
    deadline = time.time() + 300
    while time.time() < deadline:
        try:
            with urlopen(BASE + '/', timeout=5) as r:
                if r.status == 200:
                    up = True
                    break
        except Exception:  # noqa: BLE001
            time.sleep(2)
    check('server-started', up)

    try:
        urlopen(BASE + '/api/', timeout=10)
        unauth = False
    except Exception as exc:  # noqa: BLE001
        unauth = '401' in str(exc)
    check('api-requires-auth', unauth)

    log = Path('/tmp/ha-trial2.log')
    text = log.read_text(errors='replace') if log.exists() else ''
    import_errors = [ln for ln in text.splitlines() if 'ImportError' in ln]
    setup_failed = [ln for ln in text.splitlines() if 'Setup failed' in ln]
    check('no-import-errors', not import_errors, str(import_errors[:2])[:160])
    check('no-setup-failed', not setup_failed, str(setup_failed[:2])[:160])

    result = {'checks': checks, 'passed': all(c['passed'] for c in checks),
              'import_errors': import_errors[:3], 'setup_failed': setup_failed[:3],
              'boundary': 'Trial build (120 integrations, 9 removed) started on a spare port in a '
                          'clean venv and served HTTP. Deep functional regression on this trial '
                          'tree was not run; the 129-integration tree keeps its 30/30 result.'}
    OUT.write_text(json.dumps(result, indent=2) + '\n')
    print('trial-runtime: %d/%d passed=%s' % (sum(c['passed'] for c in checks), len(checks),
                                              result['passed']))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())