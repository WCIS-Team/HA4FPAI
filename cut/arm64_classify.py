"""NEXT-03b verdict: classify the 5 source-only pins by what they actually need.

pure-python -> py3-none-any wheel works on aarch64 as-is
native      -> needs a cross-built linux_aarch64 wheel
"""
import json
import zipfile
from pathlib import Path

OUT = Path('/tmp/a64-w3')
PINS = ['pymicro_vad==1.0.1', 'PyQRCode==1.2.1', 'PyRIC==0.1.6.3',
        'pyspeex_noise==1.0.2', 'PyTurboJPEG==1.8.3']

rows = []
for pin in PINS:
    stem = pin.split('==')[0].replace('-', '_').lower()
    wheels = [p for p in OUT.glob('*.whl') if stem.split('_')[0] in p.name.lower()]
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as zf:
            native = [n for n in zf.namelist() if n.endswith(('.so', '.pyd'))]
        kind = 'native-aarch64' if 'aarch64' in wheel.name else (
            'pure-python' if not native else 'native-other-arch')
        rows.append({'pin': pin, 'wheel': wheel.name, 'kind': kind,
                     'native_files': native[:3], 'bytes': wheel.stat().st_size})

usable = [r for r in rows if r['kind'] in ('pure-python', 'native-aarch64')]
unusable = [r for r in rows if r['kind'] == 'native-other-arch']
result = {'pins': len(PINS), 'usable_on_aarch64': len({r['pin'] for r in usable}),
          'rows': rows, 'unusable': [r['wheel'] for r in unusable],
          'boundary': 'Artifact classification only. None of these were installed or executed '
                      'on ARM64 hardware; the source tree still needs a Python >= 3.14 aarch64 runtime.'}
Path('/tmp/arm64-wheels-results.json').write_text(json.dumps(result, indent=2) + '\n')
for r in rows:
    print('%-9s %-46s %s' % (r['kind'], r['wheel'], r['pin']))
print('usable for aarch64: %d/%d pins' % (result['usable_on_aarch64'], len(PINS)))
