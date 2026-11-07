"""NEXT-03c: start ha-small natively on aarch64 (emulated) and exercise the API."""
import json
import subprocess
import time
from pathlib import Path
from urllib.request import Request, urlopen

BASE = 'http://127.0.0.1:8123'
OUT = Path('/tmp/arm64-runtime.json')
CFG = '/runtime'


def main():
    subprocess.run(['sh', '-c', 'pkill -f "/venv/bin/hass" || true; sleep 2'])
    subprocess.Popen(['sh', '-c', 'exec /venv/bin/hass -c %s > /tmp/ha-arm.log 2>&1' % CFG])
    checks = []

    def check(name, ok, detail=''):
        checks.append({'check': name, 'passed': bool(ok), 'detail': detail})
        print(('PASS ' if ok else 'FAIL ') + name + (' ' + detail if detail else ''), flush=True)

    up = False
    deadline = time.time() + 600
    while time.time() < deadline:
        try:
            with urlopen(BASE + '/', timeout=8) as r:
                if r.status == 200:
                    up = True
                    break
        except Exception:  # noqa: BLE001
            time.sleep(3)
    check('frontend-http-on-aarch64', up)

    try:
        urlopen(BASE + '/api/', timeout=10)
        unauth = False
        detail = 'api open?'
    except Exception as exc:  # noqa: BLE001
        unauth = '401' in str(exc)
        detail = str(exc)[:60]
    check('api-requires-auth', unauth, detail)

    log = Path('/tmp/ha-arm.log')
    text = log.read_text(errors='replace') if log.exists() else ''
    import_errors = [ln for ln in text.splitlines() if 'ImportError' in ln]
    setup_failed = [ln for ln in text.splitlines() if 'Setup failed' in ln]
    check('no-import-errors', not import_errors, str(import_errors[:2])[:150])
    check('no-setup-failed', not setup_failed, str(setup_failed[:2])[:150])

    arch = subprocess.run(['sh', '-c', 'uname -m'], capture_output=True, text=True).stdout.strip()
    pyver = subprocess.run(['sh', '-c', '/venv/bin/python -c "import sys;print(sys.version.split()[0])"'],
                           capture_output=True, text=True).stdout.strip()
    sqlite = subprocess.run(['sh', '-c',
                             '/venv/bin/python -c "import sqlite3;print(sqlite3.sqlite_version)"'],
                            capture_output=True, text=True).stdout.strip()
    result = {'checks': checks, 'passed': all(c['passed'] for c in checks),
              'arch': arch, 'python': pyver, 'sqlite': sqlite,
              'boundary': 'aarch64 under qemu user emulation on an x86 host (podman binfmt), NOT '
                          'the target board. Proves the trimmed tree installs and starts on a real '
                          'aarch64 Python 3.14 runtime; no board resource/stability claims.'}
    OUT.write_text(json.dumps(result, indent=2) + '\n')
    print('arch=%s python=%s sqlite=%s passed=%s' % (arch, pyver, sqlite, result['passed']))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())