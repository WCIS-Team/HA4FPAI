#!/usr/bin/env python3
"""Validate an installed package without loading models or running inference.

Use --package only inside a disposable Ubuntu 20.04 container. This mode ends
with the package purged, preserving deliberately created user-data fixtures.
Without --package, validate the current installation without uninstalling it.
"""
import argparse
import grp
import hashlib
import json
import os
from pathlib import Path
import platform
import pwd
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
import uuid


ROOT = Path('/usr/lib/lang-server')
STATE = Path('/var/lib/lang-server')
CONFIG = Path('/etc/lang-server/config')
PYTHON = STATE / 'venv/bin/python'
UNITS = tuple('lang-server-' + name + '.service'
              for name in ('http', 'voice', 'loop', 'model-download'))
ONLINE = {'asr': 'remote-api', 'llm': 'openai', 'tts': 'edge',
          'vad': 'none', 'kws': 'passthrough'}
REPORT = {'schema_version': 1, 'checks': [], 'ok': False}

# These guards apply only to the temporary HTTP smoke process. Health and
# component discovery must work without constructing stages or loading models.
HTTP_LAUNCHER = r'''
import importlib.abc
import runpy
import sys
class NoModels(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {
            'sherpa_onnx', 'onnxruntime', 'torch', 'tensorflow',
            'transformers', 'funasr', 'sherpa', 'llama_cpp'}:
            print('CI_FORBIDDEN_MODEL_IMPORT:' + fullname, flush=True)
            raise RuntimeError('Model imports are forbidden in this smoke test')
sys.meta_path.insert(0, NoModels())
sys.path.insert(0, '/usr/lib/lang-server/lang/server')
import core
def forbidden(*args, **kwargs):
    print('CI_FORBIDDEN_MODEL_OR_INFERENCE_CALL', flush=True)
    raise RuntimeError('Stage construction, inference and warm are forbidden')
for name in ('stage', 'warm', 'run_audio', 'run_text'):
    if hasattr(core.Chain, name):
        setattr(core.Chain, name, forbidden)
sys.argv = ['/usr/lib/lang-server/lang/server/app.py', '--host', '127.0.0.1',
            '--port', sys.argv[1], '--config', '/etc/lang-server/config', '--no-warm']
runpy.run_path(sys.argv[0], run_name='__main__')
'''


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def command(argv, timeout=300, expected=0, env=None):
    proc = subprocess.run([str(v) for v in argv], stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, universal_newlines=True,
                          encoding='utf-8', errors='replace', timeout=timeout,
                          env=env)
    require(proc.returncode == expected,
            'Command failed ({0}): {1}\n{2}'.format(
                proc.returncode, ' '.join(str(v) for v in argv), proc.stdout[-8000:]))
    return proc.stdout.strip()


def check(name, action):
    started = time.monotonic()
    row = {'name': name}
    REPORT['checks'].append(row)
    try:
        row['details'] = action()
        row['ok'] = True
        return row['details']
    except Exception as exc:
        row.update(ok=False, error=str(exc))
        raise
    finally:
        row['duration_s'] = round(time.monotonic() - started, 3)


def container_environment():
    if Path('/.dockerenv').exists() or Path('/run/.containerenv').exists():
        return True
    if shutil.which('systemd-detect-virt'):
        return subprocess.call(['systemd-detect-virt', '--container', '--quiet']) == 0
    return False


def environment_check(destructive):
    require(os.geteuid() == 0, 'Run this validator as root')
    require(sys.version_info >= (3, 8), 'Python 3.8 or newer is required')
    info = {}
    for line in Path('/etc/os-release').read_text(encoding='utf-8').splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            info[key] = value.strip('"')
    require(info.get('ID') == 'ubuntu' and info.get('VERSION_ID') == '20.04',
            'This integration test requires Ubuntu 20.04')
    container = container_environment()
    if destructive:
        require(container, '--package remove/purge tests require a disposable container')
        require(not Path('/run/systemd/system').exists(),
                '--package requires a container without a running systemd manager')
    return {'os': info.get('PRETTY_NAME'), 'machine': platform.machine(),
            'python': platform.python_version(), 'container': container,
            'systemd_running': Path('/run/systemd/system').exists(),
            'evidence_scope': 'Container packaging smoke only; not arm64 hardware validation',
            'models_loaded': False, 'inference_executed': False}


def preinst_check(package):
    old = Path('/etc/systemd/system/lang-server-http.service')
    require(not old.exists() and not old.is_symlink(),
            'Legacy-guard fixture path must be absent in this disposable container')
    original = b'[Unit]\nDescription=CI legacy guard fixture\n'
    with tempfile.TemporaryDirectory(prefix='lang-ci-control-') as temp:
        command(['dpkg-deb', '--control', package, temp])
        script = Path(temp) / 'preinst'
        require(script.is_file(), 'Package must contain the legacy preinst guard')
        old.parent.mkdir(parents=True, exist_ok=True)
        try:
            old.write_bytes(original)
            for action in ('install', 'upgrade'):
                output = command(['/bin/sh', script, action], expected=1)
                require('Legacy systemd unit found:' in output,
                        'Preinst must explain why a legacy unit is rejected')
                require(old.read_bytes() == original,
                        'Preinst changed a hand-installed service')
            old.unlink()
            old.symlink_to('/lib/systemd/system/lang-server-http.service')
            command(['/bin/sh', script, 'upgrade'])
        finally:
            if old.is_symlink() or (old.is_file() and old.read_bytes() == original):
                old.unlink()
    return {'legacy_install_rejected': True, 'legacy_upgrade_rejected': True,
            'existing_unit_unchanged': True, 'packaged_symlink_accepted': True}


def installed_check():
    status = command(['dpkg-query', '-W', '-f=${Status}', 'lang-server'])
    require(status == 'install ok installed', 'lang-server must be configured')
    require(PYTHON.is_file(), 'Private virtual environment is missing')
    return {'status': status, 'architecture': command(
        ['dpkg-query', '-W', '-f=${Architecture}', 'lang-server'])}


def isolation_check():
    probe = r'''
import importlib.util, json, site, sys
print(json.dumps({'prefix':sys.prefix, 'base_prefix':sys.base_prefix,
 'user_site_enabled':site.ENABLE_USER_SITE, 'paths':sys.path,
 'numpy_origin':importlib.util.find_spec('numpy').origin}))
'''
    data = json.loads(command([PYTHON, '-I', '-c', probe]))
    require(data['prefix'] == str(STATE / 'venv'), 'Unexpected venv prefix')
    require(data['prefix'] != data['base_prefix'], 'Python is not using a venv')
    require(data['user_site_enabled'] is False, 'User site packages must be disabled')
    cfg = (STATE / 'venv/pyvenv.cfg').read_text(encoding='utf-8').lower()
    require('include-system-site-packages = false' in cfg,
            'The private venv must not inherit system Python packages')
    for path in data['paths']:
        if 'site-packages' in path or 'dist-packages' in path:
            require(path.startswith(str(STATE / 'venv') + '/'),
                    'System package directory leaked into the private venv: ' + path)
    require(data['numpy_origin'].startswith(str(STATE / 'venv') + '/'),
            'NumPy must come from the bundled private wheel, not system APT')
    data['pip_check'] = command([PYTHON, '-I', '-m', 'pip', '--isolated', 'check'])
    for path in (STATE, STATE / 'venv'):
        st = path.stat()
        require(st.st_uid == 0 and st.st_mode & 0o022 == 0,
                'The service account must not be able to replace the venv: ' + str(path))
    return data


def units_check():
    links = {}
    for unit in UNITS:
        unit_path = Path('/lib/systemd/system') / unit
        require(unit_path.is_file(), 'Missing packaged unit: ' + unit)
        text = unit_path.read_text(encoding='utf-8')
        require('User=lang-server' in text, 'Unit must run as the service user: ' + unit)
        require('StandardOutput=journal' in text, 'Unit must log to journal: ' + unit)
        if unit != 'lang-server-model-download.service':
            require('--no-warm' in text, 'Normal service must disable warm: ' + unit)
        link = Path('/etc/systemd/system/multi-user.target.wants') / unit
        require(link.is_symlink() and link.resolve() == unit_path.resolve(),
                'Unit is not enabled, including in a non-systemd container: ' + unit)
        links[unit] = os.readlink(str(link))
    require(not Path('/etc/lang-server/no-model-download').exists(),
            'This default-install test expects automatic download to be enabled')
    if shutil.which('systemd-analyze'):
        command(['systemd-analyze', 'verify'] +
                [str(Path('/lib/systemd/system') / unit) for unit in UNITS])
        analyzer = 'passed'
    else:
        analyzer = 'unavailable; unit syntax was not analyzer-verified'
    return {'enabled_links': links, 'systemd_analyze': analyzer}


def http_check():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    account = pwd.getpwnam('lang-server')
    audio = grp.getgrnam('audio').gr_gid

    def service_user():
        os.setgroups([account.pw_gid, audio])
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)

    env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8',
           'HOME': str(STATE), 'XDG_CACHE_HOME': str(STATE / 'cache'),
           'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1',
           'LANG_CONFIG_DIR': str(CONFIG)}
    with tempfile.TemporaryFile(mode='w+b') as log:
        proc = subprocess.Popen([str(PYTHON), '-I', '-B', '-c', HTTP_LAUNCHER, str(port)],
                                cwd=str(STATE), env=env, preexec_fn=service_user,
                                stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 30
            health = None
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            while time.monotonic() < deadline:
                require(proc.poll() is None, 'Temporary HTTP process exited before becoming healthy')
                try:
                    with opener.open('http://127.0.0.1:{0}/health'.format(port), timeout=1) as response:
                        health = json.load(response)
                    break
                except (urllib.error.URLError, TimeoutError, ConnectionError):
                    time.sleep(0.1)
            require(health is not None and health.get('ok') is True,
                    'HTTP health endpoint did not become ready')
            require(health.get('service') == 'lang-server', 'Wrong HTTP service identity')
            with opener.open('http://127.0.0.1:{0}/components'.format(port), timeout=5) as response:
                components = json.load(response)
            for stage, expected in ONLINE.items():
                require(health.get('stages', {}).get(stage) == expected,
                        'Unexpected online health selection for ' + stage)
                require(components.get(stage, {}).get('active') == expected,
                        'Unexpected online component selection for ' + stage)
            return {'bind': '127.0.0.1', 'port': port, 'health': health,
                    'components': components, 'requests': ['GET /health', 'GET /components'],
                    'model_import_and_inference_guards': True,
                    'process_user': 'lang-server'}
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            log.seek(0)
            output = log.read().decode('utf-8', errors='replace')
            REPORT['http_process_log'] = output[-12000:]
            require('CI_FORBIDDEN_' not in output,
                    'Temporary HTTP process attempted a model import, stage construction or inference')


def fixture_checks(package):
    # Modify a default filename as well as add a user file: reinstall must not
    # replace credentials merely because a sanitized default is available.
    online = CONFIG / 'online.yaml'
    require(online.is_file(), 'Expected seeded online.yaml for preservation test')
    online.write_bytes(online.read_bytes() + b'\n# CI preservation sentinel, not a credential\n')
    fixture_id = uuid.uuid4().hex[:12]
    fixtures = {online: online.read_bytes(),
                CONFIG / ('ci-preserved-' + fixture_id + '.yaml'): b'ci_preserved:\n  value: keep-me\n',
                STATE / ('models/ci-user-cache-' + fixture_id + '.txt'): b'CI fixture; this is not a model\n'}
    for path, content in fixtures.items():
        if path != online:
            require(not path.exists(), 'Fixture path already exists: ' + str(path))
            path.write_bytes(content)

    def preserved(phase):
        for path, content in fixtures.items():
            require(path.is_file() and path.read_bytes() == content,
                    phase + ' lost or changed user data: ' + str(path))
        return {'phase': phase, 'fixture_sha256': {
            str(path): hashlib.sha256(value).hexdigest() for path, value in fixtures.items()}}

    check('reinstall_package', lambda: command(
        ['apt-get', 'install', '-y', '--reinstall', str(package)]))
    check('reinstall_preserves_config_and_models', lambda: preserved('reinstall'))
    check('reinstalled_units_enabled', units_check)
    check('remove_package', lambda: command(['apt-get', 'remove', '-y', 'lang-server']))
    check('remove_preserves_config_and_models', lambda: preserved('remove'))
    check('purge_package', lambda: command(['apt-get', 'purge', '-y', 'lang-server']))
    check('purge_preserves_config_and_models', lambda: preserved('purge'))
    require(not (STATE / 'venv').exists(), 'Purge must remove the package-owned venv')
    require(not (CONFIG / 'zz-package-online.yaml').exists(),
            'Purge must remove the package-owned online overlay')
    REPORT['final_package_state'] = 'purged; user config and dummy model cache retained'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path,
                        help='Install/reinstall/remove/purge this deb in a disposable container')
    parser.add_argument('--report', type=Path, required=True, help='JSON report destination')
    args = parser.parse_args()
    exit_code = 1
    try:
        REPORT['environment'] = check('environment', lambda: environment_check(bool(args.package)))
        if args.package:
            package = args.package.resolve(strict=True)
            check('preinst_legacy_guard', lambda: preinst_check(package))
            check('install_package', lambda: command(['apt-get', 'install', '-y', str(package)]))
        check('package_configured', installed_check)
        check('private_venv_and_pip_check', isolation_check)
        check('four_units_enabled_and_valid', units_check)
        check('online_http_without_models', http_check)
        if args.package:
            fixture_checks(package)
        REPORT['ok'] = True
        exit_code = 0
    except Exception as exc:
        REPORT['error'] = str(exc)
        REPORT['traceback'] = traceback.format_exc()
    finally:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(REPORT, ensure_ascii=False, indent=2) + '\n',
                               encoding='utf-8')
        print(json.dumps({'ok': REPORT['ok'], 'report': str(args.report),
                          'checks': len(REPORT['checks'])}))
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
