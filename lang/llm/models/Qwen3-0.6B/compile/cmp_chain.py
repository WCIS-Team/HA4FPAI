"""Per-step fidelity curve: icraft Host chain logits vs HF teacher-forced logits.

The 43-step free run degenerated ('ast'/./\n loops), which has two possible owners: the refeed chain
accumulating error, or the vendored decode-only convention itself. Teacher-forcing the *same* token
sequence through HF fp32 (ref_hf.py --simulate --teacher) and comparing every step separates them:
flat corr ~0.999 all the way = chain is faithful, the garbage is the convention/model; corr decaying
with step count = the refeed is the bug.

Usage (after pulling the HF dumps next to the chain):
  python cmp_chain.py --chain compile/gen_chat32 --ref D:/yzd/WCIS/MOD/qwen3/logits_teach
"""
import argparse
import os
import sys

import numpy as np

sys.stdout.reconfigure(encoding='utf-8', errors='replace')


def corr(a, b):
    a = a - a.mean()
    b = b - b.mean()
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--chain', required=True, help='dir holding stepN/logits.npy')
    ap.add_argument('--ref', required=True, help='dir holding logits_teach_stepN.npy')
    ap.add_argument('--topk', type=int, default=100)
    args = ap.parse_args()

    print('step  argmaxIC  argmaxHF  same  corr      meanabs  top%d_ov  rangeIC' % args.topk)
    rows = []
    for n in range(200):
        ic = os.path.join(args.chain, 'step%d' % n, 'logits.npy')
        hf = os.path.join(args.ref, 'logits_teach_step%d.npy' % n)
        if not (os.path.exists(ic) and os.path.exists(hf)):
            break
        a = np.load(ic).reshape(-1).astype(np.float64)
        b = np.load(hf).reshape(-1).astype(np.float64)
        ai, bi = int(a.argmax()), int(b.argmax())
        ov = len(set(np.argsort(a)[-args.topk:]) & set(np.argsort(b)[-args.topk:]))
        c = corr(a, b)
        ma = float(np.abs(a - b).mean())
        rows.append((n, ai, bi, c, ma, ov))
        print('%4d %9d %9d  %5s  %.6f  %7.4f  %5d   %7.2f..%.2f'
              % (n, ai, bi, ai == bi, c, ma, ov, a.min(), a.max()))
    if not rows:
        raise SystemExit('no stepN/logits.npy + logits_teach_stepN.npy pairs found under %s / %s'
                         % (args.chain, args.ref))
    print('\npairs %d, argmax agreement %d/%d' % (len(rows), sum(1 for r in rows if r[1] == r[2]), len(rows)))
    for lo, hi in ((0, 4), (5, 14), (15, 29), (30, 200)):
        seg = [r for r in rows if lo <= r[0] <= hi]
        if seg:
            print('corr step %d-%d: mean %.6f min %.6f' % (lo, hi,
                  sum(r[3] for r in seg) / len(seg), min(r[3] for r in seg)))


if __name__ == '__main__':
    main()
