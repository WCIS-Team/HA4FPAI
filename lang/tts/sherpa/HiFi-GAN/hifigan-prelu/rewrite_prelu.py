# -*- coding: utf-8 -*-
"""Rewrite hifigan_v2 for icraft, using PReLU instead of the 5-op LeakyRelu hack.

Why: on ZG330 the per-op overhead dominates. A Conv2d costs 0.365 ms/op and a
ReLU costs 0.341 ms/op — element-wise ops are NOT free. The old rewrite turned
69 LeakyRelu into 69 x 5 = 345 ops (~83.7% of total time). PRelu(X, slope=alpha)
is exactly LeakyRelu(X, alpha) and is a single op in icraft's op surface.

  LeakyRelu(69) -> PRelu(69)          (slope = alpha, as a constant)
  Clip(1)       -> Max then Min       (unchanged, min/max from Constant nodes)
  Squeeze axes  -> attribute          (opset 11)
  then run conv1d_to_2d to fix dilations
"""
import sys
import os
import collections
import numpy as np
import onnx
from onnx import helper, numpy_helper, TensorProto

SRC = r'S:\work\WCIS\workenv\.tmp_matcher\hifigan\hifigan_v2.onnx'
DST = r'S:\work\WCIS\workenv\hifigan_prelu\hifigan_v2_prelu.onnx'

os.makedirs(os.path.dirname(DST), exist_ok=True)

m = onnx.load(SRC)
g = m.graph

const_vals = {}
for i in g.initializer:
    try:
        const_vals[i.name] = numpy_helper.to_array(i)
    except Exception:
        pass
for n in g.node:
    if n.op_type == "Constant" and n.output[0]:
        for a in n.attribute:
            if a.name == "value":
                const_vals[n.output[0]] = numpy_helper.to_array(a.t)

_counter = collections.Counter()
def uniq(tag):
    _counter[tag] += 1
    return "%s_%d" % (tag, _counter[tag])

def add_const(v):
    name = uniq("cst")
    g.initializer.append(numpy_helper.from_array(np.array(v, np.float32), name))
    return name

new = []
n_lr = n_clip = n_sq = 0
for n in g.node:
    if n.op_type == "LeakyRelu":
        alpha = 0.1
        for a in n.attribute:
            if a.name == "alpha":
                alpha = a.f
        x, y = n.input[0], n.output[0]
        slope = add_const([alpha])            # shape [1] -> broadcasts over X
        new.append(helper.make_node("PRelu", [x, slope], [y],
                                    name=n.name or uniq("PRelu")))
        n_lr += 1
        continue

    if n.op_type == "Clip":
        ins = list(n.input)
        mn = const_vals.get(ins[1]) if len(ins) > 1 else None
        mx = const_vals.get(ins[2]) if len(ins) > 2 else None
        if mn is None or mx is None:
            print("  skip Clip (min/max not resolvable):", n.name, ins)
            new.append(n)
            continue
        x, y = ins[0], n.output[0]
        t1 = uniq("cliplo")
        new.append(helper.make_node("Max", [x, add_const(np.asarray(mn).ravel()[0])],
                                    [t1], name=uniq("ClipMin")))
        new.append(helper.make_node("Min", [t1, add_const(np.asarray(mx).ravel()[0])],
                                    [y], name=uniq("ClipMax")))
        n_clip += 1
        continue

    if n.op_type == "Squeeze" and len(n.input) > 1:
        ax = const_vals.get(n.input[1])
        new.append(helper.make_node("Squeeze", [n.input[0]], [n.output[0]],
                                    name=n.name,
                                    axes=[int(np.asarray(ax).ravel()[0])] if ax is not None else None))
        n_sq += 1
        continue

    new.append(n)

del g.node[:]
for n in new:
    g.node.append(n)

del m.opset_import[:]
op = m.opset_import.add()
op.domain = ""
op.version = 11
m.ir_version = 8

onnx.checker.check_model(m)
onnx.save(m, DST)
print("LeakyRelu->PRelu: %d   Clip->Max/Min: %d   Squeeze axes: %d" % (n_lr, n_clip, n_sq))
print("nodes: %d -> %d   (old 5-op hack would give 482)" % (len(onnx.load(SRC).graph.node), len(g.node)))
print("saved", DST)