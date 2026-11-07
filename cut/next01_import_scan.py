"""NEXT-01b: prove which retained-but-optional integrations are imported by other
retained code, i.e. cannot be deleted without patching upstream imports."""
import json
import re
from pathlib import Path

root = Path(r'S:\work\WCIS\workenv\tmp\ha-small-branch')
components = root / 'homeassistant' / 'components'
candidates = ['cloud', 'hassio', 'analytics', 'mobile_app', 'google_assistant', 'alexa',
              'plex', 'cast', 'bluetooth', 'matter', 'zeroconf', 'dhcp', 'ssdp', 'usb',
              'network', 'energy', 'my', 'usage_prediction', 'tag', 'blueprint', 'backup',
              'go2rtc', 'web_rtc', 'file_upload', 'image_upload', 'map_tiles', 'search',
              'trace', 'system_health', 'homeassistant_alerts', 'infrared', 'labs']

patterns = {}
for name in candidates:
    patterns[name] = [re.compile(r'from homeassistant\.components\.' + name + r'[ .]'),
                      re.compile(r'from homeassistant\.components import .*\b' + name + r'\b'),
                      re.compile(r'import homeassistant\.components\.' + name + r'\b')]

hits = {}
for path in list((root / 'homeassistant').rglob('*.py')):
    try:
        text = path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        continue
    rel = str(path.relative_to(root))
    for name, pats in patterns.items():
        if rel.startswith('homeassistant/components/%s/' % name):
            continue
        if any(p.search(text) for p in pats):
            hits.setdefault(name, []).append(rel)

report = []
for name in candidates:
    files = hits.get(name, [])
    report.append({'integration': name, 'imported_by': sorted(files), 'import_sites': len(files),
                   'requires_patch_to_delete': len(files) > 0})
clean = [r['integration'] for r in report if r['import_sites'] == 0]
blocked = [r for r in report if r['import_sites'] > 0]
result = {'candidates': len(candidates), 'deletable_without_patch': clean,
          'requires_patch': [{'integration': r['integration'], 'sites': r['import_sites'],
                              'examples': r['imported_by'][:3]} for r in blocked],
          'boundary': 'Static import scan of the retained tree only; runtime config/registry '
                      'references not exhaustively traced.'}
Path(r'S:\work\WCIS\workenv\ha-small\results\next01-imports.json').write_text(
    json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'deletable_without_patch': clean,
                  'requires_patch_count': len(blocked),
                  'top': sorted(((r['import_sites'], r['integration']) for r in blocked), reverse=True)[:8]},
                 ensure_ascii=False))
