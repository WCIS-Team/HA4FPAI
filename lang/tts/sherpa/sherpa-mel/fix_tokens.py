"""Fix only the space-token line so sherpa's own text2token can run.

tokens.txt line 1 is "  0" (the token IS a space) -> line.strip().split() gives
['0'] (1 field) and text2token's `assert len(toks) == 2` fails.
Rewrite that one line with a placeholder name; all other ids stay identical.
"""
import os
import shutil

D = r"D:\yzd\ttsbench\models\matcha-icefall-zh-baker"
src = os.path.join(D, "tokens.txt")
dst = r"D:\yzd\ttsbench\tokens_fixed.txt"

with open(src, encoding="utf-8") as f:
    lines = f.readlines()

out = []
for i, line in enumerate(lines):
    p = line.rstrip("\n").split()
    if len(p) == 2:
        out.append(line if line.endswith("\n") else line + "\n")
    elif len(p) == 1:
        # bare id -> the token is whitespace; give it a safe placeholder name
        out.append("__SP__ %s\n" % p[0])
        print("line %d patched: %r -> __SP__ %s" % (i + 1, line, p[0]))
    else:
        print("line %d unexpected: %r" % (i + 1, line))

with open(dst, "w", encoding="utf-8", newline="\n") as f:
    f.writelines(out)
print("wrote", dst, "lines:", len(out))

# verify all non-space ids identical
a, b = {}, {}
with open(src, encoding="utf-8") as f:
    for line in f:
        p = line.strip().split()
        if len(p) == 2:
            a[p[0]] = int(p[1])
with open(dst, encoding="utf-8") as f:
    for line in f:
        p = line.strip().split()
        if len(p) == 2:
            b[p[0]] = int(p[1])
diff = {k: (a.get(k), b.get(k)) for k in set(a) | set(b) if a.get(k) != b.get(k)}
print("id differences (excluding the space line):", diff if diff else "none")
