"""NEXT-01b v2: import scan that also understands multi-line from-imports.

v1 scanned line by line and therefore MISSED bootstrap.py's
`from .components import ( ... search as search_pre_import, ... )` block, which
made `search` look deletable when it is not. v2 first joins parenthesised import
blocks, then matches.
"""
import json
import re
from pathlib import Path

ROOT = Path(r'S:\work\WCIS\workenv\tmp\ha-small-branch')
COMPONENTS = ROOT / 'homeassistant' / 'components'
OUT = Path(r'S:\work\WCIS\workenv\ha-small\results\next01-imports-v2.json')

CANDIDATES = ['mobile_app', 'plex', 'dhcp', 'ssdp', 'my', 'usage_prediction', 'go2rtc',
              'map_tiles', 'search', 'homeassistant_alerts', 'cloud', 'hassio', 'analytics',
              'google_assistant', 'alexa', 'cast', 'bluetooth', 'matter', 'zeroconf', 'usb',
              'network', 'energy', 'backup', 'blueprint', 'trace', 'system_health',
              'web_rtc', 'file_upload', 'image_upload', 'tag', 'infrared', 'labs']

PAREN_IMPORT = re.compile(r'from\s+([\w.]+)\s+import\s+\(([^)]*)\)', re.S)


def joined_imports(text: str) -> str:
    """Collapse parenthesised import blocks onto one line, keeping the module path."""
    def flat(match):
        module = match.group(1)
        names = ' '.join(part.strip().rstrip(',') for part in match.group(2).splitlines())
        return 'from ' + module + ' import ' + names
    return PAREN_IMPORT.sub(flat, text)


def scan():
    patterns = {}
    for name in CANDIDATES:
        patterns[name] = [
            re.compile(r'from\s+homeassistant\.components\.' + name + r'\b'),
            re.compile(r'from\s+\.components\s+import\b[^\n]*\b' + name + r'\b'),
            re.compile(r'from\s+homeassistant\.components\s+import\b[^\n]*\b' + name + r'\b'),
            re.compile(r'import\s+homeassistant\.components\.' + name + r'\b'),
        ]
    hits = {}
    for path in (ROOT / 'homeassistant').rglob('*.py'):
        rel = str(path.relative_to(ROOT))
        text = joined_imports(path.read_text(encoding='utf-8', errors='replace'))
        for name, pats in patterns.items():
            if rel.startswith('homeassistant/components/%s/' % name):
                continue
            if any(p.search(text) for p in pats):
                hits.setdefault(name, set()).add(rel)
    return hits


def main():
    hits = scan()
    rows = []
    for name in CANDIDATES:
        sites = sorted(hits.get(name, []))
        rows.append({'integration': name, 'sites': len(sites), 'imported_by': sites[:4]})
    safe = [r['integration'] for r in rows if r['sites'] == 0]
    blocked = [r for r in rows if r['sites'] > 0]
    result = {'candidates': len(CANDIDATES), 'safe_to_delete': safe,
              'requires_patch_or_keep': blocked,
              'note': 'v2 fixes v1 which scanned line-by-line and missed multi-line '
                      'parenthesised imports (that is how bootstrap.py preloads `search`).'}
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('safe_to_delete:', safe)
    print()
    for r in sorted(blocked, key=lambda x: -x['sites']):
        print('%-24s %2d  %s' % (r['integration'], r['sites'], r['imported_by'][:2]))


if __name__ == '__main__':
    main()