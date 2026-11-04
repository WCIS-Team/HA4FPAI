"""Scan icraft runtime binaries for host-op implementations of Slice / Concat / CausalMask.

Tells whether a ZG artifact that keeps hand-inserted @hostt Slice+Concat ops can run on the board:
the aarch64 libicraft_hostbackend.so is the library that executes @hostt ops there.

Usage: python so_probe.py <dir> [more dirs...]
"""
import glob
import os
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

PATS = [b'icraft::xir::Slice', b'icraft::xir::Concat', b'icraft::xir::CausalMask',
        b'icraft::xir::Copy', b'icraft::xir::Reshape']

files = []
for d in sys.argv[1:]:
    files += glob.glob(os.path.join(d, '**', '*.so*'), recursive=True)
    files += glob.glob(os.path.join(d, '**', '*.dll'), recursive=True)
print('candidates', len(files))
for f in sorted(set(files)):
    if not os.path.isfile(f):
        continue
    with open(f, 'rb') as fh:
        b = fh.read()
    hits = {p.decode(): b.count(p) for p in PATS if b.count(p)}
    if hits:
        print(os.path.basename(f), len(b), hits)
