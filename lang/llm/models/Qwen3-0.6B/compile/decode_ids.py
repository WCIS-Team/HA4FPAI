"""Decode a token-id list with a HuggingFace tokenizer.json (Rust `tokenizers` bindings).

Usage:
  python decode_ids.py --tokens compile/q2_chat/tokens.txt --tokenizer compile/fmodel/T0/tokenizer.json
  python decode_ids.py --tokens compile/gen_fixed35/steps.json --field fed --tokenizer ...
  python decode_ids.py --ids "1427,124674" --tokenizer ...

Writes UTF-8 to stdout regardless of the console codepage (this machine's ACP is 936 and a
GBK console mangles or drops Chinese), and prints each generated token's decoded surface form
so a repetition collapse is visible without reading ids.
"""
import argparse
import json
import os
import sys

from tokenizers import Tokenizer

sys.stdout.reconfigure(encoding='utf-8', errors='replace')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tokenizer', required=True)
    ap.add_argument('--tokens', default='', help='file: one id per line, or a json list')
    ap.add_argument('--ids', default='', help='comma separated ids')
    ap.add_argument('--prompt-len', type=int, default=0, help='how many leading ids are the prompt')
    ap.add_argument('--steps', default='', help='host_gen.py steps.json: decode fed[] and show per-step')
    args = ap.parse_args()

    tok = Tokenizer.from_file(args.tokenizer)

    if args.steps:
        rec = json.load(open(args.steps, encoding='utf-8'))
        fed = [r['fed'] for r in rec]
        amax = [r['argmax'] for r in rec]
        print('N steps', len(rec))
        print('FED   ', fed)
        print('ARGMAX', amax)
        print('TEXT(fed stream)=%r' % tok.decode(fed, skip_special_tokens=False))
        print('TEXT(fed tail)=%r' % tok.decode(fed[args.prompt_len:], skip_special_tokens=False))
        print('TEXT(argmax stream)=%r' % tok.decode(amax, skip_special_tokens=False))
        tail = amax[args.prompt_len:]
        uniq = []
        for t in tail:
            if not uniq or uniq[-1][0] != t:
                uniq.append([t, 0])
            uniq[-1][1] += 1
        print('argmax tail run-length:', [(t, tok.decode([t]), n) for t, n in uniq])
        return

    ids = []
    if args.tokens:
        txt = open(args.tokens, encoding='utf-8').read().strip()
        if txt.startswith('['):
            ids = [int(x) for x in json.loads(txt)]
        else:
            ids = [int(x) for x in txt.replace(',', '\n').split() if x.strip()]
    elif args.ids:
        ids = [int(x) for x in args.ids.split(',') if x.strip()]
    if not os.path.exists(args.tokenizer):
        print('tokenizer missing', args.tokenizer)
    print('N=%d' % len(ids))
    print('IDS  ', ids)
    print('PROMPT', repr(tok.decode(ids[:args.prompt_len], skip_special_tokens=False)))
    print('GEN  ', repr(tok.decode(ids[args.prompt_len:], skip_special_tokens=False)))
    print('ALL  ', repr(tok.decode(ids, skip_special_tokens=False)))


if __name__ == '__main__':
    main()
