"""Emit a metadata-only variant of the vendored Qwen3 ONNX so icraft-parse's model loader can be
probed one declared convention at a time.

  --ir N          rewrite ir_version (the vendor file is 10, our export was patched to 8)
  --opset N       rewrite the ai.onnx opset import (ORT bundled in icraft-parse says official
                  support stops at 17)
  --sln-ms        move every ai.onnx SimplifiedLayerNormalization into the com.microsoft domain

The external-data offsets and the .data blob are untouched, so the 1.2 GB file is hard-linked into
the destination directory rather than copied.
"""
import argparse
import os
import sys

import onnx

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

SRC = os.path.join('S:', os.sep, 'apps', 'llm', 'agentwork', 'qwen3', 'compile',
                   'fmodel', 'qwen3_exported')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dst', required=True, help='fmodel subdir name to create')
    ap.add_argument('--src', default=SRC, help='directory holding model.onnx + model.onnx.data')
    ap.add_argument('--ir', type=int, default=0)
    ap.add_argument('--opset', type=int, default=0)
    ap.add_argument('--sln-ms', action='store_true')
    args = ap.parse_args()

    root = os.path.join('S:', os.sep, 'apps', 'llm', 'agentwork', 'qwen3', 'compile', 'fmodel')
    dst = os.path.join(root, args.dst)
    os.makedirs(dst, exist_ok=True)

    src_onnx = os.path.join(args.src, 'model.onnx')
    m = onnx.load(src_onnx, load_external_data=False)
    print('before ir=%d opset=%s' % (m.ir_version, [(o.domain or 'ai.onnx', o.version) for o in m.opset_import]))

    if args.ir:
        m.ir_version = args.ir
    if args.opset:
        for oi in m.opset_import:
            if oi.domain in ('', 'ai.onnx'):
                oi.version = args.opset
    moved = 0
    if args.sln_ms:
        for n in m.graph.node:
            if n.op_type == 'SimplifiedLayerNormalization' and (n.domain or '') == '':
                n.domain = 'com.microsoft'
                moved += 1
    print('after ir=%d opset=%s sln_moved=%d'
          % (m.ir_version, [(o.domain or 'ai.onnx', o.version) for o in m.opset_import], moved))

    out = os.path.join(dst, 'model.onnx')
    onnx.save(m, out)

    data_link = os.path.join(dst, 'model.onnx.data')
    if not os.path.exists(data_link):
        try:
            os.link(os.path.join(args.src, 'model.onnx.data'), data_link)
            print('hardlinked data', data_link)
        except OSError as e:
            print('hardlink failed (%s), copying' % e)
            import shutil
            shutil.copyfile(os.path.join(args.src, 'model.onnx.data'), data_link)
    for extra in ('genai_config.json',):
        s = os.path.join(args.src, extra)
        if os.path.exists(s):
            with open(s, encoding='utf-8') as f:
                txt = f.read()
            open(os.path.join(dst, extra), 'w', encoding='utf-8', newline='\n').write(txt)

    print('out', out, os.path.getsize(out), 'bytes; data', os.path.getsize(data_link), 'bytes')
    print('DONE_META')


if __name__ == '__main__':
    main()
