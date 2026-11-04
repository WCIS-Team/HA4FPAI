#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Acceptance harness for npuserv, run against the "mock" entry -- no NPU, no icraft.

Answers one question: does the supervisor's shape actually hold?  The board is not
available for this, and the five behaviours that matter most (one slot, evict, a worker
that dies mid-session, a model bug that must not eat the session, unload that really
reaps the child) are all reproducible without an NPU.

Run from anywhere:
    python -X utf8 lang/npuserv/tests/lifecycle_check.py
Exits 0 only if every check passed; prints one line per check.
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
SERVER = os.path.join(NPUSERV, "server.py")

fails = []
n_ok = {"c": 0}


def check(name, cond, detail=""):
    if cond:
        n_ok["c"] += 1
        print("PASS  %s" % name)
    else:
        fails.append(name)
        print("FAIL  %s  %s" % (name, detail))


def call(conn, method, path, body=None):
    data = None if body is None else (json.dumps(body) + "\n").encode("utf-8")
    hdr = {"Content-Type": "application/json"} if data else {}
    conn.request(method, path, data, hdr)
    r = conn.getresponse()
    raw = r.read().decode("utf-8")
    try:
        return r.status, json.loads(raw)
    except Exception:
        return r.status, {"_raw": raw[:400]}


def stream_call(conn, path, body):
    """One chunked response, parsed back into the event list."""
    conn.request("POST", path, (json.dumps(body) + "\n").encode("utf-8"),
                 {"Content-Type": "application/json"})
    r = conn.getresponse()
    events, buf = [], b""
    while True:
        chunk = r.read(4096)
        if not chunk:
            break
        buf += chunk
    for line in buf.decode("utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    return r.status, events


def wait_gone(proc, timeout=25.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return proc.returncode
        time.sleep(0.2)
    return None


def main():
    free = socket.socket()
    free.bind(("127.0.0.1", 0))
    port = free.getsockname()[1]
    free.close()
    state = tempfile.mkdtemp(prefix="npuserv-test-")
    log_file = os.path.join(state, "server.log")
    srv = subprocess.Popen(
        [sys.executable, "-u", SERVER, "--port", str(port), "--state-dir", state],
        stdin=subprocess.DEVNULL, stdout=open(log_file, "ab"),
        stderr=subprocess.STDOUT, cwd=NPUSERV)
    conn = None
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
                code, body = call(conn, "GET", "/health")
                if code == 200:
                    break
            except Exception:
                time.sleep(0.3)
        else:
            print("server never answered /health; log follows")
            print(open(log_file, encoding="utf-8", errors="replace").read()[-2000:])
            return 2

        check("GET /health", code == 200 and body.get("ok") and body["state"]["state"] == "idle",
              str(body)[:200])
        code, models = call(conn, "GET", "/models")
        ids = [m["id"] for m in models.get("models", [])]
        mock = [m for m in models["models"] if m["id"] == "mock"][0]
        check("GET /models lists mock and the two real ids",
              "mock" in ids and "ernie-4.5-0.3b" in ids and "qwen3-0.6b" in ids, str(ids))
        check("adapter_exists is a real on-disk check",
              mock.get("adapter_exists") is True, str(mock)[:200])

        code, st = call(conn, "GET", "/status")
        check("GET /status while idle reports no pid", code == 200 and st["pid"] is None, str(st)[:160])

        # ---------- 1. happy path ----------
        t0 = time.time()
        code, r = call(conn, "POST", "/load", {"model": "mock",
                                               "params": {"load_ms": 1200, "step_ms": 10}})
        check("POST /load(mock) reaches ready",
              code == 200 and r.get("loaded") and r.get("port") and r["pid"], str(r)[:250])
        check("load reports apply_s ~= the simulated apply",
              r.get("apply_s") and 0.9 <= r["apply_s"] <= 3.0, str(r.get("apply_s")))
        check("the wait did not return before the fake apply finished",
              time.time() - t0 >= 1.2, "%.2f s" % (time.time() - t0))
        pid1 = r["pid"]
        code, st = call(conn, "GET", "/status")
        check("GET /status shows ready + pid + info",
              st["state"] == "ready" and st["pid"] == pid1 and st["info"].get("mock"), str(st)[:250])
        code, p = call(conn, "GET", "/ping")
        check("GET /ping round-trips to the worker",
              p.get("alive") and p["reply"].get("steps") == 0, str(p)[:200])

        code, g = call(conn, "POST", "/generate", {"ids": [7, 8, 9], "max_new": 4})
        check("POST /generate returns ids + reason + text",
              code == 200 and g.get("ids") and g.get("reason") and g.get("prompt") == 3,
              str(g)[:250])
        code, st = call(conn, "GET", "/status")
        lr = json.dumps(st.get("last_result") or {})
        check("/status carries the last result and the id count, not the ids",
              "last_ids_len" in st and "ids" not in (st.get("last_result") or {}), lr[:200])
        code, p = call(conn, "GET", "/ping")
        check("worker kept its step counter across requests",
              p["reply"].get("steps") == 4, str(p)[:200])

        code, events = stream_call(conn, "/generate",
                                   {"ids": [1, 2, 3], "max_new": 3, "stream": True})
        kinds = [list(e)[0] for e in events]
        check("stream=true answers ndjson: pre*3 + tok*3 + end",
              code == 200 and kinds.count("pre") == 3 and kinds.count("tok") == 3
              and events[-1].get("end"), str(kinds))

        # ---------- 2. one slot ----------
        code, r = call(conn, "POST", "/load", {"model": "mock"})
        check("/load while loaded is refused with 409",
              code == 409 and "evict" in r.get("error", ""), str(r)[:200])

        code, r = call(conn, "POST", "/load", {"model": "mock", "evict": True,
                                               "params": {"load_ms": 300, "step_ms": 10}})
        check("/load evict=true replaces the incumbent",
              code == 200 and r.get("loaded") and r["pid"] != pid1, str(r)[:200])
        pid2 = r["pid"]
        gone = wait_for_pid_gone(pid1)
        check("evicted worker process is really gone (CMA only returns then)",
              gone, "pid %s still alive" % pid1)
        code, r = call(conn, "POST", "/load", {"model": "mock", "wait": False,
                                               "evict": True, "params": {"load_ms": 2000}})
        check("wait=false returns state=loading immediately",
              code == 200 and r.get("state") == "loading" and not r.get("loaded"), str(r)[:200])
        deadline = time.time() + 20
        ready = False
        while time.time() < deadline:
            code, st = call(conn, "GET", "/status")
            if st["state"] == "ready":
                ready = True
                break
            time.sleep(0.2)
        check("...and /status reaches ready on its own", ready, str(st)[:200])
        pid3 = st["pid"]

        # ---------- 3. failure shapes ----------
        code, r = call(conn, "POST", "/generate", {"cmd": "boom"})
        check("a model bug answers 500 and does not eat the session",
              code == 500 and "mock boom" in r.get("error", ""), str(r)[:200])
        code, p = call(conn, "GET", "/ping")
        check("session survives the bug (ping alive)", p.get("alive") is True, str(p)[:200])
        code, st = call(conn, "GET", "/status")
        check("the bug is recorded as last_error", "mock boom" in str(st.get("last_error")), str(st)[:250])

        code, r = call(conn, "POST", "/generate", {"cmd": "suicide"})
        check("worker suicide surfaces as an error", code == 500, str(r)[:200])
        code, st = call(conn, "GET", "/status")
        check("suicide frees the slot instead of lying about it",
              st["state"] == "idle" and st["pid"] is None, str(st)[:250])
        check("history records the exit reason and rc",
              any(h["why"].startswith("worker") or h["rc"] not in (0, None)
                  for h in st.get("history", [])), str(st.get("history"))[:250])

        code, r = call(conn, "POST", "/generate", {"ids": [1]})
        check("/generate with nothing loaded is 409", code == 409, str(r)[:200])

        code, r = call(conn, "POST", "/load", {"model": "mock",
                                               "params": {"fail_load": "boom-at-apply"}})
        check("a load that raises answers 500 with the worker's error",
              code == 500 and "fail_load" in r.get("error", ""), str(r)[:250])
        code, st = call(conn, "GET", "/status")
        check("failed load leaves the slot idle", st["state"] == "idle", str(st)[:200])

        code, r = call(conn, "POST", "/load", {"model": "does-not-exist"})
        check("unknown model id is 404", code == 404, str(r)[:200])

        # ---------- 4. unload ----------
        code, r = call(conn, "POST", "/load", {"model": "mock", "params": {"load_ms": 300}})
        pid4 = r["pid"]
        check("reload after the failures works", code == 200 and r.get("loaded"), str(r)[:200])
        code, r = call(conn, "POST", "/unload", {"model": "ernie-4.5-0.3b"})
        check("/unload naming a different model is 409", code == 409, str(r)[:200])
        code, r = call(conn, "POST", "/unload", {"model": "mock"})
        check("POST /unload reports before/after memory",
              code == 200 and r.get("unloaded") and "mem_before" in r and "mem_after" in r,
              str(r)[:250])
        check("unload reports the log path for forensics", r.get("log") and
              os.path.exists(r["log"]), str(r.get("log")))
        gone = wait_for_pid_gone(pid4)
        check("unloaded worker process is really gone", gone, "pid %s still alive" % pid4)
        code, r = call(conn, "POST", "/unload")
        check("second /unload is a no-op, not an error",
              code == 200 and r.get("unloaded") is False and r["state"] == "idle", str(r)[:200])
        code, p = call(conn, "GET", "/ping")
        check("/ping with nothing loaded answers alive=false, no exception",
              code == 200 and p.get("alive") is False, str(p)[:200])

        # ---------- 5. logs are files ----------
        code, r = call(conn, "POST", "/load", {"model": "mock", "params": {"load_ms": 200}})
        check("worker stdout lands in a file, not a pipe", r.get("log", "").endswith(".log")
              and os.path.exists(r["log"]), str(r)[:200])
        with open(r["log"], encoding="utf-8", errors="replace") as fh:
            txt = fh.read()
        check("that file already contains the worker's own lines",
              "worker exit" in txt or "supervisor attached" in txt or "loaded in" in txt,
              txt[-200:])
        call(conn, "POST", "/unload", {})
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
        srv.terminate()
        wait_gone(srv, 15)
    leftovers = [p for p in (pid1, pid2, pid3, pid4) if pid_alive(p)]
    check("no live mock workers remain after supervisor shutdown", not leftovers, str(leftovers))
    print("\n%d checks passed, %d failed" % (n_ok["c"], len(fails)))
    if fails:
        print("FAILED: " + ", ".join(fails))
        print("--- server log ---")
        print(open(log_file, encoding="utf-8", errors="replace").read()[-3000:])
    return 1 if fails else 0


def wait_for_pid_gone(pid, timeout=25.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.2)
    return not pid_alive(pid)


def pid_alive(pid):
    if not pid:
        return False
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/NH", "/FI", "PID eq %d" % pid],
                             capture_output=True, text=True)
        return str(pid) in out.stdout
    try:
        with open("/proc/%d/stat" % pid, encoding="utf-8") as stream:
            state = stream.read().rpartition(")")[2].split()[0]
        return state not in ("Z", "X")
    except (OSError, IndexError):
        return False


if __name__ == "__main__":
    sys.exit(main())
