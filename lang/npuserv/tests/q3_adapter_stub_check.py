#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run the Qwen3 npuserv adapter against a stub q3_infer_board -- PC only, no NPU.

The 件 has never emitted a token (台账 #212 stops at `apply`), so the only thing this can
settle on a PC is the *shape of the calls*: which token is fed at which position, where the
graph writes the new KV entry, whether the cache ends up contiguous, and that
`token_dynamic_fm` is 3 fp32 (12 B -- #196, the one change the Qwen2 vendor script forced).
The stub is written attribute-for-attribute against `patch/q3_infer_board.py`
(ModelConfig / IcraftModel / DecodeOnlySessionManager._forward / decode_network.ops[0].outputs),
and it records every call to a JSONL file, because the worker's copy of the stub module is
invisible to this process.

What this does NOT cover: any arithmetic the real graph does.  The logits here are
`argmax = fed token + 1` by construction.  Positions and cache geometry are real
consequences of the adapter; the numbers inside are invented.

    python -X utf8 lang/npuserv/tests/q3_adapter_stub_check.py
"""
import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
NPUSERV = os.path.dirname(HERE)                      # lang/npuserv
LANG = os.path.dirname(NPUSERV)                       # lang/, the registry's "root"
Q3 = os.path.join(LANG, "llm", "models", "Qwen3-0.6B")
ADAPTER = os.path.join(Q3, "board", "npuserv_adapter.py")
DRIVER = os.path.join(Q3, "patch", "q3_infer_board.py")

STUB = r'''# -*- coding: utf-8 -*-
# Stub of q3_infer_board.py.  Names match the real driver; the maths does not exist here.
import json
import os

import numpy as np

IM_HEAD = "<|im_start|>|user\n"
IM_TAIL = "<|im_end|>|\nassistant\n"
FEEDS = os.environ.get("Q3STUB_FEEDS", "")


class ModelConfig(object):
    # Defaults copied from the real driver so an adapter that reads a name that does
    # not exist there shows up as a value mismatch, not as a missing key.
    config_path = "/stub/qwen3"
    decode_json = "/stub/Q3V1-decoder_ZG.json"
    decode_raw = "/stub/Q3V1-decoder_ZG.raw"
    max_decode_length = 512
    max_new_tokens = 32
    dtype_kv = np.float16
    run_backend = "zg330"
    ip = "127.0.0.1"
    mmu_mode = True
    cuda_mode = False
    vocab_size = 151936
    stop_ids = (151645, 151643)
    n_layers = 28
    n_kv_heads = 8
    head_dim = 128


class _Enc(object):
    def __init__(self, ids):
        self.ids = list(ids)


class _Tok(object):
    # The template is spelled here independently of the adapter; if both agree on the
    # split, the wrapped string really is the vendored one.
    def encode(self, text):
        if text.startswith(IM_HEAD) and text.endswith(IM_TAIL) \
                and len(text) >= len(IM_HEAD) + len(IM_TAIL):
            return _Enc([len(text[len(IM_HEAD):len(text) - len(IM_TAIL)])])
        return _Enc([0])

    def decode(self, ids):
        return "tok:" + ",".join(str(int(t)) for t in ids)


class IcraftModel(object):
    def __init__(self, config):
        self.config = config
        self.tokenizer = None
        self.device = None

    def load_model(self):
        self.tokenizer = _Tok()
        return self

    def init_device(self):
        self.device = object()
        return self

    def reset_device(self):
        pass

    def _load_network(self, json_path, raw_path):
        return None


class _TT(object):
    def __init__(self, shape):
        self.shape = list(shape)
        self.element_dtype = '"@fp(16)"'


class _Val(object):
    def __init__(self, shape):
        self._s = shape

    def tensorType(self):
        return _TT(self._s)


class _Op(object):
    def __init__(self, outputs):
        self.outputs = outputs


class _Net(object):
    def __init__(self, outputs):
        self.ops = [_Op(outputs)]


class DecodeOnlySessionManager(object):
    def __init__(self, model):
        self.model = model
        model.load_model().init_device()
        c = model.config
        self.depth = int(c.max_decode_length)
        self.vocab = int(c.vocab_size)
        kv_shape = [1, c.n_kv_heads, self.depth, c.head_dim]
        # The input op declares input_ids, token_dynamic_fm, then n_layers*2 past tensors.
        outs = [_Val([1, 1]), _Val([1, 3])] + [_Val(kv_shape)] * (2 * int(c.n_layers))
        self.decode_network = _Net(outs)

    def _forward(self, input_ids, token_dynamic_fm, kv_cache):
        a_ids = np.asarray(input_ids)
        a_tdf = np.asarray(token_dynamic_fm)
        token = int(a_ids.reshape(-1)[0])
        pos = int(a_tdf.reshape(-1)[1])
        nz = []
        if len(kv_cache):
            arr = np.asarray(kv_cache[0])
            nz = [int(i) for i in range(arr.shape[2]) if np.any(arr[:, :, i, :])]
        if FEEDS:
            with open(FEEDS, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"token": token, "pos": pos, "tdf_len": int(a_tdf.size),
                                     "tdf_dtype": str(a_tdf.dtype),
                                     "ids_dtype": str(a_ids.dtype), "nz": nz}) + "\n")
        logits = np.zeros((1, 1, self.vocab), dtype=np.float32)
        logits[0, 0, (token + 1) % self.vocab] = 4.0
        out_kv = [np.array(a, copy=True) for a in kv_cache]
        for a in out_kv:
            a[:, :, pos, :] = token + 2          # a nonzero marker, so nz can be read back
        return [logits] + out_kv

    def sample_logits(self, logits):
        return int(np.argmax(logits))

    def _prepare_kv_cache(self, batch_size, seq_len):
        c = self.model.config
        return [np.zeros((batch_size, c.n_kv_heads, seq_len, c.head_dim), dtype=c.dtype_kv)
                for _ in range(c.n_layers * 2)]
'''

fails = []
ok = {"c": 0}

SMALL = {"n_layers": 3, "n_kv_heads": 2, "head_dim": 4, "vocab_size": 16,
         "max_decode_length": 64, "stop_ids": [15]}


def check(name, cond, detail=""):
    if cond:
        ok["c"] += 1
        print("PASS  %s" % name)
    else:
        fails.append(name)
        print("FAIL  %s  %s" % (name, detail))


def call(conn, method, path, body=None):
    data = None if body is None else (json.dumps(body) + "\n").encode("utf-8")
    conn.request(method, path, data, {"Content-Type": "application/json"} if data else {})
    r = conn.getresponse()
    raw = r.read().decode("utf-8")
    try:
        return r.status, json.loads(raw)
    except Exception:
        return r.status, {"_raw": raw[:400]}


def main():
    tmp = tempfile.mkdtemp(prefix="npuserv-q3-")
    stub_dir = os.path.join(tmp, "stub")
    os.makedirs(stub_dir)
    with open(os.path.join(stub_dir, "q3_infer_board.py"), "w", encoding="utf-8") as fh:
        fh.write(STUB)
    feeds = os.path.join(tmp, "feeds.jsonl")
    registry = {
        "proto": 1,
        "models": [{
            # Absolute on purpose: the server resolves "adapter" against
            # dirname(dirname(registry)), and this registry lives in a temp dir.
            "id": "qwen3-stub", "adapter": ADAPTER,
            "defaults": {"driver_dir": stub_dir, "max_new": 4},
            "env": {"Q3STUB_FEEDS": feeds},
            "note": "test fixture: real adapter, stub q3_infer_board",
        }],
    }
    reg_path = os.path.join(tmp, "models.json")
    with open(reg_path, "w", encoding="utf-8") as fh:
        json.dump(registry, fh)

    free = socket.socket()
    free.bind(("127.0.0.1", 0))
    port = free.getsockname()[1]
    free.close()
    srv = subprocess.Popen(
        [sys.executable, "-u", os.path.join(NPUSERV, "server.py"), "--port", str(port),
         "--registry", reg_path, "--state-dir", os.path.join(tmp, "state")],
        stdin=subprocess.DEVNULL, stdout=open(os.path.join(tmp, "server.log"), "ab"),
        stderr=subprocess.STDOUT, cwd=NPUSERV)
    conn = None

    def read_feeds(from_line=0):
        for _ in range(50):
            if os.path.exists(feeds):
                lines = [ln for ln in open(feeds, encoding="utf-8").read().splitlines()
                         if ln.strip()]
                if len(lines) >= from_line + expected_lines:
                    return [json.loads(ln) for ln in lines[from_line:]]
            time.sleep(0.1)
        return [json.loads(ln) for ln in
                open(feeds, encoding="utf-8").read().splitlines()[from_line:] if ln.strip()]

    expected_lines = 0
    try:
        deadline = time.time() + 30
        while True:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
                code, body = call(conn, "GET", "/health")
                break
            except Exception:
                if time.time() > deadline:
                    print(open(os.path.join(tmp, "server.log"), encoding="utf-8",
                               errors="replace").read()[-2000:])
                    return 2
                time.sleep(0.3)

        # ---------- 0. the stub keeps up with the real driver's names ----------
        src = open(DRIVER, encoding="utf-8").read()
        missing = [n for n in ("class ModelConfig", "class IcraftModel",
                               "class DecodeOnlySessionManager", "def _forward",
                               "self.decode_network", "def load_model", "def init_device",
                               "max_decode_length", "n_layers", "n_kv_heads", "head_dim",
                               "vocab_size", "stop_ids", "dtype_kv", "decode_json",
                               "decode_raw", "config_path", "max_new_tokens")
                   if n not in src]
        check("the stub mirrors names that still exist in patch/q3_infer_board.py",
              not missing, "gone from the driver: %s" % missing)

        # ---------- 1. load (vendor arm = the vendored two loops) ----------
        code, r = call(conn, "POST", "/load", {"model": "qwen3-stub", "params": SMALL})
        info = r.get("info") or {}
        check("Qwen3 adapter reaches ready through the real worker path",
              code == 200 and r.get("loaded"), str(r)[:300])
        check("geometry is read off the graph, not off the config",
              info.get("declared_inputs") == 8 and info.get("past_inputs") == 6
              and info.get("kv_depth_from_graph") == 64 and info.get("head_dim") == 4
              and info.get("n_kv_heads") == 2 and info.get("vocab_size") == 16,
              str(info)[:300])
        check("info carries the unresolved status and the active position arm",
              info.get("pos_rule") == "vendor" and info.get("read") == "whole"
              and "never executed" in str(info.get("verified"))
              and "#212" in str(info.get("verified")), str(info)[:300])
        check("the 件 paths the driver hard-codes are visible in info",
              info.get("json") == "/stub/Q3V1-decoder_ZG.json"
              and info.get("raw") == "/stub/Q3V1-decoder_ZG.raw", str(info)[:200])

        # ---------- 2. ids path: positions and cache geometry ----------
        code, g = call(conn, "POST", "/generate", {"ids": [10, 11, 12], "max_new": 3})
        expected_lines = 5
        f = read_feeds()
        check("generate answers with the invented chain (argmax = fed + 1)",
              code == 200 and g.get("ids") == [13, 14] and g.get("reason") == "eos"
              and g.get("prompt") == 3 and g.get("gen") == 2, str(g)[:250])
        check("the pre/tok events are paced and the end object is not duplicated",
              g.get("events") == 4, "events=%s" % g.get("events"))
        check("every step feeds 3 fp32 = 12 B in token_dynamic_fm (#196's whole point)",
              len(f) == 5 and all(x["tdf_len"] == 3 and x["tdf_dtype"] == "float32"
                                  for x in f), str(f)[:300])
        check("prompt: token i is fed with position i+1 (vendored run_prompt)",
              [(x["token"], x["pos"]) for x in f[:3]] == [(10, 1), (11, 2), (12, 3)],
              str(f)[:300])
        check("decode: position == slot, no +1 (vendored run_decode)",
              [(x["token"], x["pos"]) for x in f[3:]] == [(13, 3), (14, 4)], str(f)[:300])
        check("the cache stays contiguous -- no gap, no write above the frontier",
              [x["nz"] for x in f] == [[], [0], [0, 1], [0, 1, 2], [0, 1, 2, 3]],
              str([x["nz"] for x in f])[:300])
        check("the reply says which slot was last written",
              g.get("last_slot") == 4 and g.get("pos_rule") == "vendor", str(g)[:200])

        # ---------- 3. text path: the vendored chat template ----------
        expected_lines = 1
        code, g = call(conn, "POST", "/generate", {"text": "\u4f60\u597d", "max_new": 1})
        f = read_feeds(5)
        check("text in wraps with the vendored template (the stub split it back out)",
              code == 200 and f and f[0]["token"] == 2, str(f)[:200])
        check("text out decodes through the tokenizer the driver loaded",
              g.get("text") == "tok:3", str(g)[:200])

        # ---------- 4. uniform arm ----------
        code, r = call(conn, "POST", "/load", {"model": "qwen3-stub", "evict": True,
                                               "params": dict(SMALL, pos_rule="uniform")})
        check("pos_rule=uniform reloads", code == 200
              and (r.get("info") or {}).get("pos_rule") == "uniform", str(r)[:250])
        os.remove(feeds)
        expected_lines = 5
        code, g = call(conn, "POST", "/generate", {"ids": [10, 11, 12], "max_new": 3})
        f = read_feeds()
        check("uniform: every step feeds slot+1, so the decode phase shifts by one",
              [(x["token"], x["pos"]) for x in f] == [(10, 1), (11, 2), (12, 3),
                                                      (13, 4), (14, 5)], str(f)[:300])
        check("uniform: un-shifting keeps the same contiguous cache",
              [x["nz"] for x in f] == [[], [0], [0, 1], [0, 1, 2], [0, 1, 2, 3]],
              str([x["nz"] for x in f])[:300])

        # ---------- 5. the window gate ----------
        code, r = call(conn, "POST", "/load", {"model": "qwen3-stub", "evict": True,
                                               "params": dict(SMALL, max_decode_length=6,
                                                              stop_ids=[151645])})
        check("a shorter graph window reloads", code == 200
              and (r.get("info") or {}).get("kv_depth_from_graph") == 6, str(r)[:250])
        os.remove(feeds)
        expected_lines = 6
        code, g = call(conn, "POST", "/generate",
                      {"ids": [10, 11, 12, 13, 14], "max_new": 4})
        f = read_feeds()
        check("running out of slots answers reason=full, not a crash",
              code == 200 and g.get("reason") == "full" and g.get("gen") == 1
              and g.get("prompt") == 5, str(g)[:250])
        check("nothing is ever fed at position 0 or position == depth (op_id 4525)",
              all(1 <= x["pos"] < 6 for x in f), str([x["pos"] for x in f])[:200])

        # ---------- 6. a bad arm is refused, not silently defaulted ----------
        code, r = call(conn, "POST", "/load", {"model": "qwen3-stub", "evict": True,
                                               "params": {"pos_rule": "both"}})
        check("an unknown pos_rule fails the load with its name in the error",
              code == 500 and "pos_rule" in r.get("error", ""), str(r)[:250])
        code, st = call(conn, "GET", "/status")
        check("and the failed load leaves the slot idle", st.get("state") == "idle",
              str(st)[:200])
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
        srv.terminate()
        try:
            srv.wait(timeout=15)
        except Exception:
            srv.kill()
    print("\n%d checks passed, %d failed" % (ok["c"], len(fails)))
    if fails:
        print("FAILED: " + ", ".join(fails))
        print("--- server log ---")
        print(open(os.path.join(tmp, "server.log"), encoding="utf-8",
                   errors="replace").read()[-2500:])
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
