"""NEXT-03b: cross-build the 5 source-only pins to linux_aarch64 wheels."""
import json
import subprocess
from pathlib import Path

PINS = ['pymicro_vad==1.0.1', 'PyQRCode==1.2.1', 'PyRIC==0.1.6.3',
        'pyspeex_noise==1.0.2', 'PyTurboJPEG==1.8.3']
OUT = '/tmp/a64-w3'
ENV = ('export CC_aarch64_linux_gnu=aarch64-linux-gnu-gcc '
       'CXX_aarch64_linux_gnu=aarch64-linux-gnu-g++ '
       '_PYTHON_HOST_PLATFORM=linux_aarch64 '
       'AR_aarch64_linux_gnu=aarch64-linux-gnu-ar '
       'LDSHARED="aarch64-linux-gnu-gcc -shared"; ')

rows = []
for pin in PINS:
    cmd = ('cd /tmp && ' + ENV +
           '/venv/bin/pip wheel --no-deps --no-binary :all: --no-cache-dir -w %s "%s" 2>&1 | tail -4'
           % (OUT, pin))
    proc = subprocess.run(['sh', '-c', cmd], capture_output=True, text=True, timeout=900)
    name = pin.split('==')[0].replace('-', '_').lower()
    wheels = [p.name for p in Path(OUT).glob('*.whl') if name.split('_')[0] in p.name.lower()]
    ok = proc.returncode == 0 and any('aarch64' in w for w in wheels)
    rows.append({'pin': pin, 'built': bool(ok),
                 'wheel': next((w for w in wheels if 'aarch64' in w), None),
                 'log_tail': proc.stdout.strip().splitlines()[-3:] if not ok else []})
    print(('OK   ' if ok else 'FAIL ') + pin + (' -> ' + (rows[-1]['wheel'] or '')), flush=True)

built = [r for r in rows if r['built']]
result = {'total': len(rows), 'built': len(built),
          'failed': [r['pin'] for r in rows if not r['built']],
          'wheels': [r['wheel'] for r in built],
          'boundary': 'Cross-built aarch64 wheels only; not installed or executed on ARM64 hardware.'}
Path('/tmp/arm64-wheels-results.json').write_text(json.dumps(result, indent=2) + '\n')
print('cross-build: %d/%d' % (len(built), len(rows)))
