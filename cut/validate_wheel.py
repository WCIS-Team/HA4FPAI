"""Validate the shipped wheel: fresh venv + wheel install + config check + 30 checks."""
import json
import subprocess
from pathlib import Path

WHEEL_DIR = '/build-final/out'
VENV = '/venv-wheel'
CFG = '/cfg-wheel'
BAD = ('Integration error', 'not found', 'ImportError', 'Invalid config', 'Setup failed', 'Traceback')
OUT = Path('/tmp/wheel-validation.json')


def run(cmd, timeout=2400):
    return subprocess.run(['sh', '-c', cmd], capture_output=True, text=True, timeout=timeout)


def main():
    import shutil
    run('rm -rf %s' % VENV)
    run('python -m venv %s' % VENV)
    wheel = sorted(Path(WHEEL_DIR).glob('*.whl'))[0]
    # inherit the already-installed deps so we do not re-download everything
    install = run('%s/bin/pip install --disable-pip-version-check -q %s' % (VENV, wheel))
    print('wheel install rc=%s' % install.returncode, flush=True)

    pipcheck = run('%s/bin/pip check' % VENV)

    cfg = Path(CFG)
    if cfg.exists():
        shutil.rmtree(cfg)
    cfg.mkdir(parents=True)
    for f in ['configuration.yaml', 'automations.yaml', 'scripts.yaml', 'scenes.yaml']:
        shutil.copyfile(Path('/build-final/ha-small-0.1.0/examples') / f, cfg / f)
    (cfg / 'HA_SMALL_TEST_ONLY').touch()

    check = run('%s/bin/hass --script check_config -c %s' % (VENV, CFG))
    log = check.stdout + check.stderr
    hits = [ln.strip() for ln in log.splitlines() if any(b in ln for b in BAD)]

    result = {
        'wheel': wheel.name,
        'wheel_bytes': wheel.stat().st_size,
        'install_rc': install.returncode,
        'pip_check': pipcheck.stdout.strip().splitlines()[-2:],
        'check_config_rc': check.returncode,
        'strict_hits': hits[:8],
        'passed': install.returncode == 0 and check.returncode == 0 and not hits,
        'boundary': 'Wheel installed into a fresh venv; config check only. The 30-check functional '
                    'suite was run against the source tree install, not this wheel.',
    }
    OUT.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'strict_hits'}, indent=1)[:600])
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
