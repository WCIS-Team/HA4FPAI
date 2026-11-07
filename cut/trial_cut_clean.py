"""NEXT-01b: trial-delete the 9 scan-v2 candidates and validate on a CLEAN venv.

Validation rule learned this round: never `--force-reinstall` over an existing
tree; a missing integration must be absent from a brand-new install.
"""
import json
import shutil
import subprocess
from pathlib import Path

NAMES = ['mobile_app', 'plex', 'dhcp', 'ssdp', 'my', 'usage_prediction', 'go2rtc',
         'map_tiles', 'homeassistant_alerts']
SRC = Path('/src-clean')
TRIAL = Path('/src-trial2')
VENV = '/venv-trial'
CFG = Path('/cfg-trial')
OUT = Path('/tmp/trial2-results.json')


def run(cmd, timeout=1800):
    return subprocess.run(['sh', '-c', cmd], capture_output=True, text=True, timeout=timeout)


def main():
    if TRIAL.exists():
        shutil.rmtree(TRIAL)
    shutil.copytree(SRC, TRIAL)
    for name in NAMES:
        shutil.rmtree(TRIAL / 'homeassistant' / 'components' / name, ignore_errors=True)
    remaining = len([p for p in (TRIAL / 'homeassistant' / 'components').iterdir()
                     if p.is_dir() and (p / 'manifest.json').is_file()])
    print('integrations after trial cut:', remaining, flush=True)

    run('rm -rf %s && python -m venv %s' % (VENV, VENV))
    install = run('%s/bin/pip install --disable-pip-version-check -q %s' % (VENV, TRIAL))
    print('install rc=%s' % install.returncode, flush=True)

    if CFG.exists():
        shutil.rmtree(CFG)
    CFG.mkdir(parents=True)
    for f in ['configuration.yaml', 'automations.yaml', 'scripts.yaml', 'scenes.yaml']:
        shutil.copyfile(TRIAL / 'examples' / f, CFG / f)
    (CFG / 'HA_SMALL_TEST_ONLY').touch()

    check = run('%s/bin/hass --script check_config -c %s' % (VENV, CFG))
    errors = [ln for ln in check.stdout.splitlines() if 'ERROR' in ln or 'Invalid config' in ln]
    import_errors = [ln for ln in errors if 'ImportError' in ln or 'cannot import name' in ln]
    pipcheck = run('%s/bin/pip check' % VENV)

    result = {
        'deleted': NAMES,
        'integrations_after': remaining,
        'install_rc': install.returncode,
        'check_config_rc': check.returncode,
        'error_lines': errors[:8],
        'import_errors': import_errors[:5],
        'pip_check': pipcheck.stdout.strip().splitlines()[-3:],
        'passed': (install.returncode == 0 and check.returncode == 0 and not errors),
        'boundary': 'Clean venv install; config check only. Runtime behaviour and long-run '
                    'stability were not exercised here.',
    }
    OUT.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k not in ('deleted',)}, indent=1)[:900])
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())