"""Patch Q3V1-decoder_adapted.json so attention mask slot 0 is always closed.

Graph-level edit, no new parameters and no .raw change:
  M   = CausalMask output            v_id 180  fp32 [1,1,512]  (measured [0,0,-inf x510])
  X   = Slice(M, [0,0,511]:[1,1,512])        -> [1,1,1]   = M[511] = -inf
  T   = Slice(M, [0,0,  1]:[1,1,512])        -> [1,1,511] = M[1:]  (positions keep their index)
  M'  = Concat([X, T], axis=2)               -> [1,1,512] = [-inf, M1, M2, ...]
  op 4533 (Reshape) input rewired 180 -> M'.

M[511] is -inf for every step except e1 == 511 (cache full); at that last step slot 0 holds a
real token, so leaving it open there is the correct behaviour anyway.
"""
import sys, json, copy
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

src, dst = sys.argv[1], sys.argv[2]
# optional 3rd arg: compile_target for the inserted ops. The Host backend on PC accepts @hostt; the
# board's host runtime has no proof of a Concat forward, so @zhuget(330) is the fallback that makes
# generate put the three ops into the NPU hardop stream instead.
TARGET = sys.argv[3] if len(sys.argv) > 3 else "@hostt"
d = json.load(open(src, encoding="utf-8"))
ops = d["ops"]

MASK_V = 180
CONSUMER = 4533

idx48 = [i for i, o in enumerate(ops) if o["_type_key"] == "icraft::xir::CausalMask"][0]
op48 = ops[idx48]
mv = None
for v in op48["outputs"]:
    if v["v_id"] == MASK_V:
        mv = v
assert mv is not None, "v_id 180 is not the CausalMask output"
n = mv["dtype"]["shape"][-1]
print("mask value:", mv["dtype"]["element_dtype"], mv["dtype"]["shape"], "layout", mv["dtype"]["layout"])

consumers = [o["op_id"] for o in ops if any(v["v_id"] == MASK_V for v in (o.get("inputs") or []))]
assert consumers == [CONSUMER], "unexpected consumers of v_id %d: %s" % (MASK_V, consumers)

maxv = max(v["v_id"] for o in ops for v in (o.get("outputs") or []))
maxid = max(o["op_id"] for o in ops)
VX, VT, VM = maxv + 1, maxv + 2, maxv + 3
OPX, OPT, OPM = maxid + 1, maxid + 2, maxid + 3


def val(vid, shape, ref=mv):
    v = copy.deepcopy(ref)
    v["v_id"] = vid
    v["dtype"]["shape"] = shape
    return v


def mk_slice(vid_out, begin, end, tag):
    return {
        "_type_key": "icraft::xir::Slice",
        "begin": begin,
        "compile_target": TARGET,
        "end": end,
        "inputs": [copy.deepcopy(mv)],
        "name": "mask0_" + tag,
        "op_id": None,
        "outputs": [val(vid_out, [int(x - y) for x, y in zip(end, begin)])],
        "stride": [1, 1, 1],
        "tags": {},
    }


opX = mk_slice(VX, [0, 0, n - 1], [1, 1, n], "tail_inf")
opX["op_id"] = OPX
opT = mk_slice(VT, [0, 0, 1], [1, 1, n], "body")
opT["op_id"] = OPT
opM = {
    "_type_key": "icraft::xir::Concat",
    "axis": 2,
    "compile_target": TARGET,
    "inputs": [copy.deepcopy(opX["outputs"][0]), copy.deepcopy(opT["outputs"][0])],
    "name": "mask0_closed",
    "op_id": OPM,
    "outputs": [val(VM, [1, 1, n])],
    "tags": {},
}

ops[idx48 + 1:idx48 + 1] = [opX, opT, opM]
for o in ops:
    if o["op_id"] == CONSUMER:
        for v in o["inputs"]:
            if v["v_id"] == MASK_V:
                v["v_id"] = VM
                v["dtype"]["shape"] = [1, 1, n]

d["ops"] = ops
json.dump(d, open(dst, "w", encoding="utf-8"), ensure_ascii=False)
print("wrote", dst)
print("new ops: Slice %s v%s [0,0,%d]->[1,1,%d] ; Slice %s v%s [0,0,1]->[1,1,%d] ; Concat %s v%s axis=2 ; op %d input 180 -> v%s"
      % (OPX, VX, n - 1, n, OPT, VT, n, OPM, VM, CONSUMER, VM))
print("params_bytes/params_md5 left untouched:", d["params_bytes"], d["params_md5"])
