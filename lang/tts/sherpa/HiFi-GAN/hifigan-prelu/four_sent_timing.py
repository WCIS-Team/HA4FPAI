# -*- coding: utf-8 -*-
"""Per-sentence hard time for the PRelu model (4 sentences)."""
import re
import glob

PAT = re.compile(r"opType:\s*(\S+).*?wall:\s*([\d.]+)\s*ms\s+mem:\s*([\d.]+)\s*ms\s+"
                 r"hard:\s*([\d.]+)\s*ms")

for log in sorted(glob.glob("/root/vocos_run/prelu/d_*.log")):
    tag = log.split("d_")[-1].replace(".log", "")
    tw = th = 0.0
    n = 0
    for line in open(log, encoding="utf-8", errors="replace"):
        m = PAT.search(line)
        if m:
            n += 1
            tw += float(m.group(2))
            th += float(m.group(4))
    print("%-10s ops=%3d   wall=%7.2f ms   hard=%7.2f ms" % (tag, n, tw, th))