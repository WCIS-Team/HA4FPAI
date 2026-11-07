"""Verify retained upstream source without importing Home Assistant."""
import hashlib
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
audit = json.loads((root / 'cut/source-audit.json').read_text(encoding='utf-8'))
report = json.loads((root / 'cut/cut-report.json').read_text(encoding='utf-8'))
errors = []
for item in audit:
    path = root / item['path']
    if not path.is_file() or path.is_symlink():
        errors.append('Missing/not regular: ' + item['path'])
        continue
    if path.stat().st_size != item['bytes']:
        errors.append('Wrong size: ' + item['path'])
    elif hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
        errors.append('Modified: ' + item['path'])
components = root / 'homeassistant/components'
actual = {p.name for p in components.iterdir() if p.is_dir() and (p / 'manifest.json').is_file()}
if actual != set(report['retained_integrations']):
    errors.append('Integration directory set differs from cut report')
if errors:
    for error in errors[:30]:
        print(error)
    raise SystemExit(1)
print(json.dumps({'status':'passed','upstream_files':len(audit),
                  'integrations':len(actual),'modified_upstream_files':0,
                  'license_present':(root / 'LICENSE.md').is_file()}))
