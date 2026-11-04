#!/usr/bin/env python3
"""Build a Debian binary package without compiling or executing model runtimes."""
import argparse
import datetime
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import sys
import tarfile

HERE = Path(__file__).resolve().parent
LANG = HERE.parents[1]
WORKSPACE = next((p for p in LANG.parents if (p / 'AGENTS.md').is_file()), LANG.parent.parent)


def merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            merge(dst[k], v)
        else:
            dst[k] = v
    return dst


def yaml_dump(data, indent=0):
    lines = []
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(' ' * indent + key + ':')
            lines.extend(yaml_dump(value, indent + 2))
        else:
            value = str(value)
            if '\n' in value or ('\"' in value and "'" in value):
                raise ValueError('Unsupported configuration scalar: ' + key)
            q = "'" if '\"' in value else '\"'
            lines.append(' ' * indent + key + ': ' + q + value + q)
    return lines


def put(root, rel, content):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content.encode('utf-8') if isinstance(content, str) else content)


def config_defaults(root):
    sys.path.insert(0, str(LANG / 'server'))
    from cfg import parse
    def scrub(data):
        for key, value in data.items():
            if isinstance(value, dict):
                scrub(value)
            elif key.lower() in ('api_key','token','secret','password','access_token','client_secret','authorization','keyfile'):
                data[key] = ''
            elif isinstance(value, str) and re.match(r'^[A-Za-z]:[/\\]', value):
                data[key] = ''
    target = 'usr/lib/lang-server/default-config/'
    for p in sorted((LANG / 'config').glob('*.yaml')):
        if p.name == 'online.yaml' or p.name.startswith('zz-'):
            continue
        data = parse(p.read_text(encoding='utf-8-sig'))
        scrub(data)
        put(root, target + p.name, '\n'.join(yaml_dump(data)) + '\n')
    online = parse((LANG / 'config-examples/online.yaml').read_text(encoding='utf-8'))
    scrub(online)
    online.setdefault('online', {}).setdefault('edge', {})['decoder'] = 'mpg123'
    put(root, target + 'online.yaml', '\n'.join(yaml_dump(online)) + '\n')
    models = '/var/lib/lang-server/models/'
    manifest = json.loads((HERE / 'models.json').read_text(encoding='utf-8'))
    paths = {m['id']: models + m['path'] for m in manifest['models']}
    a, t, k, v, voc = (paths[n] for n in ['asr','tts','kws','vad','vocoder'])
    profiles = {
        'asr_profiles': {'sherpa': {'encoder': a+'/encoder.int8.onnx','decoder':a+'/decoder.onnx','joiner':a+'/joiner.int8.onnx','tokens':a+'/tokens.txt','num_threads':'1'}},
        'kws_profiles': {'sherpa': {'model_dir':k,'keywords_file':'test_wavs/keywords.txt','num_threads':'1'}},
        'vad_profiles': {'sherpa': {'model':v+'/onnx/model.onnx','num_threads':'1'}},
        'tts_profiles': {'sherpa': {'kind':'matcha','model':t+'/model-steps-3.onnx','tokens':t+'/tokens.txt','lexicon':t+'/lexicon.txt','data_dir':t+'/dict','vocoder':voc+'/vocos-22khz-univ.onnx','num_threads':'1'}},
        'spk_profiles': {'sherpa': {'library':'/var/lib/lang-server/speakers/library.json'}}}
    put(root, target+'zy-package-model-paths.yaml', '\n'.join(yaml_dump(profiles))+'\n')


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--architecture', choices=['arm64','amd64'], default='arm64')
    parser.add_argument('--version', default='0.1.0+20261104')
    parser.add_argument('--wheelhouse', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=WORKSPACE/'workenv/lang-linux-package/dist')
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9][A-Za-z0-9.+:~\-]*', args.version):
        parser.error('Invalid Debian version')
    if not (args.wheelhouse / 'requirements.lock').is_file():
        parser.error('Wheel lock missing')
    audit = json.loads((args.wheelhouse/'metadata-audit.json').read_text(encoding='utf-8'))
    if not audit.get('complete'):
        parser.error('Wheel dependency audit incomplete')
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%f')
    staging = args.output.parent / ('stage-' + args.architecture + '-' + stamp)
    data, control = staging/'data', staging/'control'
    data.mkdir(parents=True)
    control.mkdir()
    # Explicit source roots: no credentials, archives, models, logs, or mutable state.
    for rel in ['server','split','kws','tts/models/edge']:
        folder = LANG / rel
        for p in sorted(folder.rglob('*.py')):
            parts = p.relative_to(folder).parts
            if any(x in parts for x in ['archive','tests','__pycache__','.git','models']) and rel != 'tts/models/edge':
                continue
            put(data, 'usr/lib/lang-server/lang/'+p.relative_to(LANG).as_posix(), p.read_bytes())
    put(data, 'usr/lib/lang-server/lang/vad/rms.py', (LANG/'vad/rms.py').read_bytes())
    keywords = LANG/'kws/keywords.txt'
    if keywords.is_file():
        put(data, 'usr/lib/lang-server/lang/kws/keywords.txt', keywords.read_bytes())
    config_defaults(data)
    for p in args.wheelhouse.glob('*.whl'):
        put(data, 'usr/lib/lang-server/wheelhouse/'+p.name, p.read_bytes())
    for name in ['requirements.lock','wheelhouse.SHA256']:
        put(data, 'usr/lib/lang-server/'+name, (args.wheelhouse/name).read_bytes())
    for name in ['model_install.py','models.json']:
        put(data, 'usr/lib/lang-server/'+name, (HERE/name).read_bytes())
    for p in (HERE/'systemd').glob('*.service'):
        put(data, 'lib/systemd/system/'+p.name, p.read_bytes())
    for name in ['README.md']:
        if (HERE/name).exists():
            put(data, 'usr/share/doc/lang-server/'+name, (HERE/name).read_bytes())
    installed = sum(p.stat().st_size for p in data.rglob('*') if p.is_file())
    ctl = (HERE/'debian/control.in').read_text(encoding='utf-8').replace('@VERSION@', args.version).replace('Architecture: arm64','Architecture: '+args.architecture)
    ctl += 'Installed-Size: '+str((installed+1023)//1024)+'\n'
    put(control, 'control', ctl)
    for name in ['preinst','postinst','prerm','postrm']:
        put(control, name, (HERE/'debian'/name).read_bytes())
    sums = []
    for p in sorted(data.rglob('*')):
        if p.is_file():
            sums.append(hashlib.md5(p.read_bytes()).hexdigest()+'  '+p.relative_to(data).as_posix())
    put(control, 'md5sums', '\n'.join(sums)+'\n')
    epoch = 1793750400
    ctar = tree_tar(control, {x:0o755 for x in ['preinst','postinst','prerm','postrm']}, epoch)
    dtar = tree_tar(data, {}, epoch)
    args.output.mkdir(parents=True, exist_ok=True)
    name = 'lang-server_'+args.version+'_'+args.architecture
    deb = args.output/(name+'.deb')
    if deb.exists():
        raise RuntimeError('Refusing to overwrite existing artifact; choose another version/output')
    deb.write_bytes(b'!<arch>\n'+ar_member('debian-binary',b'2.0\n',epoch)+ar_member('control.tar.gz',ctar,epoch)+ar_member('data.tar.gz',dtar,epoch))
    release = args.output/(name+'_ubuntu20.04.tar.gz')
    with tarfile.open(str(release), 'w:gz') as tar:
        tar.add(str(deb), arcname=name+'/'+deb.name)
        script = (HERE/'install.sh').read_bytes()
        info = tarfile.TarInfo(name+'/install.sh'); info.size=len(script); info.mode=0o755
        tar.addfile(info, io.BytesIO(script))
        if (HERE/'README.md').is_file():
            tar.add(str(HERE/'README.md'),arcname=name+'/README.md')
        checksum = hashlib.sha256(deb.read_bytes()).hexdigest()+'  '+deb.name+'\n'
        info = tarfile.TarInfo(name+'/SHA256SUMS'); info.size=len(checksum.encode()); info.mode=0o644
        tar.addfile(info, io.BytesIO(checksum.encode()))
    for p in [deb,release]:
        print(json.dumps({'artifact':str(p),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}))
    print('staging='+str(staging))


if __name__ == '__main__':
    main()
