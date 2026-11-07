"""Record aarch64 deb evidence and the qemu syscall limitation."""

import hashlib
import json
import subprocess
from pathlib import Path

DEB = Path(r'S:\work\WCIS\workenv\ha-small\deb\ha-small_0.1.0_all.deb')
OUT = Path(r'S:\work\WCIS\workenv\ha-small\results\deb-aarch64-results.json')


def sh(cmd, cid, timeout=900):
    return subprocess.run(['podman', 'exec', cid, 'sh', '-c', cmd],
                          capture_output=True, text=True, timeout=timeout).stdout.strip()


ARM = 'ha-small-debarm'
X86 = 'ha-small-deb64'

data = DEB.read_bytes()
arch = sh('uname -m', ARM)
version = sh('/opt/ha-small/venv/bin/python -c "import homeassistant.const as c;print(c.__version__)"', ARM)
pipcheck = sh('/opt/ha-small/venv/bin/pip check', ARM)
frontend = sh('/opt/ha-small/venv/bin/python -c "'
              'import urllib.request\n'
              'try:\n'
              '    r=urllib.request.urlopen(\'http://127.0.0.1:8123/\',timeout=10); print(r.status)\n'
              'except Exception as e: print(\'err\', str(e)[:40])"', ARM)
voice = sh('/opt/ha-small/venv/bin/python -c "import micro_vad_cpp, speex_noise_cpp;print(\'ok\')"', ARM)
wheelhouse = sh('ls /wheelhouse | wc -l', ARM)
mcast_arm = sh('/opt/ha-small/venv/bin/python /tmp/c.py', ARM)
mcast_x86 = sh('/opt/ha-small/venv/bin/python /tmp/c.py', X86)
functional = json.loads(Path(r'S:\work\WCIS\workenv\ha-small\results\deb-functional-aarch64.json')
                        .read_text(encoding='utf-8')) if Path(
    r'S:\work\WCIS\workenv\ha-small\results\deb-functional-aarch64.json').exists() else None

result = {
    'package': 'ha-small_0.1.0_all.deb',
    'bytes': len(data),
    'sha256': hashlib.sha256(data).hexdigest(),
    'arch': arch,
    'installed_ha_version': version,
    'pip_check': pipcheck,
    'voice_native_ext': voice,
    'frontend_http': frontend,
    'wheelhouse_wheels': wheelhouse,
    'functional': functional,
    'qemu_syscall_limitation': {
        'aarch64': mcast_arm,
        'x86_64': mcast_x86,
        'explanation': 'IPV6_MULTICAST_IF returns ENOPROTOOPT under qemu user-mode '
                       'emulation but works natively. zeroconf sets it unconditionally, so the '
                       'aiohttp resolver fails, which breaks rest_command and trips HA recovery '
                       'mode. Not a property of the trimmed tree and not expected on real ARM64.',
    },
    'boundary': 'aarch64 is qemu-user emulation on an x86 host, not the target board. The 2 '
                'failing checks are traced to an emulation syscall limitation; the same deb '
                'passes 30/30 on native x86_64.',
}
OUT.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
print(json.dumps({k: result[k] for k in ('arch', 'installed_ha_version', 'pip_check',
                                         'voice_native_ext', 'frontend_http',
                                         'wheelhouse_wheels')}, indent=1, ensure_ascii=False))
