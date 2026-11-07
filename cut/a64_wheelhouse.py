"""Build an aarch64 wheelhouse by fetching the exact versions pinned in the x86 one.

pip's cross-platform resolver gives 'resolution-too-deep' on HA's dependency graph,
so resolve nothing: take name==version from the x86 wheel filenames and fetch each
one with --no-deps.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

X86 = Path('/wheelhouse')
A64 = Path('/wh-a64')
PLATFORMS = ['manylinux_2_28_aarch64', 'manylinux_2_27_aarch64',
             'manylinux_2_24_aarch64', 'manylinux2014_aarch64']
OUT = Path('/tmp/a64-wheelhouse.json')

WHEEL_RE = re.compile(r'^(?P<name>[^-]+)-(?P<ver>[^-]+)(-.*)?\.whl$')


def parse_wheels(directory):
    pins = {}
    for p in sorted(directory.glob('*.whl')):
        m = WHEEL_RE.match(p.name)
        if m:
            pins[m.group('name').replace('_', '-').lower()] = m.group('ver')
    return pins


def download(name, version):
    cmd = [sys.executable, '-m', 'pip', 'download', '--disable-pip-version-check',
           '--no-deps', '--only-binary=:all:', '-d', str(A64)]
    for plat in PLATFORMS:
        cmd += ['--platform', plat]
    cmd.append('%s==%s' % (name, version))
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    return r.returncode == 0, (r.stdout + r.stderr).strip().splitlines()[-1:] or ['']


def main():
    A64.mkdir(parents=True, exist_ok=True)
    pins = parse_wheels(X86)
    ok, failed = [], []
    for name, version in pins.items():
        good, tail = download(name, version)
        if good:
            ok.append('%s==%s' % (name, version))
        else:
            failed.append({'pin': '%s==%s' % (name, version), 'reason': tail[0][:120]})
        print('%s %s==%s' % ('OK  ' if good else 'MISS', name, version), flush=True)

    got = len(list(A64.glob('*.whl')))
    result = {'x86_pins': len(pins), 'a64_wheels': got, 'resolved': len(ok),
              'failed': failed,
              'note': 'Versions taken from the x86 wheelhouse; --no-deps avoids the '
                      'resolution-too-deep failure of cross-platform resolution.'}
    OUT.write_text(json.dumps(result, indent=2) + '\n')
    print('\nfetched=%d missing=%d' % (len(ok), len(failed)))
    for f in failed[:12]:
        print('  miss:', f['pin'], '|', f['reason'])


if __name__ == '__main__':
    main()
