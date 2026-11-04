#!/usr/bin/env python3
"""Inspect the HiFi-GAN vocoders' op composition, to judge NPU feasibility.

Key question: do they contain Cos/Sin (the op that broke on ZG330 int8)?
HiFi-GAN outputs waveform directly, so it should have NO Cos/Sin at all.
"""
import urllib.request
import onnx
import collections
import os

OUT = r"S:\work\WCIS\workenv\.tmp_matcher\hifigan"
os.makedirs(OUT, exist_ok=True)
BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/vocoder-models/"

for name in ("hifigan_v1", "hifigan_v2", "hifigan_v3"):
    p = os.path.join(OUT, name + ".onnx")
    if not os.path.exists(p):
        print("downloading", name, "...", flush=True)
        try:
            urllib.request.urlretrieve(BASE + name + ".onnx", p)
        except Exception as e:
            print("  FAILED:", str(e)[:100])
            continue
    m = onnx.load(p)
    ops = collections.Counter(n.op_type for n in m.graph.node)
    sz = os.path.getsize(p)
    print("\n=== %s  %.2f MB  nodes=%d  opset=%s ==="
          % (name, sz / 1e6, len(m.graph.node),
             [(o.domain, o.version) for o in m.opset_import]))
    print("  inputs: ", [(i.name, [d.dim_value or d.dim_param for d in i.type.tensor_type.shape.dim])
                         for i in m.graph.input])
    print("  outputs:", [(o.name, [d.dim_value or d.dim_param for d in o.type.tensor_type.shape.dim])
                         for o in m.graph.output])
    print("  ops:", dict(ops.most_common(12)))
    trig = {k: v for k, v in ops.items() if k in ("Cos", "Sin", "Tan", "Atan", "Acos", "Asin")}
    print("  >>> trig ops:", trig if trig else "NONE  <-- no Cos/Sin")
