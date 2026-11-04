# -*- coding: utf-8 -*-
"""Rewrite hifigan_v2 so icraft can parse it.

Two problems found against the 114-page op surface:
  1. LeakyRelu (69 nodes)  -- NOT in icraft.  Rewrite as
        LeakyRelu(x) = Relu(x) - alpha*Relu(-x)      (alpha = 0.1 here)
     which needs only Relu / Mul / Sub / Neg, all present.
  2. Clip (1 node)         -- icraft has ClampScalar instead.  Rewrite with
     Min/Max if the range is constant.

Also: opset must be 11 or 17 (this model is opset 14) and IR <= 8.
Input must stay 4-D for icraft; mel is [N,80,L] so we will wrap later in the
icraft input mapping step, not here.

Output: hifigan_v2_icraft.onnx
"""
import numpy as np
import onnx
from onnx import helper, TensorProto, numpy_helper
import collections

SRC = r"S:\work\WCIS\workenv\.tmp_matcher\hifigan\hifigan_v2.onnx"
DST = r"S:\work\WCIS\workenv\.tmp_matcher\hifigan\hifigan_v2_icraft.onnx"

m = onnx.load(SRC)
g = m.graph
print("original: nodes=%d opset=%s" % (len(g.node), [(o.domain, o.version) for o in m.opset_import]))

new_nodes = []
counter = [0]

def uniq(prefix):
    counter[0] += 1
    return "%s_%d" % (prefix, counter[0])

def add_const(name, val_f):
    g.initializer.append(numpy_helper.from_array(np.array(val_f, dtype=np.float32), name))
    return name

# ---- rewrite LeakyRelu ----
n_lrelu = 0
for n in g.node:
    if n.op_type != "LeakyRelu":
        new_nodes.append(n)
        continue
    alpha = 0.1
    for a in n.attribute:
        if a.name == "alpha":
            alpha = a.f
    x = n.input[0]
    y = n.output[0]
    a_name = uniq("neg")
    rp = uniq("relupos")
    rn = uniq("reluneg")
    mul = uniq("lmul")
    sub = y
    new_nodes.append(helper.make_node("Neg", [x], [a_name], name=uniq("NegNode")))
    new_nodes.append(helper.make_node("Relu", [x], [rp], name=uniq("ReluPos")))
    new_nodes.append(helper.make_node("Relu", [a_name], [rn], name=uniq("ReluNeg")))
    c = add_const(uniq("alpha"), alpha)
    new_nodes.append(helper.make_node("Mul", [c, rn], [mul], name=uniq("MulAlpha")))
    new_nodes.append(helper.make_node("Sub", [rp, mul], [sub], name=uniq("SubNode")))
    n_lrelu += 1
print("rewrote LeakyRelu:", n_lrelu)

# ---- rewrite Clip (constant min/max) ----
n_clip = 0
for n in list(new_nodes):
    if n.op_type != "Clip":
        continue
    mn = mx = None
    for a in n.attribute:
        if a.name == "min":
            mn = a.f
        if a.name == "max":
            mx = a.f
    if mn is None or mx is None:
        print("  Clip with tensor min/max -- left alone:", n.name)
        continue
    idx = new_nodes.index(n)
    new_nodes.remove(n)
    x, y = n.input[0], n.output[0]
    lo = add_const(uniq("clipmin"), mn)
    hi = add_const(uniq("clipmax"), mx)
    t1 = uniq("cliplo")
    new_nodes.insert(idx, helper.make_node("Max", [x, lo], [t1], name=uniq("ClipMin")))
    new_nodes.insert(idx + 1, helper.make_node("Min", [t1, hi], [y], name=uniq("ClipMax")))
    n_clip += 1
print("rewrote Clip:", n_clip)

g.node = new_nodes
# opset -> 11
del m.opset_import[:]
op = m.opset_import.add()
op.domain = ""
op.version = 11
m.ir_version = 8

onnx.checker.check_model(m)
onnx.save(m, DST)
c = collections.Counter(n.op_type for n in m.graph.node)
print("\nnew: nodes=%d opset=11 ir=%d" % (len(m.graph.node), m.ir_version))
print("ops:", dict(c.most_common(14)))
print("saved", DST)
