"""Run the 30-check functional suite against the deb-installed instance."""
import json
import subprocess
import sys
from pathlib import Path

BASE = 'http://127.0.0.1:8123'
CFG = '/etc/ha-small'
VENV = '/opt/ha-small/venv'
OUT = Path('/tmp/deb-functional.json')


def sh(cmd, timeout=1800):
    return subprocess.run(['sh', '-c', cmd], capture_output=True, text=True, timeout=timeout)


def main():
    src = Path('/tmp/functional_probe.py').read_text(encoding='utf-8')
    Path('/tmp/fp_deb.py').write_text(src.replace('/runtime', CFG), encoding='utf-8')

    onboard = sh((VENV + '/bin/python - <<PY\n'
                  'import json,urllib.request,urllib.parse\n'
                  'from pathlib import Path\n'
                  'base="http://127.0.0.1:8123"\n'
                  'def post(p, d):\n'
                  '    r=urllib.request.Request(base+p, data=json.dumps(d).encode(), method="POST",'
                  'headers={"Content-Type":"application/json"})\n'
                  '    with urllib.request.urlopen(r, timeout=30) as x: return json.load(x)\n'
                  'try:\n'
                  '    resp=post("/api/onboarding/users", {"name":"Deb","username":"deb_admin",'
                  '"password":"deb-pass-12345","client_id":"http://127.0.0.1/","language":"en"})\n'
                  '    body=urllib.parse.urlencode({"grant_type":"authorization_code",'
                  '"code":resp["auth_code"],"client_id":"http://127.0.0.1/"}).encode()\n'
                  '    r=urllib.request.Request(base+"/auth/token", data=body, method="POST",'
                  'headers={"Content-Type":"application/x-www-form-urlencoded"})\n'
                  '    with urllib.request.urlopen(r, timeout=30) as x: a=json.load(x)\n'
                  '    Path("' + CFG + '/ha-cut-probe-session.json").write_text('
                  'json.dumps({"access_token":a["access_token"],'
                  '"refresh_token":a["refresh_token"]}))\n'
                  '    print("onboarded")\n'
                  'except Exception as e:\n'
                  '    print("onboard-skip", str(e)[:60])\n'
                  'PY'))
    print('onboard:', onboard.stdout.strip()[:60], flush=True)

    run = sh(VENV + '/bin/python /tmp/fp_deb.py')
    lines = (run.stdout + run.stderr).strip().splitlines()
    Path('/tmp/deb-fp.log').write_text(run.stdout + run.stderr, encoding='utf-8')
    print('\n'.join(lines[-5:]))
    result = {'rc': run.returncode,
              'summary': lines[-1] if lines else '',
              'failures': [ln for ln in lines if ln.startswith('FAIL')],
              'boundary': 'Runs against the deb-installed instance (/opt/ha-small/venv + '
                          '/etc/ha-small), not the source-tree venv.'}
    OUT.write_text(json.dumps(result, indent=2) + '\n')
    return 0 if run.returncode == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
