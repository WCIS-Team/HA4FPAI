"""Read-only trace of icraft's IR json: what the fused control input actually is, and which ops read it.

Used to decide where a "close slot 0 in the mask" patch can live. Element types and shapes are
printed verbatim from the json so the [1, 3] convention can be checked against the ONNX graph that
produced it (see masktrace.py for the ONNX side).

Usage:
  python fmtrace.py <stage.json>                 # Input/Output decl + ops reading the control tensors
  python fmtrace.py <stage.json> --op CausalMask # dump every op whose _type_key ends with that
"""
import argparse
import json
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')


def spec(o):
    d = o.get('dtype') or {}
    return '%s%s' % (d.get('element_dtype'), d.get('shape'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('jsonpath')
    ap.add_argument('--op', default='')
    ap.add_argument('--trace', type=int, default=-1, help='BFS consumers of this v_id')
    ap.add_argument('--fans', action='store_true')
    args = ap.parse_args()

    d = json.load(open(args.jsonpath, encoding='utf-8'))
    ops = d['ops']
    prod = {}
    cons = {}
    for op in ops:
        for o in op.get('outputs') or []:
            prod[o['v_id']] = op
        for o in op.get('inputs') or []:
            cons.setdefault(o['v_id'], []).append(op)

    print('ops', len(ops))
    ins = ops[0]
    print('INPUT OP', ins['_type_key'], 'op_id', ins.get('op_id'))
    for o in sorted(ins['outputs'], key=lambda x: x['v_id'])[:6]:
        print('  v_id %-4s %-24s %s' % (o['v_id'], o.get('name'), spec(o)))
    print('  ... total inputs', len(ins['outputs']))

    types = {}
    for op in ops:
        k = op['_type_key'].split('.')[-1]
        types[k] = types.get(k, 0) + 1
    print('\nOP TYPES')
    for k in sorted(types, key=lambda x: -types[x]):
        print('  %-30s %d' % (k, types[k]))

    for target in [x for x in ('CausalMask', 'RopeDynamic', 'Mask') if any(t.endswith(x) for t in types)]:
        sel = [op for op in ops if op['_type_key'].endswith(target)]
        print('\n%s ops: %d' % (target, len(sel)))
        for op in sel[:3]:
            print('  op_id %s %s' % (op.get('op_id'), op['_type_key']))
            for o in op.get('inputs') or []:
                p = prod.get(o['v_id'])
                print('    in  v_id %-5s %-22s %-26s producer=%s%s'
                      % (o['v_id'], o.get('name'), spec(o),
                         p['_type_key'] if p else 'GRAPH-INPUT',
                         ' op_id %s' % p.get('op_id') if p else ''))
            for o in op.get('outputs') or []:
                print('    out v_id %-5s %-22s %-26s consumers=%s'
                      % (o['v_id'], o.get('name'), spec(o),
                         [c['_type_key'].split('.')[-1] + ':' + str(c.get('op_id'))
                          for c in cons.get(o['v_id'], [])][:6]))

    if args.trace >= 0:
        print('\n--trace v_id %d' % args.trace)
        seen, stack = set(), [(args.trace, 0)]
        while stack:
            vid, depth = stack.pop()
            for c in cons.get(vid, []):
                if id(c) in seen or depth > 6:
                    continue
                seen.add(id(c))
                ins = ['%s:%s%s' % (o['v_id'], o.get('name'), spec(o)) for o in c.get('inputs') or []]
                print('  %s%-30s op_id %-6s in=%s -> %s'
                      % ('    ' * depth, c['_type_key'].split('.')[-1], c.get('op_id'),
                         ', '.join(ins), [(o['v_id'], spec(o)) for o in c.get('outputs') or []]))
                for o in c.get('outputs') or []:
                    stack.append((o['v_id'], depth + 1))

    if args.op:
        sel = [op for op in ops if op['_type_key'].endswith(args.op)]
        print('\n--op %s : %d' % (args.op, len(sel)))
        for op in sel[:4]:
            print('  op_id %s %s' % (op.get('op_id'), op['_type_key']))
            for o in op.get('inputs') or []:
                p = prod.get(o['v_id'])
                print('    in  v_id %-5s %-22s %-26s <- %s'
                      % (o['v_id'], o.get('name'), spec(o),
                         (p['_type_key'] + ' op_id ' + str(p.get('op_id'))) if p else 'GRAPH-INPUT'))
            for o in op.get('outputs') or []:
                print('    out v_id %-5s %-22s %s' % (o['v_id'], o.get('name'), spec(o)))


if __name__ == '__main__':
    main()
