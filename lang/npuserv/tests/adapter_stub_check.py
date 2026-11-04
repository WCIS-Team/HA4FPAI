#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run the ERNIE npuserv adapter against a stub e41_chatd -- PC only, no NPU.

The adapter's whole job is to translate between npuserv and `e41_chatd.E41`.  That
translation is testable without a board: fake the engine, keep the attribute names
identical to the real module (specs / gmap / kv_heads / kv_depth / kv_slot_b / apply_s /
n_steps / generate(ids, max_new, emit) and the module-level MAX_SLOTS, PADFIX,
POS_OFFSET, STOP, MAX_NEW_CAP, JSON_, RAW_), and check what the adapter asks for and what
it answers.  What this does NOT cover: any of the real arithmetic -- that stays 台账 §廿六
readings.

    python -X utf8 lang/npuserv/tests/adapter_stub_check.py
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
ADAPTER = os.path.join(LANG, "llm", "ERNIE-4.5-0.3B-PT4icraft430tai",
                       "board", "npuserv_adapter.py")
ERNIE_BOARD = os.path.dirname(ADAPTER)
if ERNIE_BOARD not in sys.path:
    sys.path.insert(0, ERNIE_BOARD)
import e41_tok                                       # noqa: E402  (verifies the board tokenizer on PC)

STUB = '''# -*- coding: utf-8 -*-
"""Stub of e41_chatd.py, attribute-for-attribute with the real module."""
import os
JSON_ = os.environ.get("E41_JSON", "/stub/E41-decoder_ZG.json")
RAW_ = os.environ.get("E41_RAW", "/stub/E41-decoder_ZG.raw")
PADFIX = os.environ.get("E41_PADFIX", "1") == "1"
STOP = {int(t) for t in os.environ.get("E41_STOP", "2,100272").split(",") if t.strip()}
POS_OFFSET = int(os.environ.get("E41_POS_OFFSET", "1"))
MAX_NEW_CAP = int(os.environ.get("E41_MAX_NEW_CAP", "192"))
MAX_SLOTS = 511


class _Spec(object):
    def __init__(self, shape):
        self._s = shape

    def tensorType(self):
        return type("T", (), {"shape": self._s, "element_dtype": '"@fp(16)"'})()


class E41(object):
    def __init__(self):
        self.apply_s = 0.25
        self.n_steps = 0
        self.specs = [_Spec([1, 1]), _Spec([1, 3])] + [_Spec([1, 8, 512, 128])] * 40
        self.gmap = {"n_past": 40, "pair": dict((i, i) for i in range(2, 42))}
        self.kv_heads, self.kv_depth, self.kv_slot_b = 8, 512, 256

    def generate(self, ids, max_new, emit):
        # The worker's copy of this module is invisible to the parent process, so the
        # record of what the adapter actually passed goes out through the reply.
        emit({"pre": len(ids), "n": len(ids)})
        emit({"tok": 11, "i": 0, "sec": 0.42})
        emit({"tok": 22, "i": 1, "sec": 0.42})
        emit({"end": True, "ids": [11, 22], "reason": "eos", "sec": 1.3,
              "prompt": len(ids),
              "call": {"n_ids": len(ids), "max_new": max_new, "padfix": PADFIX,
                       "pos_offset": POS_OFFSET, "max_slots": MAX_SLOTS,
                       "max_new_cap": MAX_NEW_CAP, "stop": sorted(STOP),
                       "json": JSON_}})
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
    tmp = tempfile.mkdtemp(prefix="npuserv-stub-")
    stub_dir = os.path.join(tmp, "stub")
    os.makedirs(stub_dir)
    with open(os.path.join(stub_dir, "e41_chatd.py"), "w", encoding="utf-8") as fh:
        fh.write(STUB)
    registry = {
        "proto": 1,
        "models": [{
            # Absolute on purpose: this registry lives in a temp dir and carries no "root",
            # so a relative "adapter" would resolve against that dir's parent.
            "id": "ernie-stub", "adapter": ADAPTER,
            "defaults": {"max_new": 96, "padfix": True, "pos_offset": 1,
                         "e41_dir": stub_dir},
            "note": "test fixture: real adapter, stub e41_chatd",
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
        code, r = call(conn, "POST", "/load", {"model": "ernie-stub"})
        check("ERNIE adapter reaches ready through the real worker path",
              code == 200 and r.get("loaded"), str(r)[:300])
        info = r.get("info") or {}
        check("info carries the engine's geometry (attribute names match e41_chatd)",
              info.get("kv_heads") == 8 and info.get("kv_depth") == 512
              and info.get("kv_slot_bytes") == 256 and info.get("declared_inputs") == 42
              and info.get("past_inputs") == 40, str(info)[:300])
        check("info reports the 现役 policy numbers",
              info.get("max_slots_policy") == 511 and info.get("max_new_cap") == 192
              and info.get("pos_offset") == 1 and info.get("padfix") is True
              and info.get("stop_ids") == [2, 100272] and info.get("read") == "narrow",
              str(info)[:300])
        check("the imported engine is the stub, seen through the paths its env resolved",
              info.get("json") == "/stub/E41-decoder_ZG.json"
              and info.get("raw") == "/stub/E41-decoder_ZG.raw", str(info)[:200])

        code, g = call(conn, "POST", "/generate", {"ids": [1, 2, 3, 4], "max_new": 7})
        check("generate answers the daemon's end object",
              code == 200 and g.get("ids") == [11, 22] and g.get("reason") == "eos"
              and g.get("prompt") == 4, str(g)[:250])
        check("the end event is not duplicated into the stream",
              g.get("events") == 4, "events=%s" % g.get("events"))
        c1 = g.get("call") or {}
        check("a bare request reaches the engine as the 现役 configuration",
              c1.get("n_ids") == 4 and c1.get("max_new") == 7 and c1.get("padfix") is True
              and c1.get("pos_offset") == 1 and c1.get("max_slots") == 511
              and c1.get("max_new_cap") == 192 and c1.get("stop") == [2, 100272],
              str(g)[:300])

        code, g = call(conn, "POST", "/generate", {"ids": [1, 2]})
        check("defaults from the registry reach the engine when the request omits them",
              code == 200 and (g.get("call") or {}).get("max_new") == 96, str(g)[:250])

        enc = e41_tok.load().encode("你好")
        code, g = call(conn, "POST", "/generate", {"text": "你好", "max_new": 4})
        check("text in is encoded board-side (e41_tok) and answered with ids + text",
              code == 200 and g.get("ids") == [11, 22]
              and isinstance(g.get("text"), str)
              and g.get("call", {}).get("n_ids") == len(enc), str(g)[:250])

        code, r = call(conn, "POST", "/load", {"model": "ernie-stub", "evict": True,
                                               "params": {"padfix": False, "pos_offset": 2,
                                                          "max_new_cap": 40,
                                                          "stop_ids": [2],
                                                          "max_slots": 512}})
        info2 = r.get("info") or {}
        check("params override the env that e41_chatd reads at import",
              info2.get("padfix") is False and info2.get("pos_offset") == 2
              and info2.get("max_new_cap") == 40 and info2.get("stop_ids") == [2],
              str(info2)[:300])
        check("max_slots raises the policy gate (task #24's entry point)",
              info2.get("max_slots_policy") == 512, str(info2)[:200])
        code, g = call(conn, "POST", "/generate", {"ids": list(range(100)), "max_new": 5})
        c2 = g.get("call") or {}
        check("the engine was actually called with those settings",
              code == 200 and g.get("prompt") == 100 and c2.get("padfix") is False
              and c2.get("pos_offset") == 2 and c2.get("max_new_cap") == 40
              and c2.get("max_slots") == 512 and c2.get("stop") == [2],
              str(g)[:300])
    finally:
        if conn:
            conn.close()
        srv.terminate()
        try:
            srv.wait(timeout=15)
        except Exception:
            srv.kill()
    print("\n%d checks passed, %d failed" % (ok["c"], len(fails)))
    if fails:
        print("FAILED: " + ", ".join(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
