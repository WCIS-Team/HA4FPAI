"""Emit a parse-only toml cloned from the vendor Qwen3 decoder toml, pointing at another
fmodel dir and jr_path, with a distinct net_name."""
import argparse
import os
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE = os.path.join('S:', os.sep, 'apps', 'llm', 'agentwork', 'qwen3', 'compile',
                    'Qwen3-0.6B_decoder.toml')
ROOT = os.path.join('S:', os.sep, 'apps', 'llm', 'agentwork', 'qwen3', 'compile')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--name', required=True)
    ap.add_argument('--fm', required=True, help='fmodel subdir name, e.g. qwen3_2L')
    ap.add_argument('--jr', default=None, help='jr_path subdir name, defaults to name')
    args = ap.parse_args()

    jr = args.jr or args.name
    t = open(BASE, encoding='utf-8').read()
    t = t.replace('Qwen3-0.6B', args.name)
    t = t.replace('./fmodel/qwen3_exported/', './fmodel/%s/' % args.fm)
    t = t.replace('./jr_path/decode/', './jr_path/%s/' % jr)
    out = os.path.join(ROOT, '%s_parse.toml' % args.name)
    open(out, 'w', encoding='utf-8', newline='\n').write(t)
    print('wrote', out)


if __name__ == '__main__':
    main()
