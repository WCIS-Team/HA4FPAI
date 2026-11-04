import os
D = r"D:\yzd\ttsbench\models\matcha-icefall-zh-baker"
lines = open(os.path.join(D, "tokens.txt"), encoding="utf-8").read().splitlines()
toks = [l.split()[0] for l in lines if l.strip()]
print("total tokens:", len(toks))
for probe in ["i3", "h", "w", "sh", "u4", "zh", "n", "i", "u"]:
    print("  %-4s present: %s" % (probe, probe in toks))
print()
print("tokens 10..60:", toks[10:60])
print()
# how many single-letter tokens vs syllable tokens
import re
single = [t for t in toks if re.match(r"^[a-z]$", t)]
syl = [t for t in toks if re.match(r"^[a-z]+[0-9]$", t)]
print("single-letter:", len(single), single[:30])
print("tone-syllable:", len(syl), syl[:12])
