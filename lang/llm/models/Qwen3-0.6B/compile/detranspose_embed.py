"""Step 1 of rewriting the Qwen3 genai graph into the vendor Qwen2 graph's conventions.

The vendor's Qwen2 file has no ONNX-level Transpose: it ships the embedding twice, as
model.embed_tokens.weight [vocab, hidden] for the Gather and lm_head.MatMul.weight
[hidden, vocab] for the MatMul. Our Qwen3 export keeps one copy ([hidden, vocab]) and inserts
/model/embed_tokens/Transpose with a FLOAT16 input, which is the only fp16 Transpose in either
graph and lands in icraft's ox_shape_infer dtype converter, which has no fp16 case
("Unknown dtype 10").

Here the transposed copy is materialised into the external-data blob and the Transpose node is
removed, so Gather reads an initializer exactly like the vendor's does.

The output directory gets a fresh data file (original bytes copied, then the new tensor appended
at a 4096-aligned offset); the input file is never modified, because previous steps hard-linked
their data blobs and writing through a link would corrupt the source.
"""
import argparse
import os
import sys

import numpy as np
import onnx

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

ALIGN = 4096


def align_up(n, a=ALIGN):
    return (n + a - 1) // a * a


def ext_map(init):
    return {e.key: e.value for e in init.external_data}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--dst', required=True)
    args = ap.parse_args()

    src_onnx = os.path.join(args.src, 'model.onnx')
    src_data = os.path.join(args.src, 'model.onnx.data')
    os.makedirs(args.dst, exist_ok=True)
    dst_onnx = os.path.join(args.dst, 'model.onnx')
    dst_data = os.path.join(args.dst, 'model.onnx.data')

    m = onnx.load(src_onnx, load_external_data=False)
    g = m.graph
    inits = {i.name: i for i in g.initializer}

    tr = [n for n in g.node if n.op_type == 'Transpose' and 'embed_tokens' in n.name]
    if len(tr) != 1:
        raise SystemExit('expected exactly one embed_tokens Transpose, got %d' % len(tr))
    tr = tr[0]
    consumers = [n for n in g.node if tr.output[0] in n.input]
    print('transpose', tr.name, 'perm', [onnx.helper.get_attribute_value(a) for a in tr.attribute],
          'consumers', [(c.op_type, c.name) for c in consumers])
    if len(consumers) != 1 or consumers[0].op_type != 'Gather':
        raise SystemExit('unexpected consumers, aborting')
    gather = consumers[0]

    lm = [i for i in g.initializer if i.name == 'lm_head.MatMul.weight']
    if not lm:
        raise SystemExit('lm_head.MatMul.weight initializer not found')
    lm = lm[0]
    em = ext_map(lm)
    off, length = int(em['offset']), int(em['length'])
    dims = list(lm.dims)
    if lm.data_type != onnx.TensorProto.FLOAT16:
        raise SystemExit('lm_head weight is not fp16: %s' % lm.data_type)
    prod = 1
    for d in dims:
        prod *= d
    if prod * 2 != length:
        raise SystemExit('dims/length mismatch %s %d vs %d' % (dims, prod * 2, length))
    print('source weight', lm.name, dims, 'offset', off, 'length', length)

    with open(src_data, 'rb') as f:
        f.seek(off)
        buf = f.read(length)
    if len(buf) != length:
        raise SystemExit('short read %d/%d' % (len(buf), length))
    w = np.frombuffer(buf, dtype='<f2').reshape(dims)
    wt = np.ascontiguousarray(w.T)
    print('transposed shape', wt.shape, 'bytes', wt.nbytes,
          'spot', float(w[3, 7]), float(wt[7, 3]))

    src_size = os.path.getsize(src_data)
    new_off = align_up(src_size)
    print('data copy', src_size, 'bytes; new tensor at offset', new_off)
    with open(src_data, 'rb') as fin, open(dst_data, 'wb') as fout:
        while True:
            chunk = fin.read(1 << 24)
            if not chunk:
                break
            fout.write(chunk)
        fout.seek(new_off)
        fout.write(wt.tobytes())
    wrote = os.path.getsize(dst_data)
    print('new data size', wrote, 'expected', new_off + wt.nbytes)
    if wrote != new_off + wt.nbytes:
        raise SystemExit('data file size wrong')

    name = 'model.embed_tokens.weight'
    ni = onnx.TensorProto()
    ni.name = name
    ni.data_type = onnx.TensorProto.FLOAT16
    ni.dims.extend([wt.shape[0], wt.shape[1]])
    ni.data_location = onnx.TensorProto.EXTERNAL
    for k, v in (('location', 'model.onnx.data'), ('offset', str(new_off)),
                 ('length', str(wt.nbytes))):
        e = ni.external_data.add()
        e.key, e.value = k, v
    g.initializer.append(ni)

    g.node.remove(tr)
    gather.input[0] = name
    gone = [vi for vi in g.value_info if vi.name == tr.output[0]]
    for vi in gone:
        g.value_info.remove(vi)
    print('removed transpose outputs', len(gone), 'gather now reads', gather.input[0])

    onnx.save(m, dst_onnx)

    chk = onnx.load(dst_onnx, load_external_data=False)
    ci = {i.name: i for i in chk.graph.initializer}[name]
    cm = ext_map(ci)
    with open(dst_data, 'rb') as f:
        f.seek(int(cm['offset']))
        back = np.frombuffer(f.read(int(cm['length'])), dtype='<f2')
    print('readback', ci.dims, cm.get('offset'), cm.get('length'),
          'match', bool((back == wt.reshape(-1)).all()))
    if not bool((back == wt.reshape(-1)).all()):
        raise SystemExit('readback mismatch')
    print('nodes', len(chk.graph.node), 'inits', len(chk.graph.initializer),
          'value_info', len(chk.graph.value_info),
          'transpose left', sum(1 for n in chk.graph.node if n.op_type == 'Transpose'))
    print('DONE_DETRANSP')


if __name__ == '__main__':
    main()
