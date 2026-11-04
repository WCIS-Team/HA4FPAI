"""Read-only trace of how the vendored export builds the attention mask from token_dynamic_fm.

The compiled net forces "slot 0 = all-zero KV pad that the mask still opens" (see padfix.py for the
decision data). The fix that survives is to close slot 0 in the mask, so this script maps the op
chain from the [1, 3] control input to every node that feeds an attention softmax.

Usage:
  python masktrace.py <model.onnx>            # inputs, op-type census, mask-named nodes
  python masktrace.py <model.onnx> --fans     # BFS consumer fan-out from the control input
"""
import sys

import onnx

sys.stdout.reconfigure(encoding='utf-8', errors='replace')


def shape(t):
    d = t.type.tensor_type.shape
    return [x.dim_value if x.HasField('dim_value') else x.dim_param for x in d.dim]


def main():
    path = sys.argv[1]
    m = onnx.load(path, load_external_data=False)
    g = m.graph
    print('ir_version', m.ir_version, 'opset', [(o.domain, o.version) for o in m.opset_import])
    print('n_nodes', len(g.node))
    print('INPUTS')
    for i in list(g.input) + list(g.initializer)[:0]:
        print('  %-28s %-18s elem=%d' % (i.name, str(shape(i)), i.type.tensor_type.elem_type))
    print('OUTPUTS')
    for o in g.output:
        print('  %-28s %-18s elem=%d' % (o.name, str(shape(o)), o.type.tensor_type.elem_type))

    prod = {}
    cons = {}
    for n in g.node:
        for o in n.output:
            prod[o] = n
        for i in n.input:
            cons.setdefault(i, []).append(n)

    print('\nOP CENSUS')
    cnt = {}
    for n in g.node:
        cnt[n.op_type] = cnt.get(n.op_type, 0) + 1
    for k in sorted(cnt, key=lambda x: -cnt[x]):
        print('  %-22s %d' % (k, cnt[k]))

    print('\nNODES WITH mask/causal/rope/dynamic/slice IN NAME')
    for n in g.node:
        tag = (n.name or '') + '|' + '|'.join(list(n.output))
        if any(k in tag.lower() for k in ('mask', 'causal', 'rope', 'dynamic')):
            print('  %-16s %-34s in=%s out=%s' % (n.op_type, n.name, list(n.input), list(n.output)))

    if '--fans' in sys.argv:
        ctrl = [i.name for i in g.input if any(k in i.name.lower()
                for k in ('dynamic', 'position', 'attention', 'mask'))]
        for start in ctrl:
            print('\nFAN-OUT from %r' % start)
            seen, stack = set(), [(start, 0)]
            while stack:
                name, depth = stack.pop()
                for c in cons.get(name, []):
                    if id(c) in seen or depth > 12:
                        continue
                    seen.add(id(c))
                    print('  %s%-16s %-36s -> %s'
                          % ('    ' * depth, c.op_type, c.name, list(c.output)))
                    for co in c.output:
                        stack.append((co, depth + 1))


if __name__ == '__main__':
    main()
