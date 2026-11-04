# -*- coding: utf-8 -*-
"""Aggregate per-op time for the PRelu build; compare with the 5-op version."""
import re
from collections import defaultdict

PAT = re.compile(
    r"opId:\s*(\d+)\s+opType:\s*(\S+)\s+wall:\s*([\d.]+)\s*ms\s+"
    r"mem:\s*([\d.]+)\s*ms\s+hard:\s*([\d.]+)\s*ms\s+other:\s*([\d.]+)\s*ms")

def parse(path):
    out = []
    for line in open(path, encoding="utf-8", errors="replace"):
        m = PAT.search(line)
        if m:
            out.append((int(m.group(1)), m.group(2), float(m.group(3)),
                        float(m.group(4)), float(m.group(5)), float(m.group(6))))
    return out

def report(path, label):
    rows = parse(path)
    if not rows:
        print(label, ": no op rows")
        return None
    agg = defaultdict(lambda: [0, 0.0, 0.0, 0.0, 0.0])
    for _, op, w, me, h, o in rows:
        a = agg[op]; a[0] += 1; a[1] += w; a[2] += me; a[3] += h; a[4] += o
    tw = sum(a[1] for a in agg.values()); th = sum(a[3] for a in agg.values())
    print("=" * 70)
    print("%s  %d ops   wall=%.2f ms   hard=%.2f ms" % (label, len(rows), tw, th))
    print("  %-18s %5s %9s %9s %7s" % ("opType", "n", "hard(ms)", "ms/op", "hard%"))
    for op, a in sorted(agg.items(), key=lambda x: -x[1][3])[:10]:
        print("  %-18s %5d %9.2f %9.3f %6.1f%%"
              % (op, a[0], a[3], a[3] / a[0], 100 * a[3] / th if th else 0))
    return tw, th, agg, len(rows)

old = report("/root/vocos_run/prelu/t_old.log", "OLD  (LeakyRelu -> 5 ops)")
new = report("/root/vocos_run/prelu/t_new.log", "NEW  (LeakyRelu -> PRelu)")

if old and new:
    print("=" * 70)
    print("hard   %.2f -> %.2f ms   = %.2f x faster" % (old[1], new[1], old[1] / new[1]))
    print("wall   %.2f -> %.2f ms   = %.2f x faster" % (old[0], new[0], old[0] / new[0]))
    print("ops    %d -> %d  (%+d)" % (old[3], new[3], new[3] - old[3]))