"""Run the full 30-check functional probe against the trial build on port 8124."""
import json
import re
import subprocess
from pathlib import Path

SRC = Path('/tmp/functional_probe.py')
DST = Path('/tmp/fp_trial.py')
CFG = Path('/cfg-trial2')

text = SRC.read_text(encoding='utf-8')
text = text.replace("http://127.0.0.1:8123", "http://127.0.0.1:8124")
text = text.replace("/runtime", str(CFG))
DST.write_text(text, encoding='utf-8')
print('probe rewritten for 8124')

subprocess.run(['sh', '-c', 'rm -f %s/ha-cut-probe-session.json' % CFG])

# onboard the trial instance
onboard = subprocess.run(['sh', '-c', (
    '/venv-trial/bin/python - <<PY\n'
    'import json,urllib.request,urllib.parse\n'
    'from pathlib import Path\n'
    'base="http://127.0.0.1:8124"\n'
    'def call(path, data=None):\n'
    '    body=json.dumps(data).encode() if data is not None else None\n'
    '    req=urllib.request.Request(base+path,data=body,method="POST" if data is not None else "GET",'
    'headers={"Content-Type":"application/json"})\n'
    '    with urllib.request.urlopen(req,timeout=30) as r: return json.load(r)\n'
    'resp=call("/api/onboarding/users",{"name":"Trial","username":"trial_admin",'
    '"password":"trial-pass-123","client_id":"http://127.0.0.1/","language":"en"})\n'
    'body=urllib.parse.urlencode({"grant_type":"authorization_code","code":resp["auth_code"],'
    '"client_id":"http://127.0.0.1/"}).encode()\n'
    'req=urllib.request.Request(base+"/auth/token",data=body,method="POST",'
    'headers={"Content-Type":"application/x-www-form-urlencoded"})\n'
    'with urllib.request.urlopen(req,timeout=30) as r: auth=json.load(r)\n'
    'p=Path("%s/ha-cut-probe-session.json")\n'
    'p.write_text(json.dumps({"access_token":auth["access_token"],"refresh_token":auth["refresh_token"]}))\n'
    'print("onboarded")\n'
    'PY') % str(CFG)], capture_output=True, text=True, timeout=300)
print('onboard rc=%s out=%s' % (onboard.returncode, onboard.stdout.strip()[:80]))
if onboard.returncode != 0:
    print(onboard.stderr[-400:])
    raise SystemExit(1)

run = subprocess.run(['/venv-trial/bin/python', str(DST)], capture_output=True, text=True,
                     timeout=1800)
lines = run.stdout.strip().splitlines()
print('\n'.join(lines[-6:]))
Path('/tmp/trial2-functional.log').write_text(run.stdout + run.stderr, encoding='utf-8')
result = {'rc': run.returncode,
          'summary': lines[-1] if lines else '',
          'failures': [ln for ln in lines if ln.startswith('FAIL')]}
Path('/tmp/trial2-functional.json').write_text(json.dumps(result, indent=2) + '\n')
raise SystemExit(0 if run.returncode == 0 else 1)