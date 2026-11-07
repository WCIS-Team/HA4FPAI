"""Re-establish an authenticated session for the marked lab runtime.

The lab runtime is operator-owned and marked HA_SMALL_TEST_ONLY. This script
only re-creates the throwaway probe user when the previous session file was
removed; it never touches production HA instances.
"""
import json
import secrets
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urlencode

sys.path.insert(0, '/tmp')
from functional_probe import request  # noqa: E402

runtime = Path('/runtime')
assert (runtime / 'HA_SMALL_TEST_ONLY').is_file(), 'test-only runtime required'
session_file = runtime / 'ha-cut-probe-session.json'
if session_file.exists():
    print('session already present')
    raise SystemExit(0)
password = secrets.token_urlsafe(30)
status, response = request('/api/onboarding/users', {
    'name': 'HA Small Test', 'username': 'cut_admin', 'password': password,
    'client_id': 'http://127.0.0.1/', 'language': 'en'})
if status != 200:
    raise RuntimeError('Onboarding failed: %s %s' % (status, response))
status, auth = request('/auth/token', {'grant_type': 'authorization_code',
                                       'code': response['auth_code'],
                                       'client_id': 'http://127.0.0.1/'}, form=True)
if status != 200:
    raise RuntimeError('Token exchange failed: %s' % status)
session_file.write_text(json.dumps({'access_token': auth['access_token'],
                                    'refresh_token': auth['refresh_token']}))
session_file.chmod(0o600)
end = time.monotonic() + 60
while time.monotonic() < end:
    status, body = request('/api/config', token=auth['access_token'])
    if status == 200 and isinstance(body, dict):
        print('ready, components=%d' % len(body.get('components', [])))
        raise SystemExit(0)
    time.sleep(0.5)
raise RuntimeError('readiness timeout with fresh token')
