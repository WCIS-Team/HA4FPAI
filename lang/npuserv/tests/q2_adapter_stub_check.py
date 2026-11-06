#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run the Qwen2 npuserv adapter against a stub driver -- PC only, no NPU.

What a PC can settle here is the *shape of the calls*, because the adapter drives the
vendored script's own two entry points instead of re-implementing them:

  prefill  session.process_prompt_character_by_character(ids)   tdf = [1, i+1, 0]
  decode   session.run_decode(tok, kv, pos, generated)          pos = prompt_len + step - 1

The stub mirrors `board/qwen2_infer_decode_only_axi_pad3.py` name for name (ModelConfig /
IcraftModel / DecodeOnlySessionManager._prepare_kv_cache / process_prompt_character_by_character
/ run_decode / sample_logits / ContentFilter) and records every feed to JSONL, because the
worker's copy of the stub is invisible to this process.

Not covered: any arithmetic.  Logits are `argmax = fed token + 1` by construction; the
positions and the cache frontier are real consequences of the adapter.

    python -X utf8 lang/npuserv/tests/q2_adapter_stub_check.py
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
NPUSERV = os.path.dirname(HERE)
LANG = os.path.dirname(NPUSERV)
Q2 = os.path.join(LANG, "llm", "models", "Qwen2")
ADAPTER = os.path.join(Q2, "board", "npuserv_adapter.py")
DRIVER = os.path.join(Q2, "board", "qwen2_infer_decode_only_axi_pad3.py")

STUB = r'''# -*- coding: utf-8 -*-
# Stub of qwen2_infer_decode_only_axi_pad3.py.  Names match the real driver; no maths.
import json
import os

import numpy as np

IM_HEAD = "<|im_start|>\n"
IM_TAIL = "<|im_end|>assistant\n"
FEEDS = os.environ.get("Q2STUB_FEEDS", "")


class ModelConfig(object):
    config_path = "/stub/qwen2/config"
    decode_json = "/stub/Qwen2-0.5B-decoder_ZG.json"
    decode_raw = "/stub/Qwen2-0.5B-decoder_ZG.raw"
    max_decode_length = 512
    max_new_tokens = 150
    dtype_kv = np.float16
    run_backend = "zg330"
    ip = "127.0.0.1"
    mmu_mode = True
    cuda_mode = False
    vocab_size = 151936
    sampling_method = "greedy"
    sampling_value = 50
    temperature = 1.0
    repetition_penalty = 1.2


class _Enc(object):
    def __init__(self, ids):
        self.ids = list(ids)


class _Tok(object):
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


class ContentFilter(object):
    def __init__(self):
        self.called = 0

    def should_stop_generation(self, generated, tokenizer, step):
        self.called += 1
        return False


class DecodeOnlySessionManager(object):
    def __init__(self, model):
        self.model = model
        model.load_model().init_device()

    def _prepare_kv_cache(self, batch_size, seq_len):
        c = self.model.config
        shape = (batch_size, 2, seq_len, 4)
        return [np.zeros(shape, dtype=c.dtype_kv) for _ in range(6)]

    def _record(self, token, pos, phase, kv):
        arr = np.asarray(kv[0])
        nz = [int(i) for i in range(arr.shape[2]) if np.any(arr[:, :, i, :])]
        if FEEDS:
            with open(FEEDS, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"token": token, "pos": pos, "phase": phase,
                                     "nz": nz}) + "\n")

    def sample_logits(self, logits, generated_tokens=None, sampling_method="greedy",
                      sampling_value=None, temperature=1.0, repetition_penalty=1.0):
        return np.array([int(np.argmax(np.asarray(logits).reshape(-1)))])

    def process_prompt_character_by_character(self, prompt_tokens):
        kv = self._prepare_kv_cache(1, self.model.config.max_decode_length)
        last = prompt_tokens[-1]
        for i, tok in enumerate(prompt_tokens):
            self._record(tok, i + 1, "prompt", kv)
            for a in kv:
                a[:, :, i, :] = tok + 2
            last = tok
        logits = np.zeros((1, 1, self.model.config.vocab_size), dtype=np.float32)
        logits[0, 0, (last + 1) % self.model.config.vocab_size] = 4.0
        return int(self.sample_logits(logits)[0]), kv

    def run_decode(self, input_token, kv_cache, seq_pos, generated_tokens):
        self._record(input_token, seq_pos, "decode", kv_cache)
        out = [np.array(a, copy=True) for a in kv_cache]
        for a in out:
            a[:, :, seq_pos, :] = input_token + 2
        logits = np.zeros((1, 1, self.model.config.vocab_size), dtype=np.float32)
        logits[0, 0, (input_token + 1) % self.model.config.vocab_size] = 4.0
        return int(self.sample_logits(logits)[0]), out
'''

fails = []
ok = {"c": 0}


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
    tmp = tempfile.mkdtemp(prefix="npuserv-q2-")
    stub_dir = os.path.join(tmp, "stub")
    os.makedirs(stub_dir)
    with open(os.path.join(stub_dir, "qwen2_infer_decode_only_axi_pad3.py"), "w",
              encoding="utf-8") as fh:
        fh.write(STUB)
    feeds = os.path.join(tmp, "feeds.jsonl")
    registry = {
        "proto": 1,
        "models": [{
            "id": "qwen2-stub", "adapter": ADAPTER,
            "defaults": {"driver_dir": stub_dir, "max_new": 3, "stop_ids": [15]},
            "env": {"Q2STUB_FEEDS": feeds},
            "note": "test fixture: real adapter, stub qwen2 driver",
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
    expected = 0

    def read_feeds(from_line=0, want=None):
        want = expected if want is None else want
        for _ in range(50):
            if os.path.exists(feeds):
                lines = [ln for ln in open(feeds, encoding="utf-8").read().splitlines()
                         if ln.strip()]
                if len(lines) >= from_line + want:
                    return [json.loads(ln) for ln in lines[from_line:]]
            time.sleep(0.1)
        return []

    try:
        deadline = time.time() + 30
        while True:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
                call(conn, "GET", "/health")
                break
            except Exception:
                if time.time() > deadline:
                    print(open(os.path.join(tmp, "server.log"), encoding="utf-8",
                               errors="replace").read()[-2000:])
                    return 2
                time.sleep(0.3)

        src = open(DRIVER, encoding="utf-8").read()
        missing = [n for n in ("class ModelConfig", "class IcraftModel",
                               "class DecodeOnlySessionManager", "class ContentFilter",
                               "def process_prompt_character_by_character", "def run_decode",
                               "def sample_logits", "def _prepare_kv_cache",
                               "max_decode_length", "vocab_size", "dtype_kv",
                               "decode_json", "decode_raw", "config_path")
                   if n not in src]
        check("the stub mirrors names that still exist in the vendored driver",
              not missing, "gone: %s" % missing)

        # ---------- load ----------
        code, r = call(conn, "POST", "/load", {"model": "qwen2-stub", "params": {}})
        info = r.get("info") or {}
        check("adapter reaches ready through the real worker path",
              code == 200 and r.get("loaded"), str(r)[:300])
        check("geometry comes off the cache the driver allocates",
              info.get("cache_tensors") == 6 and info.get("n_layers") == 3
              and info.get("n_kv_heads") == 2 and info.get("head_dim") == 4
              and info.get("kv_depth_in_config") == 512, str(info)[:300])
        check("info names the arms and the unresolved status",
              info.get("stop_ids") == [15] and "position_rule" in info
              and "npuserv yet" in str(info.get("verified")), str(info)[:300])
        check("the driver's hard-coded paths are visible",
              info.get("json") == "/stub/Qwen2-0.5B-decoder_ZG.json", str(info)[:200])

        # ---------- ids path ----------
        code, g = call(conn, "POST", "/generate", {"ids": [10, 11, 12]})
        expected = 5
        f = read_feeds()
        check("the invented chain answers: argmax = fed + 1, stop id ends it",
              code == 200 and g.get("ids") == [13, 14] and g.get("reason") == "eos"
              and g.get("prompt") == 3 and g.get("gen") == 2, str(g)[:250])
        check("one pre event plus one tok event per token, end object counted once",
              g.get("events") == 4, "events=%s" % g.get("events"))
        check("prompt: token i fed with position i+1 (vendored shift)",
              [(x["token"], x["pos"], x["phase"]) for x in f[:3]]
              == [(10, 1, "prompt"), (11, 2, "prompt"), (12, 3, "prompt")], str(f)[:300])
        check("decode: position = prompt_len + step - 1, un-shifted (vendored run_decode)",
              [(x["token"], x["pos"], x["phase"]) for x in f[3:]]
              == [(13, 3, "decode"), (14, 4, "decode")], str(f)[:300])
        check("the cache stays contiguous - no gap, no write past the frontier",
              [x["nz"] for x in f] == [[], [0], [0, 1], [0, 1, 2], [0, 1, 2, 3]],
              str([x["nz"] for x in f])[:300])
        check("the reply reports the last position it wrote",
              g.get("last_pos") == 4, str(g)[:200])

        # ---------- text path ----------
        expected = 1
        code, g = call(conn, "POST", "/generate", {"text": "\u4f60\u597d", "max_new": 1})
        f = read_feeds(5)
        check("text is wrapped in the vendor template (stub split it back out)",
              code == 200 and f and f[0]["token"] == 2, str(f)[:200])
        check("text out decodes through the tokenizer the driver loaded",
              g.get("text") == "tok:3", str(g)[:200])
        expected = 1
        code, g = call(conn, "POST", "/generate", {"ids": [10], "max_new": 1, "decode": False})
        check("decode:false returns ids without text",
              code == 200 and "text" not in g and g.get("ids") == [11], str(g)[:200])

        # ---------- the window gate ----------
        code, r = call(conn, "POST", "/load", {"model": "qwen2-stub", "evict": True,
                                               "params": {"max_decode_length": 6,
                                                          "stop_ids": [99]}})
        check("a shorter window reloads and shows up in info", code == 200
              and (r.get("info") or {}).get("kv_depth_in_config") == 6, str(r)[:250])
        if os.path.exists(feeds):
            os.remove(feeds)
        expected = 6
        code, g = call(conn, "POST", "/generate", {"ids": [10, 11, 12, 13, 14], "max_new": 4})
        f = read_feeds()
        check("max_new is clamped to the free slots, nothing is fed at position >= depth",
              code == 200 and g.get("gen") == 1 and g.get("reason") == "limit"
              and all(x["pos"] < 6 for x in f), str(g)[:200] + str([x["pos"] for x in f])[:120])
        code, r = call(conn, "POST", "/generate", {"ids": [10, 11, 12, 13, 14, 15]})
        check("a prompt that fills the window is refused with a readable error",
              code == 500 and "window" in json.dumps(r, ensure_ascii=False), str(r)[:250])
        code, st = call(conn, "GET", "/status")
        check("the worker survived the refused request", st.get("state") == "ready", str(st)[:200])

        # ---------- unload ----------
        code, r = call(conn, "POST", "/unload", {"model": "qwen2-stub"})
        code, st = call(conn, "GET", "/status")
        check("unload kills the worker and frees the slot",
              r.get("unloaded") and st.get("state") == "idle", str(r)[:200])
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