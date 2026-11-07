"""Record the deb packaging evidence (structure, install, offline setup, startup)."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

DEB = Path(sys.argv[1])
OUT = Path(sys.argv[2])


def sh(cmd, cid):
    return subprocess.run(['podman', 'exec', cid, 'sh', '-c', cmd],
                          capture_output=True, text=True, timeout=600).stdout.strip()


def main():
    data = DEB.read_bytes()
    info = sh('dpkg-deb -f /tmp/p.deb Package Version Architecture Depends Recommends', 'ha-small-deb64')
    contents = sh('dpkg-deb -c /tmp/p.deb | wc -l', 'ha-small-deb64')
    files = sh('dpkg -L ha-small | wc -l', 'ha-small-deb64')
    venv = sh('/opt/ha-small/venv/bin/python -c "import homeassistant.const as c;print(c.__version__)"',
              'ha-small-deb64')
    pipcheck = sh('/opt/ha-small/venv/bin/pip check', 'ha-small-deb64')
    voice = sh('/opt/ha-small/venv/bin/python -c "import micro_vad_cpp, speex_noise_cpp;print(\'ok\')"',
               'ha-small-deb64')
    probe = ('/opt/ha-small/venv/bin/python -c "'
             'import urllib.request\n'
             'try:\n'
             '    r=urllib.request.urlopen(\'http://127.0.0.1:8123/\',timeout=10); print(r.status)\n'
             'except Exception as e: print(\'err\', str(e)[:40])"')
    frontend = sh(probe, 'ha-small-deb64')

    result = {
        'package': 'ha-small_0.1.0_all.deb',
        'bytes': len(data),
        'sha256': hashlib.sha256(data).hexdigest(),
        'control': info.splitlines(),
        'entries_in_deb': contents,
        'installed_files': files,
        'installed_ha_version': venv,
        'pip_check': pipcheck,
        'voice_native_ext': voice,
        'frontend_http': frontend,
        'how': 'dpkg -i --force-depends (the test image has no dpkg python3 package); '
               'HA_SMALL_WHEELHOUSE=/wheelhouse /usr/bin/ha-small-setup for the offline install',
        'wheelhouse': {'wheels': 103, 'bytes_compressed': 126570460},
        'boundary': 'Built and tested on Debian 13 x86_64 containers. No distro today ships '
                    'python3.14 as a dpkg package, so Depends cannot be satisfied by apt; the '
                    'wheelhouse is x86_64 only, so there is no aarch64 deb yet. No systemd boot '
                    'test (containers have no systemd PID 1).',
    }
    OUT.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: result[k] for k in ('bytes', 'sha256', 'installed_ha_version',
                                             'pip_check', 'voice_native_ext', 'frontend_http')},
                     indent=1))


if __name__ == '__main__':
    main()
