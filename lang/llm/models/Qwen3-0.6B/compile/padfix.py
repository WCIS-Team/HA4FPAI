r"""Can the vendored "slot 0 = permanent all-zero KV pad, opened by the mask" convention be saved?

The exported Qwen3-0.6B decoder (ONNX-FP16, then icraft's adapted/ZG artifacts) is locked to that
convention: element 1 of `token_dynamic_fm` is simultaneously the RoPE position, the KV write slot
and the number of mask-opened slots, and feeding 0 crashes the graph (op_id 4525 Reshape). So token 0
lands in slot 1 and slot 0 stays all-zero while the mask still attends to it. Under that convention
the model degenerates to a single repeated token (wiki qwen3-0.6b.md section 2.1), while the same
driver with slot 0 removed produces coherent Chinese.

This script runs the HF fp32/eager model as a stand-in for the compiled net and lets the slot-0
content be chosen per run, so the fix can be picked before touching the compiler or the board.
The cache is rebuilt every step from tensors this script owns, so no HF cache internal is trusted.

Variants (slot of token i = i + shift, RoPE position = i + shift, mask opens 0 .. i + shift):
  vendored  shift=1, slot 0 kept all-zero            = what the compiled net forces today
  copy1     shift=1, slot 0 := copy of slot 1        = driver-only trick, first token counted twice
  copylast  shift=1, slot 0 := copy of newest slot   = driver-only trick, newest token counted twice
  mask0     shift=1, slot 0 all-zero but closed by the mask = what a mask-branch patch would do
  zero      shift=0, no pad slot                     = what a 0-based re-export would do

Usage (school machine, from D:\yzd\WCIS\MOD\qwen3):
  venv\Scripts\python.exe padfix.py --model D:\yzd\WCIS\MOD\qwen3\Qwen3-0.6B --variant copy1 --steps 24
  venv\Scripts\python.exe padfix.py --model ... --variant all --testset --out padsweep_result.json
  venv\Scripts\python.exe padfix.py --model ... --variant copy1 --prompt-ids "151644,872,..." --steps 24

Output: one line per run with the decoded text plus the generated-id list, and a json with the same
plus collapse diagnostics (longest single-token run, distinct tokens in the generated tail).
"""
import argparse
import json
import os
import sys
import time

import numpy as np
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from transformers.cache_utils import DynamicCache

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

CHAT_IDS = [151644, 872, 198, 105043, 100165, 11319, 151645, 198, 151644, 77091, 198]

QUESTIONS = [
    '你是谁？',
    '请介绍一下你自己',
    '天空为什么是蓝色的？',
    '写一句话祝福朋友',
    '1加1等于几？',
    '推荐一本适合入门的书',
    '怎样才能睡得好一点？',
    '请用一句话解释什么是神经网络',
    '今天适合跑步吗？',
    '帮我想三个周末的安排',
]


def cache_to_tensors(cache):
    if getattr(cache, 'layers', None):
        first = cache.layers[0]
        if getattr(first, 'keys', None) is not None:
            return [(l.keys, l.values) for l in cache.layers]
    return [(k, v) for k, v in zip(cache.key_cache, cache.value_cache)]


def tensors_to_cache(pairs):
    cache = DynamicCache()
    for i, (k, v) in enumerate(pairs):
        cache.update(k, v, i)
    return cache


def apply_policy(pairs, variant):
    """Slot 0 is the pad; the policy says what to put in it. Tensors are freshly allocated by HF
    each step, so writing slot 0 in place does not corrupt the caller's cache."""
    if variant == 'copy1':
        for k, v in pairs:
            if k.shape[2] >= 2:
                k[:, 0, :, :] = k[:, 1, :, :]
                v[:, 0, :, :] = v[:, 1, :, :]
    elif variant == 'copylast':
        for k, v in pairs:
            if k.shape[2] >= 2:
                k[:, 0, :, :] = k[:, -1, :, :]
                v[:, 0, :, :] = v[:, -1, :, :]
    return pairs


def collapse_stats(gen_ids):
    if not gen_ids:
        return {'distinct': 0, 'maxrun': 0}
    best = run = 1
    for a, b in zip(gen_ids, gen_ids[1:]):
        run = run + 1 if a == b else 1
        best = max(best, run)
    return {'distinct': len(set(gen_ids)), 'maxrun': best, 'n': len(gen_ids)}


def seed_pad(model, dev):
    """Slot 0 as the compiled net holds it: allocated, never written, all zeros. Cache length is
    then one more than the number of tokens fed, exactly like ref_hf.py --pad-slots 1."""
    cfg = model.config
    nh = cfg.num_key_value_heads
    hd = getattr(cfg, 'head_dim', None) or cfg.hidden_size // cfg.num_attention_heads
    dt = next(model.parameters()).dtype
    z = lambda: torch.zeros(1, nh, 1, hd, device=dev, dtype=dt)
    return [(z(), z()) for _ in range(cfg.num_hidden_layers)]


def run_variant(model, tok, ids, variant, steps, dev):
    """Greedy decode one step at a time under the chosen cache convention."""
    shift = 0 if variant == 'zero' else 1
    pairs = seed_pad(model, dev) if shift else []
    gen = []
    t0 = time.time()
    for i in range(len(ids) + steps):
        token = ids[i] if i < len(ids) else gen[-1]
        pos = i + shift
        past_len = i + shift
        cache = tensors_to_cache(pairs) if pairs else None
        mask = torch.ones(1, past_len + 1, device=dev, dtype=torch.long)
        if variant == 'mask0':
            mask[0, 0] = 0
        out = model(input_ids=torch.tensor([[token]], device=dev),
                    past_key_values=cache, use_cache=True,
                    position_ids=torch.tensor([[pos]], device=dev),
                    attention_mask=mask)
        pairs = apply_policy(cache_to_tensors(out.past_key_values), variant)
        lg = out.logits[0, -1].float()
        nxt = int(torch.argmax(lg))
        if i >= len(ids) - 1:
            gen.append(nxt)
        if nxt in (151645, 151643):
            break
    text = tok.decode(gen, skip_special_tokens=False)
    stats = collapse_stats(gen)
    print('%-9s steps=%-3d %.1fs  distinct=%-3d maxrun=%-3d  %r'
          % (variant, len(gen), time.time() - t0, stats['distinct'], stats['maxrun'], text))
    sys.stdout.flush()
    return {'variant': variant, 'shift': shift, 'gen_ids': gen, 'text': text,
            'stats': stats, 'wall_s': round(time.time() - t0, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', required=True)
    ap.add_argument('--variant', default='all',
                    help='all | vendored | copy1 | copylast | mask0 | zero')
    ap.add_argument('--steps', type=int, default=24)
    ap.add_argument('--prompt-ids', default='')
    ap.add_argument('--testset', action='store_true', help='run the QUESTIONS chat prompts')
    ap.add_argument('--out', default='')
    args = ap.parse_args()

    dev = 'cuda' if torch.cuda.is_available() else 'cpu'
    tok = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(args.model, dtype=torch.float32,
                                                 attn_implementation='eager',
                                                 trust_remote_code=True)
    model.eval()
    if dev == 'cuda':
        model = model.to(dev)
    print('device', dev, 'transformers', __import__('transformers').__version__,
          'torch', torch.__version__, 'layers', model.config.num_hidden_layers,
          'kv_heads', getattr(model.config, 'num_key_value_heads', None),
          'head_dim', getattr(model.config, 'head_dim', None) or
          model.config.hidden_size // model.config.num_attention_heads)

    variants = (['vendored', 'copy1', 'copylast', 'mask0', 'zero'] if args.variant == 'all'
                else [args.variant])
    prompts = []
    if args.testset:
        for q in QUESTIONS:
            ids = tok.apply_chat_template([{'role': 'user', 'content': q}],
                                          add_generation_prompt=True, tokenize=False)
            prompts.append({'q': q, 'ids': tok(ids, add_special_tokens=False)['input_ids']})
    if args.prompt_ids:
        prompts.append({'q': '--prompt-ids',
                        'ids': [int(x) for x in args.prompt_ids.split(',') if x.strip()]})
    if not prompts:
        prompts.append({'q': '你是谁？', 'ids': CHAT_IDS})

    result = {}
    with torch.no_grad():
        for pr in prompts:
            key = pr['q']
            result[key] = {'prompt_ids': pr['ids'],
                           'prompt_text': tok.decode(pr['ids'], skip_special_tokens=False)}
            print('\n=== PROMPT %r n=%d' % (key, len(pr['ids'])))
            for v in variants:
                result[key][v] = run_variant(model, tok, pr['ids'], v, args.steps, dev)

    if args.out:
        json.dump(result, open(args.out, 'w', encoding='utf-8'), indent=1, ensure_ascii=False)
        print('wrote', args.out)


if __name__ == '__main__':
    main()
