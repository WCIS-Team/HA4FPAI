#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Repeatable lang deployment. Stdlib controller; optional existing Paramiko/HF/MS clients.
Default is a read-only plan. See DEPLOYMENT.md. Compatible with Python 3.8+.
"""
import argparse
import base64
import datetime
import getpass
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parent
WORKSPACE = next((p for p in ROOT.parents if (p / 'AGENTS.md').is_file() and (p / 'repos').is_dir()), ROOT.parent.parent)
DEFAULT_WORK = WORKSPACE / 'workenv' / 'lang-deploy'
UNITS = ['lang-server-http', 'lang-server-voice', 'lang-server-loop']
ASR = 'sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30'
KWS = 'sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20'
SPK = '3dspeaker_speech_campplus_sv_zh-cn_16k-common'
# Download only assets used by the implemented CPU backends. NPU artifacts are external.
# spk（声纹/CAM++）默认不入链，但文件照样要装：接口 /api/v1/spk/* 与 verify 路由都靠它。
MODELS = {
    'asr': ('asr/models/' + ASR, 'csukuangfj/' + ASR,
            ['encoder.int8.onnx', 'decoder.onnx', 'joiner.int8.onnx', 'tokens.txt']),
    'kws': ('kws/models/' + KWS, 'csukuangfj/' + KWS,
            ['encoder*.int8.onnx', 'decoder*.onnx', 'joiner*.int8.onnx', 'tokens.txt', '**/keywords.txt']),
    'vad': ('vad/models/silero', 'onnx-community/silero-vad', ['onnx/model.onnx']),
    'tts': ('tts/models/matcha-icefall-zh-baker', 'csukuangfj/matcha-icefall-zh-baker',
            ['model-steps-3.onnx', 'tokens.txt', 'lexicon.txt', '*.fst', 'dict/**/*']),
    'vocoder': ('tts/models/vocoder', 'k2-fsa/sherpa-onnx-models', ['vocos-22khz-univ.onnx']),
    # 只取官方 fp32 那件（本仓量化出的 .int8.onnx 不随包；要 int8 就设 spk_profiles.sherpa.prefer_int8=1 并自己拷）
    'spk': ('vertify/models/' + SPK, '', [SPK + '.onnx']),
}
# 只有 GitHub release 才有、且没有 HF/ModelScope 镜像的资产：name -> (release tag, 资产名)
RELEASES = {
    'vocoder': ('vocoder-models', 'vocos-22khz-univ.onnx'),
    # ⚠️ tag 上游拼写就是 recongition（不是 recognition），改错就 404
    'spk': ('speaker-recongition-models', SPK + '.onnx'),
}
PATH_KEYS = {'dir', 'model_dir', 'encoder', 'decoder', 'joiner', 'tokens', 'lexicon', 'vocoder', 'data_dir', 'keywords_file', 'keyfile'}


def say(label, value):
    print(label + ': ' + json.dumps(value, ensure_ascii=False), flush=True)


def shell(argv):
    return ' '.join(shlex.quote(str(x)) for x in argv)


class Target:
    def __init__(self, args):
        self.args = args
        self.client = None
        self.remote = args.target == 'board'
        if self.remote:
            use_paramiko = args.transport == 'paramiko' or (args.transport == 'auto' and importlib.util.find_spec('paramiko'))
            if use_paramiko:
                import paramiko
                self.client = paramiko.SSHClient()
                self.client.load_system_host_keys()
                if args.accept_new_host_key:
                    self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                password = os.environ.get('LANG_DEPLOY_PASSWORD')
                if args.ask_password:
                    password = getpass.getpass('SSH password: ')
                self.client.connect(args.host, port=args.port, username=args.user, password=password,
                                    key_filename=args.identity, timeout=15, auth_timeout=15)
            elif not shutil.which('ssh') or not shutil.which('scp'):
                raise RuntimeError('Need existing ssh/scp or Paramiko; no tools installed automatically')

    def run(self, argv, timeout=300, stdin=None):
        if self.remote:
            cmd = shell(argv)
            if self.client:
                sin, out, err = self.client.exec_command(cmd, timeout=timeout)
                if stdin is not None:
                    sin.write(stdin); sin.flush(); sin.channel.shutdown_write()
                # Drain both channels together; large native diagnostics cannot deadlock SSH.
                channel = out.channel
                chunks, errors = [], []
                deadline = time.monotonic() + timeout
                while True:
                    while channel.recv_ready(): chunks.append(channel.recv(65536))
                    while channel.recv_stderr_ready(): errors.append(channel.recv_stderr(65536))
                    if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready(): break
                    if time.monotonic() > deadline:
                        channel.close(); raise TimeoutError('Remote command timed out')
                    time.sleep(.02)
                rc = channel.recv_exit_status()
                output = b''.join(chunks).decode('utf-8', 'replace')
                error = b''.join(errors).decode('utf-8', 'replace')
            else:
                ssh = ['ssh', '-p', str(self.args.port), '-o', 'ConnectTimeout=15']
                if self.args.accept_new_host_key: ssh += ['-o', 'StrictHostKeyChecking=accept-new']
                if self.args.identity: ssh += ['-i', self.args.identity]
                p = subprocess.run(ssh + [self.args.user + '@' + self.args.host, cmd], input=stdin,
                                   text=True, encoding='utf-8', capture_output=True, timeout=timeout)
                rc, output, error = p.returncode, p.stdout, p.stderr
        else:
            p = subprocess.run([str(x) for x in argv], input=stdin, text=True, encoding='utf-8',
                               capture_output=True, timeout=timeout)
            rc, output, error = p.returncode, p.stdout, p.stderr
        if rc:
            # Do not print commands/configuration: those may contain merged secrets.
            raise RuntimeError('Command failed (exit %d): %s' % (rc, error[-1800:]))
        return output

    def py(self, code, timeout=300):
        return self.run([self.args.python if self.remote else sys.executable, '-X', 'utf8', '-c', code], timeout)

    def put(self, source, dest):
        if self.remote and self.client:
            with self.client.open_sftp() as s:
                s.put(str(source), str(dest))
        elif self.remote:
            argv = ['scp', '-P', str(self.args.port)]
            if self.args.identity: argv += ['-i', self.args.identity]
            if self.args.accept_new_host_key: argv += ['-o', 'StrictHostKeyChecking=accept-new']
            subprocess.run(argv + [str(source), self.args.user + '@' + self.args.host + ':' + str(dest)], check=True)
        else:
            shutil.copy2(str(source), str(dest))

    def close(self):
        if self.client: self.client.close()


def inspect_target(t, base):
    return json.loads(t.py("""import sys,platform,shutil,json,importlib.util,socket,os
p=%r
while not os.path.exists(p): p=os.path.dirname(p)
net={}
for h in ['pypi.org','huggingface.co','modelscope.cn']:
 try:
  with socket.create_connection((h,443),timeout=4): net[h]=True
 except OSError: net[h]=False
print(json.dumps({'python':list(sys.version_info[:3]),'arch':platform.machine(),
 'free':shutil.disk_usage(p).free,'packages':{m:bool(importlib.util.find_spec(m)) for m in ['numpy','sherpa_onnx','wyoming','edge_tts','miniaudio']},
 'tools':{m:shutil.which(m) for m in ['mpg123','ffmpeg','systemctl']},'network':net}))
""" % str(base)))


def load_cfg(t, base):
    # Config values travel only in memory; never emit them to the console/log.
    raw = t.py("import sys,json,os; sys.path.insert(0,%r); from cfg import Config; print(json.dumps(Config(%r).data))" %
               (str(base) + '/server', str(base) + '/config'))
    return json.loads(raw)


def merge(dst, src):
    for k, v in src.items():
        if k in dst and isinstance(dst[k], dict) and isinstance(v, dict): merge(dst[k], v)
        else: dst[k] = v
    return dst


def yaml_dump(data, level=0):
    lines = []
    for key, val in data.items():
        if isinstance(val, dict):
            lines.append(' ' * level + key + ':')
            lines.extend(yaml_dump(val, level + 2))
        else:
            s = str(val)
            # cfg.parse is a deliberately small parser without YAML escapes.
            if '\n' in s or ('\"' in s and "'" in s):
                raise ValueError('Configuration scalar cannot be round-tripped safely: ' + key)
            quote = "'" if '\"' in s else '\"'
            lines.append(' ' * level + key + ': ' + quote + s + quote)
    return lines


def usable(folder, patterns):
    return all(any(p.is_file() and p.stat().st_size > (1024 if p.suffix == '.onnx' else 0)
                   for p in folder.glob(pattern)) for pattern in patterns)


def model_files(name, folder):
    selected = set()
    for pattern in MODELS[name][2] + ['test_wavs/*.wav']:
        selected.update(p for p in folder.glob(pattern) if p.is_file() and '.cache' not in p.parts)
    return sorted(selected)


def sha256_file(path):
    h = hashlib.sha256()
    with open(str(path), 'rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()


def fetch_models(args, names, cache):
    result = {}
    for name in names:
        rel, repo, patterns = MODELS[name]
        candidates = [ROOT / rel, cache / rel]
        for source in args.model_source:
            candidates += [Path(source) / rel, Path(source) / Path(rel).name, Path(source)]
        found = next((d for d in candidates if usable(d, patterns)), None)
        if found is None:
            dest = cache / rel
            dest.mkdir(parents=True, exist_ok=True)
            repo = args.model_repo.get(name, repo)
            download_patterns = patterns + ['dict/**'] if name == 'tts' else patterns
            if not repo:
                # Official release CLI is used when no HF/ModelScope mirror exists (vocoder / spk).
                if name not in RELEASES:
                    raise RuntimeError('No download source for %s: supply --model-source containing it, '
                                       'or --model-repo %s=OWNER/REPO' % (name, name))
                tag, asset = RELEASES[name]
                if not shutil.which('gh'):
                    raise RuntimeError('Missing %s: supply --model-source containing it, '
                                       'or an existing gh CLI, or --model-repo %s=OWNER/REPO' % (asset, name))
                subprocess.run(['gh', 'release', 'download', tag, '--repo', 'k2-fsa/sherpa-onnx',
                                '--pattern', asset, '--dir', str(dest), '--skip-existing'], check=True)
            else:
                module = 'modelscope' if args.provider == 'modelscope' else 'huggingface_hub'
                if not importlib.util.find_spec(module):
                    package = module if module == 'modelscope' or sys.version_info >= (3, 9) else 'huggingface_hub<1.0'
                    subprocess.run([sys.executable, '-m', 'pip', 'install', '--only-binary=:all:',
                                    '--disable-pip-version-check', '--timeout', '30', package], check=True)
                if args.provider == 'modelscope':
                    from modelscope import snapshot_download
                    snapshot_download(repo, local_dir=str(dest), allow_patterns=download_patterns)
                else:
                    from huggingface_hub import snapshot_download, hf_hub_download
                    endpoints = [os.environ.get('HF_ENDPOINT', 'https://huggingface.co'), 'https://hf-mirror.com']
                    for endpoint in dict.fromkeys(endpoints):
                        try:
                            if name == 'vocoder' and repo == 'k2-fsa/sherpa-onnx-models':
                                downloaded = hf_hub_download(repo, 'vocoder-models/vocos-22khz-univ.onnx',
                                                             local_dir=str(dest), endpoint=endpoint, token=False)
                                shutil.copy2(downloaded, str(dest / 'vocos-22khz-univ.onnx'))
                            else:
                                snapshot_download(repo_id=repo, local_dir=str(dest), allow_patterns=download_patterns,
                                                  max_workers=1, endpoint=endpoint, token=False)
                            break
                        except Exception:
                            if endpoint == endpoints[-1]:
                                raise RuntimeError('Official model client could not fetch '+name+
                                                   '; provide --model-source or a reachable --provider/--model-repo')
                            say('download_retry', {'name':name, 'next':'same repository via mirror'})
            if not usable(dest, patterns):
                raise RuntimeError('Downloaded model incomplete: ' + name)
            found = dest
        result[name] = found
        say('model_ready', {'name': name, 'source': str(found), 'bytes': sum(p.stat().st_size for p in model_files(name, found))})
    return result


def model_profiles(base, models):
    b = str(base).replace('\\', '/')
    prof = {}
    def path(name): return b + '/' + MODELS[name][0]
    if 'asr' in models:
        d = path('asr'); prof['asr_profiles'] = {'sherpa': dict(dir=d, encoder=d+'/encoder.int8.onnx', decoder=d+'/decoder.onnx', joiner=d+'/joiner.int8.onnx', tokens=d+'/tokens.txt', num_threads='1')}
    if 'kws' in models:
        prof['kws_profiles'] = {'sherpa': {'model_dir': path('kws'), 'keywords_file': b + '/kws/keywords.txt', 'num_threads':'1'}}
    if 'vad' in models:
        prof['vad_profiles'] = {'sherpa': {'model':path('vad')+'/onnx/model.onnx', 'num_threads':'1'}}
    if 'tts' in models and 'vocoder' in models:
        d=path('tts'); prof['tts_profiles'] = {'sherpa':dict(kind='matcha', model=d+'/model-steps-3.onnx', tokens=d+'/tokens.txt', lexicon=d+'/lexicon.txt', vocoder=path('vocoder')+'/vocos-22khz-univ.onnx', num_threads='1')}
    if 'spk' in models:
        # 只改路径与线程数；门限/登记库/回复话术仍取 vertify.yaml（deep-merge 只覆盖给出的键）
        prof['spk_profiles'] = {'sherpa': {'model_dir': path('spk'), 'num_threads': '1'}}
    return prof


def collect_code():
    # Explicit dependency roots; never ship credential configs, archive, vendor builds or unrelated vision.
    for rel in ['server', 'split', 'tts/models/edge', 'vad/webrtc', 'upper', 'klar']:
        folder = ROOT / rel
        if not folder.exists(): continue
        for p in folder.rglob('*'):
            if not p.is_file() or any(x in p.parts for x in ['__pycache__','.git','archive','tests']): continue
            if p.suffix not in ['.py','.so','.json','.txt','.service','.bin']: continue
            yield p, p.relative_to(ROOT).as_posix()
    yield ROOT / 'vad/rms.py', 'vad/rms.py'
    for p in (ROOT / 'kws').glob('*.py'):
        yield p, p.relative_to(ROOT).as_posix()
    yield ROOT / 'kws/keywords.txt', 'kws/keywords.txt'


def backup_and_extract(t, base, archive, stamp):
    say('backup', t.py("""from pathlib import Path
import shutil,tarfile,json,os
b=Path(%r); backup=b.parent/('lang-backup-'+%r); backup.mkdir(parents=True,exist_ok=False)
for rel in ['server','config','split','tts/models/edge','vad/webrtc','vad/rms.py','upper','klar','kws/router.py','kws/keywords.txt']:
 src=b/rel; dst=backup/rel
 if src.is_dir(): shutil.copytree(str(src),str(dst))
 elif src.is_file(): dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(str(src),str(dst))
u=Path('/etc/systemd/system')
for name in %r:
 p=u/(name+'.service')
 if p.exists(): shutil.copy2(str(p),str(backup/p.name))
b.mkdir(parents=True,exist_ok=True)
with tarfile.open(%r) as f:
 for m in f.getmembers():
  if m.name.startswith('/') or '..' in Path(m.name).parts or m.issym() or m.islnk(): raise ValueError('unsafe archive member')
 f.extractall(str(b))
print(str(backup))
""" % (str(base), stamp, UNITS, str(archive))))


def verify(t, base, names):
    # Deployment validation must never construct or run models (OOM constraint).
    for name in names:
        rel, _, patterns = MODELS[name]
        code = "from pathlib import Path; p=Path(%r); patterns=%r; " % (str(base)+'/'+rel, patterns)
        code += "assert all(any(x.is_file() and x.stat().st_size > (1024 if x.suffix == '.onnx' else 0) for x in p.glob(pattern)) for pattern in patterns), 'incomplete model files'; print('FILES_OK_NO_MODEL_EXECUTION')"
        say('verify_'+name, t.py(code, 60).strip())


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--apply', action='store_true')
    p.add_argument('--target', choices=['local','board'], default='local')
    p.add_argument('--host', default='192.168.1.113'); p.add_argument('--user', default='root')
    p.add_argument('--port', type=int, default=22); p.add_argument('--identity')
    p.add_argument('--transport', choices=['auto','ssh','paramiko'], default='auto')
    p.add_argument('--ask-password', action='store_true'); p.add_argument('--accept-new-host-key', action='store_true')
    p.add_argument('--python', default='python3', help='target Python interpreter')
    p.add_argument('--profile', choices=['online','local'], default='online')
    p.add_argument('--activate', action='store_true', help='explicitly replace chain choices')
    p.add_argument('--models', choices=['selected','all','none'], default='all')
    p.add_argument('--model-source', action='append', default=[], help='existing model tree; repeatable')
    p.add_argument('--model-repo', action='append', default=[], metavar='NAME=OWNER/REPO')
    p.add_argument('--provider', choices=['huggingface','modelscope'], default='huggingface')
    p.add_argument('--work-dir', type=Path, default=Path(os.environ.get('LANG_DEPLOY_WORK', str(DEFAULT_WORK))))
    p.add_argument('--root', help='target lang directory (default local source or /mnt/data/lang)')
    p.add_argument('--no-restart', action='store_true'); p.add_argument('--skip-verify', action='store_true')
    args = p.parse_args(argv)
    if args.profile == 'local' and args.activate:
        p.error('Local model activation is disabled after OOM; prepare files without --activate')
    args.model_repo = dict(x.split('=',1) for x in args.model_repo)
    base = args.root or ('/mnt/data/lang' if args.target == 'board' else str(ROOT))
    if args.target == 'board' and (not base.startswith('/') or '..' in Path(base).parts): p.error('--root must be an absolute POSIX directory')
    names = list(MODELS) if args.models == 'all' or (args.models == 'selected' and args.profile == 'local') else []
    t = Target(args)
    try:
        facts = inspect_target(t, base); say('probe', facts)
        if facts['python'] < [3,8,0]: raise RuntimeError('Python 3.8+ required')
        required = {'wyoming':'wyoming'}
        if args.profile == 'online': required['edge_tts']='edge-tts'
        if names: required.update(numpy='numpy', sherpa_onnx='sherpa-onnx==2.0.1')
        missing = [pkg for mod,pkg in required.items() if not facts['packages'].get(mod)]
        say('plan', {'apply':args.apply,'target':args.target,'root':base,'profile':args.profile,
                     'activate':args.activate,'models':names,'missing_packages':missing,
                     'decoder':'reuse existing decoder/mpg123/ffmpeg','external':['NPU ERNIE/Qwen compiled artifacts'],
                     'opt_in':['spk 声纹（CAM++）装文件但**不入链**：chain.spk 默认 none，'
                               '用 router/kws_route 的 verify 或 /api/v1/spk/* 点名'],
                     'steps':['probe','binary-only missing dependencies','official-client model acquisition','validate files','backup','merge configuration','transfer dependency tree','file-only checks (no model construction)','systemd units','health']})
        if not args.apply: return 0
        if facts['free'] < (1024**3 if names else 128*1024**2): raise RuntimeError('Insufficient free space incl. staging/backup reserve')
        args.work_dir.mkdir(parents=True,exist_ok=True)
        models = fetch_models(args,names,args.work_dir/'downloads')
        stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        if missing:
            say('install_missing',missing)
            say('pip',t.run([args.python if t.remote else sys.executable,'-m','pip','install','--only-binary=:all:','--no-cache-dir','--disable-pip-version-check','--timeout','30']+missing,600))
        # Read actual target before mutation; only fill absent values from source.
        sys.path.insert(0,str(ROOT/'server'))
        from cfg import Config, parse
        template = ROOT / 'config-examples' / 'online.yaml'
        source = parse(template.read_text(encoding='utf-8')) if template.is_file() else {}
        merge(source, Config(str(ROOT/'config')).data)
        def scrub_credentials(obj):
            for key, value in obj.items():
                if isinstance(value, dict):
                    scrub_credentials(value)
                elif key.lower() in ('api_key','token','password','secret','access_token','client_secret','authorization'):
                    obj[key] = ''
        # Only target-owned credentials may survive the merge. Never ship workstation keys.
        scrub_credentials(source)
        try: existing=load_cfg(t,base)
        except RuntimeError:
            exists=json.loads(t.py('import os,json; print(json.dumps(os.path.exists(%r)))' % (str(base)+'/config')))
            if exists: raise
            existing={}
        config=merge(source,existing)
        merge(config,model_profiles(base,models))
        if args.profile == 'online':
            # Install local model files, but keep every model-bearing stage online/off.
            config.setdefault('chain', {}).update({'asr':'remote-api','llm':'openai','tts':'edge',
                                                   'vad':'none','kws':'passthrough','tokenizer':'tts_split',
                                                   'spk':'none'})
        elif args.activate:
            raise RuntimeError('Local model activation is disabled after OOM; use the online profile')
        if args.target=='board':
            def remap(obj):
                for k,v in obj.items():
                    if isinstance(v,dict): remap(v)
                    elif isinstance(v,str) and re.match(r'^[A-Za-z]:[/\\]',v):
                        norm=v.replace('\\','/'); marker='/lang/'
                        if marker not in norm: raise RuntimeError('Unmappable host path in configuration key: '+k)
                        obj[k]=str(base)+'/'+norm.split(marker,1)[1]
            remap(config)
        # Keep decoder exactly if available; only resolve an unusable miniaudio request.
        edge=config.get('online',{}).get('edge',{})
        if edge.get('decoder')=='miniaudio' and not facts['packages']['miniaudio']:
            if facts['tools']['mpg123']: edge['decoder']='mpg123'
            elif facts['tools']['ffmpeg']: edge['decoder']='ffmpeg'
            else: raise RuntimeError('Need existing mpg123/ffmpeg or binary miniaudio for Edge audio decoding')
        archive=args.work_dir/('lang-'+stamp+'.tar')
        with tarfile.open(str(archive),'w') as tar:
            for src,rel in collect_code(): tar.add(str(src),arcname=rel,recursive=False)
            for name,d in models.items():
                for f in model_files(name,d):
                    tar.add(str(f),arcname=MODELS[name][0]+'/'+f.relative_to(d).as_posix(),recursive=False)
        target_archive=str(Path(base).parent/('lang-stage-'+stamp+'.tar')) if not t.remote else '/mnt/data/lang-stage-'+stamp+'.tar'
        t.put(archive,target_archive)
        expected=sha256_file(archive)
        actual=t.py("import hashlib; h=hashlib.sha256(); f=open(%r,'rb'); [h.update(b) for b in iter(lambda:f.read(1048576),b'')]; f.close(); print(h.hexdigest())" % target_archive).strip()
        if actual!=expected: raise RuntimeError('Archive SHA256 mismatch; target not modified')
        backup_and_extract(t,base,target_archive,stamp)
        encoded=base64.b64encode(('\n'.join(yaml_dump(config))+'\n').encode('utf-8')).decode('ascii')
        t.py("from pathlib import Path; import base64,os; d=Path(%r); d.mkdir(parents=True,exist_ok=True); p=d/'zz-deployment.yaml'; q=p.with_suffix('.tmp'); q.write_bytes(base64.b64decode(%r)); os.chmod(str(q),0o600); os.replace(str(q),str(p))" % (str(base)+'/config',encoded))
        # Syntax checks without pycache writes, including on plan-friendly source trees.
        say('syntax',t.py("from pathlib import Path; b=Path(%r); files=list((b/'server').rglob('*.py')); [compile(p.read_bytes(),str(p),'exec') for p in files]; print(len(files))" % str(base)))
        if names and not args.skip_verify: verify(t,base,names)
        if t.remote and not args.no_restart:
            if not facts['tools']['systemctl']: raise RuntimeError('systemd missing; use --no-restart for code-only deployment')
            for unit in UNITS:
                # Preserve target-specific memory caps and environment/drop-ins in existing units.
                t.py("from pathlib import Path; import re; src=Path(%r); dst=Path(%r); text=src.read_text(); old=dst.read_text() if dst.exists() else ''; keep=[x for x in old.splitlines() if x.startswith(('Environment=','EnvironmentFile=','MemoryMax=')) and not x.startswith('Environment=LANG_CONFIG_DIR=')]; text=re.sub(r'(?m)^MemoryMax=.*\\n','',text) if any(x.startswith('MemoryMax=') for x in keep) else text; keep=list(dict.fromkeys(x for x in keep if x not in text.splitlines())); text=text.replace('[Service]','[Service]\\n'+'\\n'.join(keep)); text=text.replace('/mnt/data/lang',%r); dst.write_text(text)" %
                     (str(base)+'/server/deploy/'+unit+'.service','/etc/systemd/system/'+unit+'.service',str(base)))
            t.run(['systemctl','daemon-reload'])
            t.run(['systemctl','enable']+UNITS)
            t.run(['systemctl','restart']+UNITS)
            health=None
            for attempt in range(20):
                try:
                    health=json.loads(t.py("import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:%s/health',timeout=5).read().decode())" % config.get('server',{}).get('port','18091'))); break
                except RuntimeError: time.sleep(1)
            if health is None: raise RuntimeError('HTTP health timeout; backup retained')
            say('services',t.run(['systemctl','is-active']+UNITS).split())
            say('health',{'http':True,'chain':config.get('chain'), 'stages_error':health.get('stages_error',{})})
        say('complete',{'root':base,'archive_sha256':expected,'activated':args.activate})
        return 0
    finally: t.close()


if __name__=='__main__':
    try: sys.exit(main())
    except (Exception,KeyboardInterrupt) as exc:
        print('DEPLOY_FAILED: %s' % exc,file=sys.stderr); sys.exit(1)
