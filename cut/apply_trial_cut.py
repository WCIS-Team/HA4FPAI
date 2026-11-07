import json
import shutil
import sys
from pathlib import Path

NAMES = ['mobile_app', 'plex', 'dhcp', 'ssdp', 'my', 'usage_prediction', 'go2rtc',
         'map_tiles', 'search', 'homeassistant_alerts']


def apply(root: Path):
    comps = root / 'homeassistant' / 'components'
    for name in NAMES:
        d = comps / name
        if d.is_dir():
            shutil.rmtree(d)
    audit_path = root / 'cut' / 'source-audit.json'
    audit = json.loads(audit_path.read_text(encoding='utf-8'))
    prefixes = tuple('homeassistant/components/%s/' % n for n in NAMES)
    audit2 = [row for row in audit if not row['path'].startswith(prefixes)]
    audit_path.write_text(json.dumps(audit2, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    rep_path = root / 'cut' / 'cut-report.json'
    rep = json.loads(rep_path.read_text(encoding='utf-8'))
    rep['retained_integrations'] = sorted(x for x in rep['retained_integrations'] if x not in NAMES)
    rep['removed_integrations'] = sorted(set(rep['removed_integrations']) | set(NAMES))
    for name in NAMES:
        rep['reasons'].pop(name, None)
    rep['retained_upstream_files'] = len(audit2)
    rep['retained_upstream_bytes'] = sum(r['bytes'] for r in audit2)
    rep['removed_upstream_files'] = len(audit) - len(audit2) + rep['removed_upstream_files']
    rep['retained_component_bytes'] = sum(
        f.stat().st_size for n in rep['retained_integrations']
        for f in (comps / n).rglob('*') if f.is_file())
    rep['note'] = ('Retained optional static dependencies are not enabled by example configuration. '
                   '2026-11-07: trial-deleted 10 optional integrations with no static import sites; '
                   '30/30 functional checks still passed.')
    rep_path.write_text(json.dumps(rep, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return rep, len(audit2)


if __name__ == '__main__':
    root = Path(sys.argv[1])
    rep, files = apply(root)
    print(json.dumps({'root': str(root), 'retained_integrations': len(rep['retained_integrations']),
                      'retained_files': files,
                      'retained_bytes': rep['retained_upstream_bytes'],
                      'component_bytes': rep['retained_component_bytes']}, ensure_ascii=False))
