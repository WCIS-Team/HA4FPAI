"""One-step probe: dump the CausalMask output tensor of the compiled net and read its values.

The mask enters attention additively (parsed IR: CausalMask op_id 48 -> v_id 180 @fp(32)[1,1,512]
-> Cast -> Reshape -> Expand -> Add onto the scores before Softmax), so the number that closes a
slot is what a "close slot 0" patch has to reproduce. This probe prints that number for a vendored
step-0 feed. It runs one icraft-run forward, ~41 s, and needs >=6 GB free RAM.

Usage:
  python dump_mask.py --json <adapted.json> --raw <adapted.raw> --work <dir> [--vid 180] [--step 0]
"""
import argparse
import os
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import host_gen as hg  # noqa: E402

sys.stdout.reconfigure(encoding='utf-8', errors='replace')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', required=True)
    ap.add_argument('--raw', required=True)
    ap.add_argument('--work', required=True)
    ap.add_argument('--prompt', default='151644,872,198,105043,100165,11319,151645,198,151644,77091,198')
    ap.add_argument('--step', type=int, default=0)
    ap.add_argument('--vid', type=int, default=180)
    ap.add_argument('--dump-format', default='SFT')
    ap.add_argument('--exe', default=hg.EXE)
    args = ap.parse_args()

    net = hg.load_net(args.json)
    prompt = [int(t) for t in args.prompt.split(',') if t.strip()]
    i = args.step
    step_dir = os.path.join(args.work, 'mask_step%d' % i)
    feed_dir = os.path.join(step_dir, 'feed')
    os.makedirs(feed_dir, exist_ok=True)

    dyn = [1.0, float(i + 1), 0.0]
    paths = hg.write_ctrl(feed_dir, net, prompt[i], dyn)
    prev = os.path.join(args.work, 'step%d' % (i - 1), 'run', net['name'],
                        'ftmp' + args.dump_format.upper())
    paths += (hg.chain_past(feed_dir, net, prev) if i else hg.seed_past(feed_dir, net))
    inputs_txt = os.path.join(step_dir, 'inputs.txt')
    open(inputs_txt, 'w').write('\n'.join(os.path.abspath(p) for p in paths) + '\n')
    print('feed step %d token %d tdf %s, %d inputs' % (i, prompt[i], dyn, len(paths)))

    dump_ids = ','.join(str(v) for v in [args.vid, net['logits']['v_id']])
    log_dir = os.path.join(step_dir, 'run')
    os.makedirs(log_dir, exist_ok=True)
    cmd = [args.exe, '--json', args.json, '--raw', args.raw, '--input', inputs_txt,
           '--backends', 'Host', '--dump_output', '--dump_format', args.dump_format.upper(),
           '--dump_ftmp', dump_ids, '--log_path', log_dir, '--log_time']
    rc = subprocess.call(cmd,
                         stdout=open(os.path.join(step_dir, 'run.out.log'), 'wb'),
                         stderr=open(os.path.join(step_dir, 'run.err.log'), 'wb'),
                         cwd=os.path.dirname(os.path.abspath(args.work)))
    print('icraft-run rc', rc)
    dump_dir = os.path.join(log_dir, net['name'], 'ftmp' + args.dump_format.upper())
    print('dump dir contents:', os.listdir(dump_dir) if os.path.isdir(dump_dir) else 'MISSING')

    path = os.path.join(dump_dir, '%d.ftmp' % args.vid)
    if not os.path.exists(path):
        tail = open(os.path.join(step_dir, 'run.err.log'), encoding='utf-8',
                    errors='replace').read()[-1500:]
        print('NO DUMP for v_id %d. stderr tail:\n%s' % (args.vid, tail))
        return
    arr = hg.read_dump(path, [1, 1, 512])
    uniq, cnt = np.unique(arr, return_counts=True)
    print('v_id %d: %d values, dtype %s' % (args.vid, arr.size, arr.dtype))
    print('distinct (value, count): %s' % [(float(u), int(c)) for u, c in zip(uniq, cnt)][:8])
    print('first 8 :', [float(x) for x in arr[:8]])
    idx = np.flatnonzero(arr != arr[0])
    print('slot 0 = %.6g; first index with a different value = %s (%d such indices)'
          % (float(arr[0]), int(idx[0]) if idx.size else None, int(idx.size)))
    finite = np.isfinite(arr.astype(np.float64))
    print('all finite:', bool(finite.all()), 'min', float(arr.min()), 'max', float(arr.max()))
    np.save(os.path.join(args.work, 'mask_step%d.npy' % i), arr)


if __name__ == '__main__':
    main()
