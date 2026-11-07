"""NEXT-03: which pinned dependencies have aarch64 cp314 wheels (inventory only)."""
import json
import subprocess
from pathlib import Path

lock = Path('/src-branch/cut/requirements-linux-x86_64.lock')
pins = []
for line in lock.read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if not line or line.startswith('#'):
        continue
    pins.append(line)

rows = []
for pin in pins:
    name = pin.split('==')[0]
    cmd = ['/venv/bin/pip', 'download', '--no-deps', '--only-binary=:all:',
           '--platform', 'manylinux2014_aarch64', '--python-version', '3.14',
           '--implementation', 'cp', '--abi', 'cp314', '-d', '/tmp/a64', pin]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    rows.append({'pin': pin, 'aarch64_wheel': proc.returncode == 0,
                 'detail': '' if proc.returncode == 0 else (proc.stderr.strip().splitlines() or [''])[-1][:160]})
    print(('OK  ' if rows[-1]['aarch64_wheel'] else 'MISS') + ' ' + pin, flush=True)

ok = [r for r in rows if r['aarch64_wheel']]
missing = [r for r in rows if not r['aarch64_wheel']]
result = {'status': 'inventory-complete', 'total': len(rows), 'with_aarch64_wheel': len(ok),
          'without_aarch64_wheel': len(missing),
          'missing': [r['pin'] for r in missing],
          'boundary': 'Wheel presence only (pip download --platform manylinux2014_aarch64 cp314). '
                      'No ARM64 interpreter was installed and nothing ran on ARM64.'}
Path('/tmp/arm64-results.json').write_text(json.dumps(result, indent=2) + '\n')
print('arm64 inventory: %d/%d have wheels' % (len(ok), len(rows)))
