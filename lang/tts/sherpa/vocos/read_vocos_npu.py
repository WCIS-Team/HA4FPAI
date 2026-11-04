#!/usr/bin/env python3
"""Read REAL fp32 vocos outputs on the board NPU, using the corrected idiom:
   - input TensorType from net.ops[0].outputs[0]  (InputNode, @fp(32))
   - dual backend Session.Create([ZG330Backend, HostBackend], net.view(0), [zgdev, hostdev])
   - Tensor constructed from a numpy array: xrt.Tensor(arr, tt)
   - outputs must be moved back: t.to(xrt.HostDevice.MemRegion()) before read()
"""
import sys
import numpy as np
import icraft
from icraft import xir, xrt, host_backend
from icraft.zg330backend import ZG330Backend

J = "/root/vocos_run/imodel/vocos_native_ZG.json"
R = "/root/vocos_run/imodel/vocos_native_ZG.raw"
INP = "/root/vocos_run/io/input_1_1_600_80.bin"
OUTP = "/root/vocos_run/npu_out_"

net = xir.Network.CreateFromJsonFile(J)
try:
    net.lazyLoadParamsFromFile(R)
except Exception as e:
    print("lazyLoad note:", type(e).__name__, str(e)[:80])

print("ops:", len(net.ops), "inputs:", len(net.inputs()), "outputs:", len(net.outputs()))

in_tt = net.ops[0].outputs[0].tensorType()
print("input tt:", in_tt.shape, in_tt.element_dtype)

mel = np.fromfile(INP, dtype=np.float32).reshape(in_tt.shape[0], in_tt.shape[1],
                                                  in_tt.shape[2], in_tt.shape[3])
print("input data:", mel.shape, mel.size)

dev = xrt.Device.Open("axi://zg330aiu?npu=0x40000000&dma=0x80000000")
sess = xrt.Session.Create([ZG330Backend, host_backend.HostBackend],
                          net.view(0), [dev, xrt.HostDevice.Default()])
sess.apply()
print("session applied")

inp = xrt.Tensor(mel.astype(np.float32), in_tt)
print("input tensor ready")

outs = sess.forward([inp])
print("forward ->", len(outs), "tensors")

gold = {}
for nm in ("mag", "x", "y"):
    gold[nm] = np.fromfile("/root/vocos_run/io/golden_%s.bin" % nm, dtype=np.float32)

for i, t in enumerate(outs):
    h = t.to(xrt.HostDevice.MemRegion())
    nbytes = h.dtype().bytes()
    buf = bytearray(nbytes)
    h.read(buf, 0, nbytes)
    v = np.frombuffer(bytes(buf), dtype=np.float32)
    print("out[%d] n=%d min=%.6f max=%.6f mean=%.6f unique=%d"
          % (i, v.size, float(v.min()), float(v.max()), float(v.mean()), np.unique(v).size))
    v.tofile(OUTP + str(i) + ".bin")
