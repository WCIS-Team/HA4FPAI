"""Print every occurrence of a byte pattern in a binary with context, to read mangled symbol names.

Used to check whether the aarch64 icraft host runtime actually implements the ops a hand-patched ZG
artifact adds (a signature/forward symbol for ConcatNode), not just mentions the op name in a pass.

Usage: python so_ctx.py <file> <pattern> [more patterns...]
"""
import sys

path = sys.argv[1]
pats = [p.encode() for p in sys.argv[2:]]
b = open(path, 'rb').read()
print(path, len(b), 'bytes')
for p in pats:
    n = b.count(p)
    print('-- %s : %d' % (p.decode(), n))
    i = 0
    shown = 0
    while shown < 12:
        i = b.find(p, i)
        if i < 0:
            break
        ctx = b[max(0, i - 70):i + 70]
        print('   @%#x | %s' % (i, ''.join(chr(c) if 32 <= c < 127 else '.' for c in ctx)))
        i += 1
        shown += 1
