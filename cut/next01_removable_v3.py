"""NEXT-01b v3: decide removability with ALL three constraints.

A retained integration X may be deleted only if:
  (a) no retained integration lists X in manifest "dependencies" (hard dep),
  (b) no static import references X (multi-line aware),
  (c) bootstrap.py does not pre-import X.
v1 (line-wise imports) and v2 (imports only) each produced a wrong answer; this
version is the one that matches how the original closure was computed.
"""
import json
import re
from pathlib import Path

import sys
ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r'S:\work\WCIS\workenv\tmp\ha-small-branch')
COMPS = ROOT / 'homeassistant' / 'components'
OUT = Path(r'S:\work\WCIS\workenv\ha-small\results\next01-imports-v3.json')

CANDIDATES = ['mobile_app', 'plex', 'dhcp', 'ssdp', 'my', 'usage_prediction', 'go2rtc',
              'map_tiles', 'homeassistant_alerts', 'search', 'matter', 'energy',
              'image_upload', 'cloud', 'analytics', 'google_assistant', 'alexa', 'cast',
              'zeroconf', 'usb', 'network', 'backup', 'blueprint', 'trace', 'system_health',
              'web_rtc', 'tag', 'infrared', 'labs', 'hassio', 'bluetooth']

PAREN = re.compile(r'from\s+([\w.]+)\s+import\s+\(([^)]*)\)', re.S)


def flatten(text: str) -> str:
    def sub(m):
        names = ' '.join(p.strip().rstrip(',') for p in m.group(2).splitlines())
        return 'from %s import %s' % (m.group(1), names)
    return PAREN.sub(sub, text)


def main():
    retained = sorted(p.name for p in COMPS.iterdir()
                      if p.is_dir() and (p / 'manifest.json').is_file())
    retained_set = set(retained)

    # (a) manifest hard dependencies
    hard_dep_users = {}
    for name in retained:
        try:
            manifest = json.loads((COMPS / name / 'manifest.json').read_text(encoding='utf-8'))
        except Exception:  # noqa: BLE001
            continue
        for dep in manifest.get('dependencies') or []:
            if dep in CANDIDATES and dep != name:
                hard_dep_users.setdefault(dep, set()).add(name)

    # (b) static imports
    import_users = {}
    pats = {n: re.compile(r'from\s+(?:homeassistant\.)?components[.\s]+' + n + r'\b')
            for n in CANDIDATES}
    pats_rel = {n: re.compile(r'from\s+\.components\s+import\b[^\n]*\b' + n + r'\b')
                for n in CANDIDATES}
    for path in (ROOT / 'homeassistant').rglob('*.py'):
        rel = path.relative_to(ROOT).as_posix()
        text = flatten(path.read_text(encoding='utf-8', errors='replace'))
        for name in CANDIDATES:
            if rel.startswith('homeassistant/components/%s/' % name):
                continue
            if pats[name].search(text) or pats_rel[name].search(text) \
                    or re.search(r'import\s+homeassistant\.components\.' + name + r'\b', text):
                import_users.setdefault(name, set()).add(rel)

    # (c) bootstrap pre-imports
    boot = (ROOT / 'homeassistant' / 'bootstrap.py').read_text(encoding='utf-8', errors='replace')
    boot_flat = flatten(boot)
    boot_users = {n: 'bootstrap.py' for n in CANDIDATES
                  if re.search(r'\b' + n + r'\b', boot_flat.split('DEFAULT_INTEGRATIONS')[0])}

    rows = []
    for name in CANDIDATES:
        if name not in retained_set:
            continue
        deps = sorted(hard_dep_users.get(name, []))
        imps = sorted(import_users.get(name, []))
        removable = not deps and not imps and name not in boot_users
        rows.append({'integration': name, 'removable': removable,
                     'manifest_dependents': deps[:5], 'import_sites': imps[:3],
                     'bootstrap': name in boot_users})

    safe = sorted(r['integration'] for r in rows if r['removable'])
    blocked = [r for r in rows if not r['removable']]
    result = {'retained_integrations': len(retained), 'candidates_checked': len(rows),
              'removable': safe, 'blocked': blocked,
              'rules': 'manifest hard dependencies (a) + multi-line-aware static imports (b) '
                       '+ bootstrap pre-imports (c)'}
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('removable:', safe)
    print()
    for r in sorted(blocked, key=lambda x: -(len(x['manifest_dependents']) + len(x['import_sites']))):
        print('%-22s deps=%s boot=%s imports=%s' % (r['integration'],
                                                    r['manifest_dependents'][:3],
                                                    r['bootstrap'], r['import_sites'][:1]))


if __name__ == '__main__':
    main()
