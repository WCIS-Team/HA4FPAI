"""Rewrite an exported genai ONNX so its declared conventions match what icraft-parse accepts:
ir_version 8, opset 21, literal head dim in the KV tensors.

Only graph metadata changes; tensor payloads are untouched, so model.onnx.data is copied, not
rewritten.
"""
import argparse
import json
import os
import shutil
import sys

import onnx

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

SYMBOLIC_DIMS = ('kv_cache_dim',)


def fix_value_info(vi, replacements):
    n = 0
    if not vi.type.HasField('tensor_type'):
        return 0
    for dim in vi.type.tensor_type.shape.dim:
        if dim.HasField('dim_param') and dim.dim_param in replacements:
            value = replacements[dim.dim_param]
            dim.ClearField('dim_param')
            dim.dim_value = value
            n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True, help='directory holding model.onnx + model.onnx.data')
    ap.add_argument('--dst', required=True)
    ap.add_argument('--head-dim', type=int, default=0, help='0 = read head_size from genai_config.json')
    ap.add_argument('--opset', type=int, default=21)
    args = ap.parse_args()

    src_onnx = os.path.join(args.src, 'model.onnx')
    if args.head_dim:
        head_dim = args.head_dim
    else:
        g = json.load(open(os.path.join(args.src, 'genai_config.json'), encoding='utf-8'))
        head_dim = int(g['model']['decoder']['head_size'])
    replacements = {d: head_dim for d in SYMBOLIC_DIMS}
    print('head_dim=%d opset_target=%d' % (head_dim, args.opset))

    model = onnx.load(src_onnx, load_external_data=False)
    graph = model.graph

    before = [(o.domain, o.version) for o in model.opset_import]
    for oi in model.opset_import:
        if oi.domain in ('', 'ai.onnx'):
            oi.version = args.opset
    print('opset', before, '->', [(o.domain, o.version) for o in model.opset_import])

    # icraft-parse's bundled onnxruntime rejects ir_version 10 ("max supported IR version: 8")
    print('ir_version', model.ir_version, '-> 8')
    model.ir_version = 8

    containers = [('input', graph.input), ('output', graph.output), ('value_info', graph.value_info)]
    for label, items in containers:
        changed = sum(fix_value_info(vi, replacements) for vi in items)
        print('dims fixed in %-10s %d' % (label, changed))

    os.makedirs(args.dst, exist_ok=True)
    dst_onnx = os.path.join(args.dst, 'model.onnx')
    onnx.save(model, dst_onnx)
    for extra in ('model.onnx.data', 'genai_config.json', 'tokenizer.json', 'tokenizer_config.json',
                  'chat_template.jinja'):
        s = os.path.join(args.src, extra)
        if os.path.exists(s) and extra != 'model.onnx':
            shutil.copyfile(s, os.path.join(args.dst, extra))

    check = onnx.load(dst_onnx, load_external_data=False)
    kv = check.graph.input[2]
    dims = [(d.dim_value if d.HasField('dim_value') else d.dim_param) for d in kv.type.tensor_type.shape.dim]
    print('AFTER in2', kv.name, dims)
    print('data_location', check.graph.initializer[0].data_location)

    full = onnx.load(dst_onnx, load_external_data=True)
    w = full.graph.initializer[0]
    arr = onnx.numpy_helper.to_array(w)
    print('external_data_resolves', w.name, arr.shape, arr.dtype, float(arr.reshape(-1)[0]))
    del full

    print('out', os.path.getsize(dst_onnx), 'bytes, data',
          os.path.getsize(os.path.join(args.dst, 'model.onnx.data')), 'bytes')
    print('DONE_VENDORIZE')


if __name__ == '__main__':
    main()
