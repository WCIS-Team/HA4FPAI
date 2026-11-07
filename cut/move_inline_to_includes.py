import re
import sys
from pathlib import Path

DOMAINS = {'automation': 'automations.yaml', 'script': 'scripts.yaml', 'scene': 'scenes.yaml'}


def move_block(text: str, domain: str):
    lines = text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln == '%s:' % domain)
    except StopIteration:
        return text, None
    end = start + 1
    while end < len(lines) and (lines[end].startswith('  ') or lines[end] == ''):
        if lines[end] == '' and end + 1 < len(lines) and not lines[end + 1].startswith('  '):
            break
        end += 1
    body = [ln[2:] if ln.startswith('  ') else ln for ln in lines[start + 1:end]]
    new_lines = lines[:start] + ['%s: !include %s' % (domain, DOMAINS[domain])] + lines[end:]
    return '\n'.join(new_lines) + '\n', '\n'.join(body).strip() + '\n'


def process(config: Path, out_dir: Path, merge: bool):
    text = config.read_text(encoding='utf-8')
    moved = {}
    for domain in DOMAINS:
        text, body = move_block(text, domain)
        if body:
            moved[domain] = body
    config.write_text(text, encoding='utf-8')
    for domain, body in moved.items():
        target = out_dir / DOMAINS[domain]
        if merge and target.exists():
            current = target.read_text(encoding='utf-8')
            ids = re.findall(r"- id: '?([\w-]+)", body)
            if any(i in current for i in ids):
                print('skip (already present)', target)
                continue
            target.write_text(body + current, encoding='utf-8')
            print('merged into', target)
        else:
            target.write_text(body, encoding='utf-8')
            print('wrote', target)
    return sorted(moved)


if __name__ == '__main__':
    mode = sys.argv[1]
    if mode == 'runtime':
        print('runtime moved:', process(Path('/runtime/configuration.yaml'), Path('/runtime'), merge=True))
    elif mode == 'trees':
        for root in [Path(r'S:\work\WCIS\workenv\tmp\ha-small-branch\examples'),
                     Path(r'S:\work\WCIS\repos\ha-small\examples')]:
            print(root, 'moved:', process(root / 'configuration.yaml', root, merge=False))
