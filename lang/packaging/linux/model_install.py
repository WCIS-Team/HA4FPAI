#!/usr/bin/env python3
"""Download model files with the official HF client. Never load or execute models."""
import argparse
import datetime
import fcntl
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys


def safe_child(root, relative):
    rel = PurePosixPath(relative)
    if rel.is_absolute() or '..' in rel.parts or not rel.parts:
        raise ValueError('Unsafe manifest path')
    target = root.joinpath(*rel.parts)
    if root.resolve() not in target.resolve().parents:
        raise ValueError('Manifest path escapes model root')
    return target


def complete(folder, patterns):
    return all(any(p.is_file() and p.stat().st_size > (1024 if p.suffix == '.onnx' else 0)
                   for p in folder.glob(pattern)) for pattern in patterns)


def save_status(dest, status):
    temp = dest / '.download-status.tmp'
    temp.write_text(json.dumps(status, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(str(temp), str(dest / 'download-status.json'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--dest', type=Path, required=True)
    parser.add_argument('--check-only', action='store_true', help='Check files without network or model imports')
    args = parser.parse_args(argv)
    document = json.loads(args.manifest.read_text(encoding='utf-8'))
    if document.get('schema') != 1:
        raise ValueError('Unsupported manifest schema')
    args.dest.mkdir(parents=True, exist_ok=True)
    with (args.dest / '.download.lock').open('a') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Another download is active; nothing to do', flush=True)
            return 0
        status = {'model_execution': False, 'models': {}, 'updated_utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}
        failures = []
        for model in document['models']:
            name = model['id']
            folder = safe_child(args.dest, model['path'])
            required = model['required']
            if complete(folder, required):
                status['models'][name] = 'files-present'
                print(name + ': files already present (not loaded)', flush=True)
                save_status(args.dest, status)
                continue
            if args.check_only:
                status['models'][name] = 'incomplete'
                failures.append(name)
                save_status(args.dest, status)
                continue
            if shutil.disk_usage(str(args.dest)).free < 1024 ** 3:
                raise RuntimeError('Need at least 1 GiB free space for model files and download reserve')
            folder.mkdir(parents=True, exist_ok=True)
            # Preserve invalid placeholders, but do not let cache metadata hide zero-byte files.
            for path in folder.rglob('*.onnx'):
                if path.stat().st_size <= 1024:
                    path.rename(path.with_name(path.name + '.invalid-' + datetime.datetime.now().strftime('%Y%m%d%H%M%S%f')))
            os.environ.setdefault('HF_HUB_DISABLE_XET', '1')
            os.environ.setdefault('HF_HUB_ENABLE_HF_TRANSFER', '0')
            os.environ.setdefault('HF_HUB_ETAG_TIMEOUT', '15')
            os.environ.setdefault('HF_HUB_DOWNLOAD_TIMEOUT', '60')
            from huggingface_hub import snapshot_download
            endpoints = list(dict.fromkeys([os.environ.get('HF_ENDPOINT', 'https://huggingface.co'), 'https://hf-mirror.com']))
            downloaded = False
            for endpoint in endpoints:
                try:
                    print(name + ': downloading model files (one worker; no inference)', flush=True)
                    snapshot_download(repo_id=model['repo_id'], revision=model.get('revision', 'main'),
                                      local_dir=str(folder), allow_patterns=model['allow_patterns'],
                                      max_workers=1, token=False, endpoint=endpoint)
                    for source, target in model.get('copies', {}).items():
                        source_path = safe_child(folder, source)
                        target_path = safe_child(folder, target)
                        target_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(str(source_path), str(target_path))
                    if not complete(folder, required):
                        raise RuntimeError('Required files missing or empty')
                    downloaded = True
                    break
                except Exception as exc:
                    # Avoid emitting URLs, request headers or authentication details.
                    print(name + ': download attempt failed (' + type(exc).__name__ + ')', file=sys.stderr, flush=True)
            status['models'][name] = 'files-present' if downloaded else 'download-failed'
            if not downloaded:
                failures.append(name)
            save_status(args.dest, status)
        print(json.dumps({'files_ready': not failures, 'incomplete': failures, 'model_execution': False}), flush=True)
        return 1 if failures else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print('Model file preparation failed: ' + type(exc).__name__, file=sys.stderr)
        sys.exit(1)
