#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""npuserv adapter: Q3V1-decoder_ZG (Qwen3-0.6B) on the ZG330.

Model-specific steps stay in `patch/q3_infer_board.py` (the port itself: 58 declared
inputs, 28 layers x 2 KV tensors, 8 kv heads, head_dim 128, `token_dynamic_fm` = 3 fp32
because the ZG graph asks for 12 B where the vendor script feeds 8 B -- 台账 #196).  This
adapter only turns a npuserv request into that driver's calls and back.

VERIFICATION STATUS (2026-11-04): never executed on a board.  Not by this adapter, and not
by q3_infer_board.py either -- the 件 passed the four-step precheck to `apply` in 75.1 s
(台账 #212) and has produced zero tokens, because the driver was never on the board
(#208-#211: the 10-14 push carried json + raw + tokenizer only).  A first board run pushes
three things together: `npuserv/`, this adapter, and `patch/q3_infer_board.py`.

What IS settled without a board: the position rule.  The driver's own two loops disagree --
`run_prompt` feeds tdf = slot + 1 and copies out[..., slot + 1, :] into slot, while
`run_decode` feeds tdf = slot and hands the cache back un-shifted.  The one board-validated
statement about where an entry lands is ERNIE's daemon: it feeds [1, past_len, 0]
(e41_chatd.py:151) and reads the new entry back at output index == past_len
(e41_chatd.py:169), i.e. tdf is the write index.  Both vendored arms obey that; they differ
in whether the cache is kept 0-based (prompt arm) or 1-based (decode arm).  tdf can never be
0 for this graph family (element 1 = 0 is unrepresentable, op_id 4525).  So `pos_rule`
chooses between the two arms and nothing else:
  "vendor"  (default) prompt: tdf = slot + 1, un-shift; decode: tdf = slot, no un-shift
                      -- run_prompt + run_decode verbatim, the sequence the Qwen2 chain
                         that produced coherent text on this board actually used
  "uniform"           every step: tdf = slot + 1, un-shift -- cache stays 0-based, and the
                      decode phase is the only place this differs from "vendor"

Readback is whole-tensor (56 x 1 MiB fp16 = 58 MiB per step, the driver's Tensor2Numpy).
The narrow readback worth 25.75 ms/step on ERNIE (台账 §册一) is not implemented for this 件.

Request (POST /generate): {"ids":[...], "max_new":32}
                          {"text":"你是谁", "max_new":32}   # needs the board tokenizer
                          {"pos_rule":"uniform"} at load time to ablate the arm above
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (HERE, os.path.join(HERE, os.pardir, "patch"),
           os.environ.get("NPUSERV_HOME", "")):
    if _p and os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, os.path.abspath(_p))

import adapter_lib                                             # noqa: E402

IM_START = "<" + "|im_start" + "|>"
IM_END = "<" + "|im_end" + "|>"
RULES = ("vendor", "uniform")


class Q3Engine(object):
    name = "qwen3-0.6b"

    def __init__(self, np, cfg, model, sm, params):
        self.np = np
        self.cfg = cfg
        self.model = model
        self.sm = sm
        self.params = params
        self.n_steps = 0
        self.rule = cfg.pos_rule
        self.stop_ids = set(int(t) for t in cfg.stop_ids)
        # KV window straight off the graph, not off the hard-coded config number.
        outputs = sm.decode_network.ops[0].outputs
        spec = list(outputs[2].tensorType().shape)
        self.kv_heads, self.kv_depth, self.head_dim = int(spec[1]), int(spec[2]), int(spec[3])
        self.info = {
            "kv_depth_from_graph": self.kv_depth,
            "max_decode_length_in_config": cfg.max_decode_length,
            "n_layers": cfg.n_layers, "n_kv_heads": self.kv_heads,
            "head_dim": self.head_dim, "vocab_size": cfg.vocab_size,
            "declared_inputs": len(outputs), "past_inputs": len(outputs) - 2,
            "stop_ids": sorted(self.stop_ids), "pos_rule": self.rule,
            "position_rule": "token_dynamic_fm = [1, p, 0] (12 B); new entry lands at out[p]",
            "read": "whole", "json": cfg.decode_json, "raw": cfg.decode_raw,
            "tokenizer": os.path.join(cfg.config_path, "tokenizer.json"),
            "verified": "never executed on board; 台账 #212 stops at apply, zero tokens",
        }

    def _new_cache(self):
        return [self.np.zeros((1, self.kv_heads, self.kv_depth, self.head_dim),
                              dtype=self.cfg.dtype_kv)
                for _ in range(self.cfg.n_layers * 2)]

    def _pos(self, slot, phase):
        """The value fed in token_dynamic_fm[1] -- which is also where the graph writes."""
        return slot + 1 if (self.rule == "uniform" or phase == "prompt") else slot

    def _step(self, token, slot, phase):
        """One decoder-graph call for the token that belongs in cache slot `slot`."""
        p = self._pos(slot, phase)
        if p < 1 or p >= self.kv_depth:
            raise RuntimeError("position %d out of range for kv depth %d" % (p, self.kv_depth))
        ids = self.np.array([[token]], dtype=self.np.float32)
        tdf = self.np.array([[1, p, 0]], dtype=self.np.float32)      # 3 x fp32 = 12 B
        arrays = self.sm._forward(ids, tdf, self.kv)
        logits = self.np.asarray(arrays[0]).reshape(-1)
        new_kv = arrays[1:]
        if p == slot + 1:
            for j, nv in enumerate(new_kv):
                self.kv[j][:, :, slot, :] = nv[:, :, p, :]
        else:
            self.kv = list(new_kv)
        self.n_steps += 1
        return int(self.np.argmax(logits))

    def template(self, text):
        # Same wrapper as the vendored driver's generate() (q3_infer_board.py).
        return IM_START + "|user\n" + str(text) + IM_END + "|\nassistant\n"

    def generate(self, req, emit):
        ids = [int(t) for t in req.get("ids") or []]
        if not ids and req.get("text"):
            ids = list(self.model.tokenizer.encode(self.template(req["text"])).ids)
        if not ids:
            raise ValueError("ids or text required")
        max_new = int(req.get("max_new", self.params.get("max_new", 32)))
        self.kv = self._new_cache()
        t0 = time.time()
        reason, gen = "limit", []
        pos = 0
        nxt = ids[0]
        for i, tok in enumerate(ids):                 # prompt: one token per step
            nxt = self._step(tok, i, "prompt")
            if i % 8 == 7 or i == len(ids) - 1:
                emit({"pre": i + 1, "n": len(ids)})
            pos = i + 1
        for _ in range(max_new):
            if nxt in self.stop_ids:
                reason = "eos"
                break
            if not 1 <= self._pos(pos, "decode") < self.kv_depth:
                reason = "full"
                break
            gen.append(nxt)
            emit({"tok": nxt, "i": len(gen) - 1, "sec": round(time.time() - t0, 2)})
            nxt = self._step(nxt, pos, "decode")
            pos += 1
        out = {"ids": gen, "reason": reason, "prompt": len(ids), "gen": len(gen),
               "sec": round(time.time() - t0, 1), "pos_rule": self.rule,
               "last_slot": pos - 1, "model": self.name}
        if req.get("decode", True):
            try:
                out["text"] = self.model.tokenizer.decode(gen)
            except Exception as e:
                out["text"] = None
                out["decode_error"] = repr(e)
        return out

    def close(self):
        # Session memory returns only when this process exits (台账 §册三); there is no
        # documented release call, so there is nothing to do here.
        pass


def load(params):
    alt = params.get("driver_dir")
    if alt:
        # This adapter sits next to a copy of the driver and also searches ../patch,
        # which is where the正本 lives in the repo.  A fixture (or any other layout)
        # has to name its directory explicitly.
        sys.path.insert(0, os.path.abspath(alt))
    # The board's pip icraft is 3.33.1 (Dec 2024) but the system .deb runtime is 3.39.0
    # (Aug 21) -- the old python binding wants a symbol the new libicraft_xir.so dropped
    # (`icraft::xir::Params::load()`), so `import icraft` fails with undefined symbol.
    # /root/icraft39 holds the 3.39.0 python bindings; put it ahead of site-packages.
    ic39 = os.path.join(os.path.sep, "root", "icraft39")
    if os.path.isdir(ic39) and ic39 not in sys.path:
        sys.path.insert(0, ic39)
    import numpy as np
    import q3_infer_board as Q3

    class Cfg(Q3.ModelConfig):
        pass

    Cfg.config_path = params.get("config_path", Cfg.config_path)
    Cfg.decode_json = params.get("json", Cfg.decode_json)
    Cfg.decode_raw = params.get("raw", Cfg.decode_raw)
    Cfg.max_decode_length = int(params.get("max_decode_length", Cfg.max_decode_length))
    Cfg.n_layers = int(params.get("n_layers", Cfg.n_layers))
    Cfg.n_kv_heads = int(params.get("n_kv_heads", Cfg.n_kv_heads))
    Cfg.head_dim = int(params.get("head_dim", Cfg.head_dim))
    Cfg.vocab_size = int(params.get("vocab_size", Cfg.vocab_size))
    Cfg.max_new_tokens = int(params.get("max_new", Cfg.max_new_tokens))
    if params.get("stop_ids"):
        Cfg.stop_ids = tuple(int(t) for t in params["stop_ids"])
    Cfg.dtype_kv = {"fp16": np.float16, "fp32": np.float32}.get(
        params.get("dtype_kv", "fp16"), np.float16)
    rule = params.get("pos_rule", "vendor")
    if rule not in RULES:
        raise ValueError("pos_rule %r not one of %s" % (rule, list(RULES)))
    Cfg.pos_rule = rule

    cfg = Cfg()
    adapter_lib.log("loading tokenizer + device + decoder net for Qwen3")
    model = Q3.IcraftModel(cfg)
    sm = Q3.DecodeOnlySessionManager(model)      # does load_model + init_device + apply
    return Q3Engine(np, cfg, model, sm, params)


if __name__ == "__main__":
    sys.exit(adapter_lib.run(load))
