"""NEXT-02: size accounting of the trimmed source, installed venv and frontend."""
import json
import os
import subprocess
from pathlib import Path


def du(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def du_kb(path):
    out = subprocess.run(['du', '-sk', path], capture_output=True, text=True)
    return int(out.stdout.split()[0]) * 1024 if out.stdout else 0


sp = '/venv/lib/python3.14/site-packages'
top = []
for entry in os.scandir(sp):
    if entry.is_dir(follow_symlinks=False) and not entry.name.endswith('.dist-info'):
        size = du_kb(entry.path)
        top.append({'name': entry.name, 'bytes': size})
top.sort(key=lambda x: -x['bytes'])
frontend = [t for t in top if 'frontend' in t['name'].lower()]

result = {
    'source_tree_bytes': du('/src-branch'),
    'installed_package_bytes': du(sp + '/homeassistant'),
    'venv_total_bytes': du_kb('/venv'),
    'site_packages_total_bytes': du_kb(sp),
    'top_site_packages': top[:15],
    'frontend_package': frontend,
    'homeassistant_frontend_version': next(
        (p.name for p in Path(sp).glob('home_assistant_frontend-*')), None),
    'boundary': 'Disk sizes on this Linux x86_64 lab environment. Not RSS, not startup peak, '
                'not board figures.',
}
Path('/tmp/size-results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'source_mb': round(result['source_tree_bytes'] / 1048576, 1),
                  'venv_mb': round(result['venv_total_bytes'] / 1048576, 1),
                  'site_packages_mb': round(result['site_packages_total_bytes'] / 1048576, 1),
                  'frontend': frontend,
                  'top3': top[:3]}, ensure_ascii=False))
