#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test fixture: a model worker with no NPU and no icraft.

Lets the whole lifecycle (spawn -> handshake -> ready -> generate events -> bye ->
process really gone) be exercised on a PC, before any of it is claimed against the
board.  Also exposes three failure shapes the supervisor has to survive:

  {"cmd":"boom"}      engine raises            -> worker answers {"err":...}, stays up
  {"cmd":"suicide"}    worker dies mid-session -> supervisor must free the slot
  params.fail_load     load() raises           -> handshake carries error=..., /load fails

Registered as id "mock" in npuserv/models.json.
"""
import os
import sys
import time

sys.path.insert(0, os.environ.get("NPUSERV_HOME", "."))
import adapter_lib                                      # noqa: E402


class MockEngine(object):
    name = "mock"

    def __init__(self, params):
        self.params = params
        self.apply_s = params.get("load_ms", 1500) / 1000.0
        self.step_ms = params.get("step_ms", 40) / 1000.0
        self.stop = set(int(t) for t in params.get("stop_ids", []))
        self.info = {"mock": True, "pid": os.getpid(), "step_ms": self.step_ms,
                     "reply_prefix": list(params.get("reply_prefix", range(1000, 1040)))}
        self.n_steps = 0
        self.calls = 0

    def generate(self, req, emit):
        self.calls += 1
        ids = [int(t) for t in req.get("ids") or []]
        if not ids and req.get("text"):
            ids = [3 + (hash(ch) % 1000) for ch in str(req["text"])[:40]]
        if not ids:
            raise ValueError("mock needs ids or text")
        max_new = int(req.get("max_new", self.params.get("max_new", 8)))
        extra = self.info["reply_prefix"][:max_new]
        out = []
        for i, t in enumerate(ids):
            emit({"pre": i + 1, "n": len(ids)})
            time.sleep(self.step_ms)
        reason = "limit"
        for t in extra:
            if t in self.stop:
                reason = "eos"
                break
            out.append(t)
            emit({"tok": t, "i": len(out) - 1, "sec": round(self.step_ms, 4)})
            time.sleep(self.step_ms)
            self.n_steps += 1
        text = "mock-reply:%d" % self.calls
        return {"ids": out, "text": text, "reason": reason, "prompt": len(ids),
                "gen": len(out), "sec": round(len(ids) * self.step_ms +
                                              len(out) * self.step_ms, 3),
                "calls": self.calls}

    def close(self):
        pass


def load(params):
    if params.get("fail_load"):
        raise RuntimeError("mock fail_load requested: %r" % (params["fail_load"],))
    time.sleep(params.get("load_ms", 1500) / 1000.0)      # stands in for apply()
    eng = MockEngine(params)

    orig = eng.generate

    def generate(req, emit):
        if req.get("cmd") == "boom":
            raise RuntimeError("mock boom")
        if req.get("cmd") == "suicide":
            emit({"bye_to_self": True})
            os._exit(9)
        return orig(req, emit)

    eng.generate = generate
    return eng


if __name__ == "__main__":
    sys.exit(adapter_lib.run(load))
