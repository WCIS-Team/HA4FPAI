"""HF reference for the vendored Qwen3-0.6B icraft decode step, run on the school machine.

Three kinds of numbers, so the icraft Host forward has something to be checked against:

  --prefixes "1427;1427,124674"   plain HF forward per prefix, argmax + top5 of the last position
  --zeropast "1427:1"             token 1427 at position 1 with ONE all-zero KV slot per layer
                                  (this is exactly what the already-run icraft step fed:
                                  token_dynamic_fm = [1, 1, 0] -> slot 1, RoPE position 1, mask
                                  open on slots 0 and 1)
  --chain "1427" --steps 4        greedy chain through HF, one token per step, prints every step's
                                  argmax so it can be lined up against host_gen.py's steps.json
  --simulate "1427" --steps 3     greedy chain under the VENDORED convention instead of HF's:
                                  slot 0 is a permanent zero pad that the mask still opens, token i
                                  goes to slot i+1 with RoPE position i+1, token_dynamic_fm element 2
                                  is ignored. Use this to line up with host_gen.py --pos-offset 1.

Usage:
  python ref_hf.py --model D:\\yzd\\WCIS\\MOD\\qwen3\\Qwen3-0.6B --prefixes "1427" --zeropast "1427:1"
  python ref_hf.py --model D:\\yzd\\WCIS\\MOD\\qwen3\\Qwen3-0.6B --chain "1427" --steps 4
"""
import argparse
import json
import os
import re
import sys

import numpy as np
import torch
from transformers import AutoModelForCausalLM

sys.stdout.reconfigure(encoding='utf-8', errors='replace')


def save(tag, logits, args):
    if not args.dump_logits:
        return
    p = os.path.join(args.dump_logits, 'logits_%s.npy' % re.sub(r'[^0-9A-Za-z_.-]', '_', tag))
    np.save(p, logits.detach().float().cpu().numpy())
    print('SAVED', p)


def top(logits, k=5):
    vals, idx = torch.topk(logits.float(), k)
    return [(int(i), round(float(v), 3)) for v, i in zip(vals, idx)]


TOK = {}


def show_text(seq, args, tag):
    if not args.show_text:
        return
    if 't' not in TOK:
        from transformers import AutoTokenizer
        TOK['t'] = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    txt = TOK['t'].decode(seq, skip_special_tokens=False)
    print('TEXT[%s] %r' % (tag, txt))
    sys.stdout.flush()
    return txt


def zero_past(model, n_slots, dtype):
    from transformers.cache_utils import DynamicCache
    cache = DynamicCache()
    cfg = model.config
    head_dim = getattr(cfg, 'head_dim', None) or cfg.hidden_size // cfg.num_attention_heads
    kv = getattr(cfg, 'num_key_value_heads', cfg.num_attention_heads)
    for layer in range(cfg.num_hidden_layers):
        key = torch.zeros(1, kv, n_slots, head_dim, dtype=dtype)
        val = torch.zeros(1, kv, n_slots, head_dim, dtype=dtype)
        cache.update(key, val, layer)
    return cache


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', required=True)
    ap.add_argument('--prefixes', default='')
    ap.add_argument('--zeropast', default='', help='token:position with that many zero KV slots')
    ap.add_argument('--chain', default='')
    ap.add_argument('--simulate', default='', help='greedy chain under the vendored net convention')
    ap.add_argument('--teacher', action='store_true',
                    help='with --simulate: feed the listed tokens as-is instead of the greedy argmax')
    ap.add_argument('--pad-slots', type=int, default=1,
                    help='with --simulate: how many all-zero KV slots to keep inside the opened mask '
                         '(the vendored net has 1 permanent pad slot; 0 = ordinary HF cache)')
    ap.add_argument('--pos-base', type=int, default=1,
                    help='with --simulate: RoPE position of the first token (vendored net = 1, HF = 0)')
    ap.add_argument('--steps', type=int, default=4)
    ap.add_argument('--show-text', action='store_true',
                    help='decode the final token sequence and print/record it as text')
    ap.add_argument('--out', default='')
    ap.add_argument('--dump-logits', default='', help='directory for full fp32 logits as .npy')
    args = ap.parse_args()

    dev = 'cuda' if torch.cuda.is_available() else 'cpu'
    model = AutoModelForCausalLM.from_pretrained(args.model, torch_dtype=torch.float32,
                                                 attn_implementation='eager',
                                                 trust_remote_code=True)
    model.eval()
    if dev == 'cuda':
        model = model.to(dev)
    print('device', dev, 'layers', model.config.num_hidden_layers,
          'kv_heads', getattr(model.config, 'num_key_value_heads', None),
          'head_dim', getattr(model.config, 'head_dim', None),
          'transformers', __import__('transformers').__version__, 'torch', torch.__version__)

    result = {}
    with torch.no_grad():
        for pre in [p for p in args.prefixes.split(';') if p.strip()]:
            ids = [[int(t) for t in pre.split(',') if t.strip()]]
            out = model(input_ids=torch.tensor(ids, device=dev))
            lg = out.logits[0, -1]
            am = int(torch.argmax(lg))
            result['prefix:' + pre] = {'argmax': am, 'top5': top(lg)}
            save('prefix_' + pre, lg, args)
            print('PREFIX', pre, '->', am, 'top5', top(lg))

        if args.zeropast:
            tok, pos = args.zeropast.split(':')
            tok, pos = int(tok), int(pos)
            dtype = next(model.parameters()).dtype
            cache = zero_past(model, pos, dtype)
            ids = torch.tensor([[tok]], device=dev)
            out = model(input_ids=ids,
                        past_key_values=cache,
                        position_ids=torch.tensor([[pos]], device=dev),
                        attention_mask=torch.ones(1, pos + 1, device=dev, dtype=torch.long))
            lg = out.logits[0, -1]
            am = int(torch.argmax(lg))
            result['zeropast:%d:%d' % (tok, pos)] = {'argmax': am, 'top5': top(lg)}
            save('zeropast_%d_%d' % (tok, pos), lg, args)
            print('ZEROPAST token %d position %d with %d zero slots -> %d  top5 %s'
                  % (tok, pos, pos, am, top(lg)))

        if args.chain:
            seq = [int(t) for t in args.chain.split(',') if t.strip()]
            prompt = list(seq)
            steps = []
            for i in range(len(prompt) + args.steps):
                ids = torch.tensor([seq], device=dev)
                out = model(input_ids=ids)
                lg = out.logits[0, -1]
                nxt = int(torch.argmax(lg))
                steps.append({'step': i, 'fed': ids[0, -1].item(), 'n': len(seq),
                              'argmax': nxt, 'top5': top(lg)})
                save('chain_n%d' % len(seq), lg, args)
                print('CHAIN step %2d n=%d fed %-7d -> %-7d top5 %s'
                      % (i, len(seq), ids[0, -1].item(), nxt, top(lg)))
                seq.append(nxt)
                if i >= len(prompt) - 1 and nxt in (151645, 151643):
                    print('stop token at step', i)
                    break
            result['chain'] = {'prompt': prompt, 'steps': steps, 'sequence': seq}
            result['chain']['text'] = show_text(seq, args, 'chain')

        if args.simulate:
            # HF-side replica of the vendored net's convention, proven by probe: slot 0 is a
            # permanent all-zero pad that the mask still opens, token i is written at slot i+1 and
            # gets RoPE position i+1, and element 2 of token_dynamic_fm is ignored entirely.
            dtype = next(model.parameters()).dtype
            pad = args.pad_slots
            cache = zero_past(model, pad, dtype) if pad else None
            seq = [int(t) for t in args.simulate.split(',') if t.strip()]
            prompt = list(seq)
            steps = []
            # --teacher: feed exactly the tokens given, never the greedy argmax. Needed to line up
            # with host_gen.py, whose step-0 argmax differs from HF's by fp16 noise.
            n_iter = len(seq) if args.teacher else len(prompt) + args.steps
            for i in range(n_iter):
                ids = torch.tensor([[seq[i]]], device=dev)
                out = model(input_ids=ids, past_key_values=cache, use_cache=True,
                            position_ids=torch.tensor([[i + args.pos_base]], device=dev),
                            attention_mask=torch.ones(1, i + pad + 1, device=dev, dtype=torch.long))
                lg = out.logits[0, -1]
                nxt = int(torch.argmax(lg))
                cache = out.past_key_values
                steps.append({'step': i, 'fed': seq[i], 'position': i + args.pos_base,
                              'cache_slots': i + pad + 1, 'pad_slots': pad,
                              'pos_base': args.pos_base, 'argmax': nxt, 'top5': top(lg)})
                print('SIM step %2d pos %-3d pad %d fed %-7d -> %-7d top5 %s'
                      % (i, i + args.pos_base, pad, seq[i], nxt, top(lg)))
                sys.stdout.flush()
                tag = 'teach_step%d' if args.teacher else 'sim_p%d_b%d_step%d' % (pad, args.pos_base, i)
                save(tag, lg, args)
                # during the prompt phase only the LAST token's argmax is the first generated
                # token; appending every earlier argmax scrambles the stream (see host_gen.py).
                if i >= len(prompt) - 1:
                    seq.append(nxt)
                if i >= len(prompt) - 1 and nxt in (151645, 151643):
                    print('stop token at step', i)
                    break
            result['simulate'] = {'prompt': prompt, 'steps': steps, 'sequence': seq}
            result['simulate']['text'] = show_text(
                seq, args, 'simulate_p%d_b%d' % (pad, args.pos_base))

    if args.out:
        json.dump(result, open(args.out, 'w', encoding='utf-8'), indent=1)
        print('wrote', args.out)


if __name__ == '__main__':
    main()
