# -*- coding: utf-8 -*-
"""Verify hifigan_v2_prelu.onnx is numerically identical to hifigan_v2.onnx.

Runs both on onnxruntime CPU with the same mel and compares waveforms.
Must be ~1.000000 correlation / ~0 max|diff| before we bother compiling.
"""
import numpy as np
import onnxruntime as ort

ORIG = r'D:\yzd\ttsbench\hifigan_v2.onnx'
NEW = r'D:\yzd\ttsbench\hifigan_v2_prelu.onnx'
MEL = r'D:\yzd\ttsbench\samples\01_问候_exact_mel.bin'

mel = np.fromfile(MEL, np.float32).reshape(80, -1)
T = mel.shape[1]
x = mel[None, :80, :T].astype(np.float32)
print("mel:", x.shape)

so = ort.InferenceSession(ORIG, providers=["CPUExecutionProvider"])
sn = ort.InferenceSession(NEW, providers=["CPUExecutionProvider"])
print("orig in/out:", [(i.name, i.shape) for i in so.get_inputs()], "->", [(o.name, o.shape) for o in so.get_outputs()])
print("new  in/out:", [(i.name, i.shape) for i in sn.get_inputs()], "->", [(o.name, o.shape) for o in sn.get_outputs()])

a = np.asarray(so.run(None, {so.get_inputs()[0].name: x})[0], np.float32).ravel()
b = np.asarray(sn.run(None, {sn.get_inputs()[0].name: x})[0], np.float32).ravel()

print("orig len=%d  new len=%d" % (a.size, b.size))
n = min(a.size, b.size)
d = np.abs(a[:n] - b[:n])
c = float(np.corrcoef(a[:n], b[:n])[0, 1]) if a[:n].std() > 1e-12 and b[:n].std() > 1e-12 else float("nan")
print("corr  = %.9f" % c)
print("max|d|= %.3e    rms(a)=%.6f rms(b)=%.6f" % (d.max(), np.sqrt((a[:n]**2).mean()), np.sqrt((b[:n]**2).mean())))
print()
if c > 0.999999 and d.max() < 1e-5:
    print("PASS 数值等价")
else:
    print("FAIL 不等价，别编译")