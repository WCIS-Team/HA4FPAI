# -*- coding: utf-8 -*-
"""Rewrite hifigan_v2 for icraft.

  LeakyRelu(69) -> Neg/Relu/Relu/Mul/Sub   (alpha=0.1)
  Clip(1)       -> Max then Min, with min/max taken from the Constant nodes
  opset 14 -> 11, ir -> 8
"""
import numpy as np
import onnx
from onnx import helper, numpy_helper
import collections

SRC = r"S:\work\WCIS\workenv\.tmp_matcher\hifigan\hifigan_v2.onnx"
DST = r"S:\work\WCIS\workenv\.tmp_matcher\hifigan\hifigan_v2_icraft.onnx"

m = onnx.load(SRC)
g = m.graph
print("original nodes=%d" % len(g.node))

# map: constant-node output name -> float value
const_vals = {}
for n in g.node:
    if n.op_type == "Constant":
        for a in n.attribute:
            if a.name == "value" and a.t.data_type == onnx.TensorProto.FLOAT:
                arr = numpy_helper.to_array(a.t)
                const_vals[n.output[0]] = float(arr.reshape(-1)[0])

counter = [0]
def uniq(p):
    counter[0] += 1
    return "%s_%d" % (p, counter[0])

def add_const(v):
    name = uniq("cst")
    g.initializer.append(numpy_helper.from_array(np.array(v, np.float32), name))
    return name

new = []
n_lr = n_clip = 0
for n in g.node:
    if n.op_type == "LeakyRelu":
        alpha = 0.1
        for a in n.attribute:
            if a.name == "alpha":
                alpha = a.f
        x, y = n.input[0], n.output[0]
        neg, rp, rn, cm, mu = uniq("neg"), uniq("rp"), uniq("rn"), uniq("ca"), uniq("mu")
        new.append(helper.make_node("Neg", [x], [neg], name=uniq("NegNode")))
        new.append(helper.make_node("Relu", [x], [rp], name=uniq("ReluPos")))
        new.append(helper.make_node("Relu", [neg], [rn], name=uniq("ReluNeg")))
        new.append(helper.make_node("Mul", [add_const(alpha), rn], [mu], name=uniq("MulAlpha")))
        new.append(helper.make_node("Sub", [rp, mu], [y], name=uniq("SubNode")))
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
        new.append(helper.make_node("Max", [x, add_const(mn)], [t1], name=uniq("ClipMin")))
        new.append(helper.make_node("Min", [t1, add_const(mx)], [y], name=uniq("ClipMax")))
        n_clip += 1
        continue

    if n.op_type == "Squeeze" and len(n.input) > 1:
        # opset 11: axes must be an attribute, not an input
        ax = const_vals.get(n.input[1])
        nn = helper.make_node("Squeeze", [n.input[0]], [n.output[0]],
                             name=n.name, axes=[int(ax)] if ax is not None else None)
        new.append(nn)
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
print("rewrote LeakyRelu=%d Clip=%d" % (n_lr, n_clip))
print("new nodes=%d opset=11 ir=%d" % (len(g.node), m.ir_version))
print("ops:", dict(collections.Counter(n.op_type for n in g.node).most_common(14)))
print("saved", DST)
