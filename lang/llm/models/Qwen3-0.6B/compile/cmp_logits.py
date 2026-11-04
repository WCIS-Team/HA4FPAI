"""Compare one icraft Host logits vector against one HF reference vector.

Usage:
  python cmp_logits.py --icraft <10105.ftmp> --icraft-format sft --hf <logits_zeropast_1427_1.npy>
  python cmp_logits.py --icraft <10105.ftmp> --icraft-format sfb --hf <logits_prefix_1427.npy>

sft = the whitespace-separated text dump icraft-run writes with --dump_format SFT,
sfb = the raw little-endian dump from --dump_format SFB. Measured size says fp32: the logits of a
151936-entry vocab dump as 607744 B even though the json declares @fp(16), so the dtype is
inferred from the byte count instead of assumed.
"""
import argparse
import os
import sys

import numpy as np

sys.stdout.reconfigure(encoding='utf-8', errors='replace')


def load_icraft(path, fmt):
    if fmt == 'sft':
        return np.asarray([float(t) for t in open(path, encoding='utf-8', errors='replace').read().split()],
                          dtype=np.float32)
    if fmt == 'npy':
        return np.load(path).reshape(-1).astype(np.float32)
    size = os.path.getsize(path)
    if size % 4 == 0:
        return np.fromfile(path, dtype='<f4').astype(np.float32)
    return np.fromfile(path, dtype='<f2').astype(np.float32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--icraft', required=True)
    ap.add_argument('--icraft-format', choices=['sft', 'sfb', 'npy'], required=True)
    ap.add_argument('--hf', required=True)
    ap.add_argument('--topk', type=int, default=100)
    args = ap.parse_args()

    a = load_icraft(args.icraft, args.icraft_format)
    b = np.asarray(np.load(args.hf)).reshape(-1).astype(np.float32)
    print('icraft n', a.size, 'finite', int(np.isfinite(a).sum()), 'range %.3f..%.3f' % (a.min(), a.max()))
    print('hf     n', b.size, 'finite', int(np.isfinite(b).sum()), 'range %.3f..%.3f' % (b.min(), b.max()))
    if a.size != b.size:
        raise SystemExit('vocab length differs')

    ia, ib = int(np.argmax(a)), int(np.argmax(b))
    ra = int((b > b[ia]).sum())
    rb = int((a > a[ib]).sum())
    print('argmax icraft %d (hf rank %d) | argmax hf %d (icraft rank %d) | agree %s'
          % (ia, ra, ib, rb, ia == ib))

    d = a - b
    print('diff maxabs %.4f  meanabs %.4f  rms %.4f' % (np.abs(d).max(), np.abs(d).mean(),
                                                       float(np.sqrt((d ** 2).mean()))))
    print('corr %.6f' % np.corrcoef(a, b)[0, 1])

    ta = set(np.argsort(-a)[:args.topk].tolist())
    tb = set(np.argsort(-b)[:args.topk].tolist())
    print('top%d overlap %d (%.1f%%)' % (args.topk, len(ta & tb), 100.0 * len(ta & tb) / args.topk))
    print('icraft top10', np.argsort(-a)[:10].tolist())
    print('hf     top10', np.argsort(-b)[:10].tolist())
    order_b = np.argsort(-b)
    print('hf top5 values', [round(float(b[i]), 3) for i in order_b[:5]])
    print('icraft at hf-top5', [(int(i), round(float(a[i]), 3), int((a > a[i]).sum()))
                                for i in order_b[:5]])


if __name__ == '__main__':
    main()
