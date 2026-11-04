# -*- coding: utf-8 -*-
"""Convert 1D Conv/ConvTranspose in an ONNX graph into 2D form.

icraft's Conv op validates `dilations` against rank 2 (2D); a 1-D Conv carries
1-element kernel_shape/dilations/strides and gets rejected:
    Op (Conv) [ShapeInferenceError] Attribute dilations has incorrect size

Fix: give the kernel an extra spatial axis of size 1 (no-op in H), pad H with 0,
and let the input keep an extra H=1 axis. This preserves the math exactly.
"""
import onnx, os, sys, collections
import numpy as np
from onnx import numpy_helper, helper, TensorProto

def fix_convs(model):
    g = model.graph
    inits = {i.name: i for i in g.initializer}
    stats = collections.Counter()
    for n in g.node:
        if n.op_type not in ("Conv", "ConvTranspose"):
            continue
        attrs = {a.name: a for a in n.attribute}
        ks = list(attrs["kernel_shape"].ints) if "kernel_shape" in attrs else None
        if ks is None or len(ks) != 1:
            continue  # already 2D/3D
        # weight tensor: [M, C/group, k]
        wname = n.input[1]
        w = inits.get(wname)
        if w is None:
            continue
        dims = list(w.dims)
        if len(dims) != 3:
            continue
        arr = numpy_helper.to_array(w)
        new = np.expand_dims(arr, axis=len(dims) - 1)  # [M, C/g, k] -> [M, C/g, k, 1]
        t = numpy_helper.from_array(new, wname)
        w.CopyFrom(t)
        # rewrite attributes to 2-element
        for name in ("kernel_shape", "dilations", "strides", "pads", "output_padding"):
            if name not in attrs:
                continue
            vals = list(attrs[name].ints)
            if name == "pads":
                # 1-D pads come as [left, right] (len 2) or [pad] (len 1)
                if len(vals) in (1, 2):
                    pl, pr = (vals[0], vals[0]) if len(vals) == 1 else (vals[0], vals[1])
                    newvals = [0, pl, 0, pr]
                else:
                    continue
            elif len(vals) == 1:
                if name == "output_padding":
                    newvals = [0, vals[0]]
                else:
                    newvals = [1, vals[0]]
            else:
                continue
            attrs[name].ints[:] = newvals
            stats[name] += 1
        stats["conv1d->2d"] += 1
    return stats

def process(src, dst):
    m = onnx.load(src)
    st = fix_convs(m)
    onnx.save(m, dst)
    print(f"{os.path.basename(src)} -> {os.path.basename(dst)}  {dict(st)}")

if __name__ == "__main__":
    for f in sys.argv[1:]:
        process(f, f.rsplit(".onnx", 1)[0] + "_c2d.onnx")
