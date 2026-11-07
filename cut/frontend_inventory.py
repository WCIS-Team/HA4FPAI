"""NEXT-02b: composition of the bundled frontend package (read-only analysis)."""
import json
import os
from collections import Counter
from pathlib import Path

root = Path('/venv/lib/python3.14/site-packages/hass_frontend')
ext_bytes = Counter()
ext_count = Counter()
largest = []
files = 0
for dirpath, _dirs, names in os.walk(root):
    for name in names:
        p = Path(dirpath) / name
        try:
            size = p.stat().st_size
        except OSError:
            continue
        files += 1
        suffix = p.suffix or '(none)'
        ext_bytes[suffix] += size
        ext_count[suffix] += 1
        largest.append((size, str(p.relative_to(root))))

largest.sort(reverse=True)
top_dirs = Counter()
for dirpath, dirs, _names in os.walk(root):
    rel = Path(dirpath).relative_to(root)
    if len(rel.parts) <= 1:
        for d in dirs:
            top_dirs[d] = sum(f.stat().st_size for f in (Path(dirpath) / d).rglob('*') if f.is_file())

# Reference check: how many chunk files are named in any index/manifest/app js
names_on_disk = {n for _s, n in largest}
texts = []
for candidate in ['index.html', 'frontend_latest/index.html', 'frontend_es5/index.html']:
    p = root / candidate
    if p.is_file():
        texts.append(p.read_text(encoding='utf-8', errors='replace'))
referenced = 0
for n in names_on_disk:
    base = n.split('/')[-1]
    if any(base in t for t in texts):
        referenced += 1

result = {
    'root': str(root),
    'files': files,
    'total_bytes': sum(ext_bytes.values()),
    'by_extension': {k: {'files': ext_count[k], 'bytes': v} for k, v in ext_bytes.most_common(8)},
    'top_dirs': dict(top_dirs.most_common(10)),
    'largest_files': [{'bytes': s, 'path': p} for s, p in largest[:10]],
    'index_html_present': bool(texts),
    'chunk_files_referenced_from_index': referenced,
    'boundary': 'Composition only. Cutting assets means repacking the upstream frontend wheel; '
                'no asset was deleted.',
}
Path('/tmp/frontend-results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'files': files, 'total_mb': round(result['total_bytes'] / 1048576, 1),
                  'top_dirs_mb': {k: round(v / 1048576, 1) for k, v in list(top_dirs.most_common(6))},
                  'by_ext': {k: round(v['bytes'] / 1048576, 1) for k, v in result['by_extension'].items()}},
                 ensure_ascii=False))
