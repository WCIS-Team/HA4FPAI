#!/usr/bin/env python3
"""Build the ha4fpai-control Debian package without dpkg-deb (pure stdlib).

Mirrors lang/packaging/linux/build_deb.py's ar/tar assembly. The control
service is pure-stdlib Python, so unlike lang there is no wheelhouse:
the package ships the control/ package, two systemd units and a doc file.
No post-install auto-start: units stay manual per repo AGENTS convention.
"""
import argparse
import datetime
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile

HERE = Path(__file__).resolve().parent
CTRL = HERE.parent                      # control/ 包根（parents[1] 是仓库根——曾因此全仓误入包）
WORKSPACE = next((p for p in CTRL.parents if (p / 'AGENTS.md').is_file()), CTRL.parent)


def put(root, rel, content):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content.encode('utf-8') if isinstance(content, str) else content)


def tree_tar(root, modes, epoch):
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode='wb', mtime=epoch) as zipped:
        with tarfile.open(fileobj=zipped, mode='w', format=tarfile.GNU_FORMAT) as tar:
            for p in sorted(root.rglob('*')):
                rel = p.relative_to(root).as_posix()
                info = tarfile.TarInfo('./' + rel)
                info.uid = info.gid = 0
                info.uname = info.gname = 'root'
                info.mtime = epoch
                info.mode = modes.get(rel, 0o755 if p.is_dir() else 0o644)
                if p.is_dir():
                    info.type = tarfile.DIRTYPE
                    tar.addfile(info)
                elif p.is_file():
                    info.size = p.stat().st_size
                    with p.open('rb') as f:
                        tar.addfile(info, f)
                else:
                    raise ValueError('Unexpected staging file type')
    return buf.getvalue()


def ar_member(name, data, epoch):
    header = (name + '/').ljust(16) + str(epoch).ljust(12) + '0'.ljust(6) + '0'.ljust(6) + '100644'.ljust(8) + str(len(data)).ljust(10) + '`\n'
    return header.encode('ascii') + data + (b'\n' if len(data) % 2 else b'')


def stage_data(data):
    """control 包源码（去 tests/__pycache__）＋ systemd 单元 ＋ 文档。"""
    for p in sorted(CTRL.rglob('*.py')):
        parts = p.relative_to(CTRL).parts
        if 'tests' in parts or '__pycache__' in parts:
            continue
        put(data, 'usr/lib/ha4fpai-control/control/' + p.relative_to(CTRL).as_posix(), p.read_bytes())
    for p in sorted((CTRL / 'deploy').glob('*.service')):
        put(data, 'lib/systemd/system/' + p.name, p.read_bytes())
    for name in ['README.md']:
        if (CTRL / name).is_file():
            put(data, 'usr/share/doc/ha4fpai-control/' + name, (CTRL / name).read_bytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--architecture', choices=['arm64', 'amd64'], default='arm64')
    parser.add_argument('--version', default='0.1.0+20261105')
    parser.add_argument('--output', type=Path,
                        default=WORKSPACE / 'workenv/ha4fpai-control-package/dist')
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9][A-Za-z0-9.+:~\-]*', args.version):
        parser.error('Invalid Debian version')
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%f')
    staging = args.output.parent / ('stage-' + args.architecture + '-' + stamp)
    data, control = staging / 'data', staging / 'control'
    data.mkdir(parents=True)
    control.mkdir()
    stage_data(data)
    installed = sum(p.stat().st_size for p in data.rglob('*') if p.is_file())
    ctl = (HERE / 'debian/control.in').read_text(encoding='utf-8')
    ctl = ctl.replace('@VERSION@', args.version).replace(
        'Architecture: arm64', 'Architecture: ' + args.architecture)
    ctl += 'Installed-Size: ' + str((installed + 1023) // 1024) + '\n'
    put(control, 'control', ctl)
    scripts = ['postinst', 'prerm', 'postrm']
    for name in scripts:
        put(control, name, (HERE / 'debian' / name).read_bytes())
    sums = []
    for p in sorted(data.rglob('*')):
        if p.is_file():
            sums.append(hashlib.md5(p.read_bytes()).hexdigest() + '  ' + p.relative_to(data).as_posix())
    put(control, 'md5sums', '\n'.join(sums) + '\n')
    epoch = 1793836800
    ctar = tree_tar(control, {name: 0o755 for name in scripts}, epoch)
    dtar = tree_tar(data, {}, epoch)
    args.output.mkdir(parents=True, exist_ok=True)
    name = 'ha4fpai-control_' + args.version + '_' + args.architecture
    deb = args.output / (name + '.deb')
    if deb.exists():
        raise RuntimeError('Refusing to overwrite existing artifact; choose another version/output')
    deb.write_bytes(b'!<arch>\n' + ar_member('debian-binary', b'2.0\n', epoch)
                    + ar_member('control.tar.gz', ctar, epoch)
                    + ar_member('data.tar.gz', dtar, epoch))
    release = args.output / (name + '_ubuntu20.04.tar.gz')
    with tarfile.open(str(release), 'w:gz') as tar:
        tar.add(str(deb), arcname=name + '/' + deb.name)
        checksum = (hashlib.sha256(deb.read_bytes()).hexdigest() + '  ' + deb.name + '\n').encode('utf-8')
        info = tarfile.TarInfo(name + '/SHA256SUMS')
        info.size = len(checksum)
        info.mode = 0o644
        tar.addfile(info, io.BytesIO(checksum))
    for p in [deb, release]:
        print(json.dumps({'artifact': str(p), 'bytes': p.stat().st_size,
                          'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}))
    print('staging=' + str(staging))


if __name__ == '__main__':
    main()
