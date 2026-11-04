# -*- coding: utf-8 -*-
"""Aggregate per-op wall/mem/hard time by op type from icraft --log_time output."""
import re
import sys
from collections import defaultdict

PAT = re.compile(
    r"opId:\s*(\d+)\s+opType:\s*(\S+)\s+wall:\s*([\d.]+)\s*ms\s+"
    r"mem:\s*([\d.]+)\s*ms\s+hard:\s*([\d.]+)\s*ms\s+other:\s*([\d.]+)\s*ms")

def parse(path):
    rows = []
    for line in open(path, encoding="utf-8", errors="replace"):
        m = PAT.search(line)
        if m:
            rows.append((int(m.group(1)), m.group(2), float(m.group(3)),
                         float(m.group(4)), float(m.group(5)), float(m.group(6))))
    return rows

def report(path, label):
    rows = parse(path)
    if not rows:
        print(label, "no rows")
        return
    agg = defaultdict(lambda: [0, 0.0, 0.0, 0.0, 0.0])
    for _, op, w, me, h, o in rows:
        a = agg[op]
        a[0] += 1; a[1] += w; a[2] += me; a[3] += h; a[4] += o
    tw = sum(a[1] for a in agg.values())
    th = sum(a[3] for a in agg.values())
    print("=" * 74)
    print("%s   %d ops   wall总=%.2f ms   hard总=%.2f ms" % (label, len(rows), tw, th))
    print("  %-16s %5s %9s %8s %8s %8s %7s" % ("opType", "n", "wall(ms)", "mem", "hard", "other", "hard%"))
    for op, a in sorted(agg.items(), key=lambda x: -x[1][3])[:12]:
        print("  %-16s %5d %9.2f %8.2f %8.2f %8.2f %6.1f%%"
              % (op, a[0], a[1], a[2], a[3], a[4], 100 * a[3] / th if th else 0))
    return tw, th, agg

v = report("/root/vocos_run/prof/tv.log", "VOCOS fp16")
h = report("/root/vocos_run/prof/th.log", "HIFIGAN fp16")

if v and h:
    print("=" * 74)
    print("hard 时间比 hifigan/vocos = %.2f" % (h[1] / v[1]))
    print("wall 时间比 hifigan/vocos = %.2f" % (h[0] / v[0]))
    # per-op-type average
    print()
    print("每算子平均 hard 时间（ms）：")
    for name, d in (("vocos", v[2]), ("hifigan", h[2])):
        print("  %-8s" % name, end="")
        for op in ("Conv", "ConvTranspose", "MatMul", "Transpose", "LayerNorm", "Mul", "Add"):
            if op in d:
                print("  %s=%.3f(n=%d)" % (op, d[op][3] / d[op][0], d[op][0]), end="")
        print()
