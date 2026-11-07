"""NEXT-03a v2: re-check aarch64 wheel availability with realistic platform/ABI sets.

The first pass used a single platform tag (manylinux2014_aarch64) and cp314 only,
which under-reports: many projects ship manylinux_2_17/2_28_aarch64 wheels and
abi3 builds. This pass tries each tag combination pip actually resolves.
"""
import json
import subprocess
from pathlib import Path

LOCK = Path('/src-branch/cut/requirements-linux-x86_64.lock')
COMBOS = [
    ('manylinux_2_28_aarch64', 'cp314'),
    ('manylinux_2_17_aarch64', 'cp314'),
    ('manylinux2014_aarch64', 'cp314'),
    ('manylinux_2_28_aarch64', 'abi3'),
    ('manylinux_2_17_aarch64', 'abi3'),
    ('manylinux2014_aarch64', 'abi3'),
]

pins = [ln.strip() for ln in LOCK.read_text(encoding='utf-8').splitlines()
        if ln.strip() and not ln.strip().startswith('#')]

rows = []
for pin in pins:
    found = None
    detail = ''
    for platform, abi in COMBOS:
        cmd = ['/venv/bin/pip', 'download', '--no-deps', '--only-binary=:all:',
               '--platform', platform, '--python-version', '3.14',
               '--implementation', 'cp', '--abi', abi, '-d', '/tmp/a64v2', pin]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
        if proc.returncode == 0:
            found = '%s/%s' % (platform, abi)
            break
        detail = (proc.stderr.strip().splitlines() or [''])[-1][:150]
    rows.append({'pin': pin, 'wheel': found is not None, 'matched_tag': found, 'detail': detail})
    print(('OK   ' if found else 'MISS ') + pin + ('  [' + found + ']' if found else ''), flush=True)

missing = [r['pin'] for r in rows if not r['wheel']]
result = {'total': len(rows), 'with_wheel': len(rows) - len(missing), 'missing': missing,
          'matched_tags': {r['pin']: r['matched_tag'] for r in rows if r['wheel']},
          'boundary': 'Wheel availability only; no ARM64 interpreter was run in this check.'}
Path('/tmp/arm64-results-v2.json').write_text(json.dumps(result, indent=2) + '\n')
print('arm64 v2: %d/%d have wheels; missing=%s' % (len(rows) - len(missing), len(rows), missing))
