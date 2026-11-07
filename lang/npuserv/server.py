#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""npuserv -- one API on the board, any compiled 件 on the NPU, one at a time.

Shape, and why it is this shape:

  * The server is a *supervisor*.  A loaded model lives in a child process (an adapter
    under the model's own directory, run through adapter_lib).  The ZG330 session is
    bound to the process that ran apply(), and the board's single 256 MiB CMA pool only
    comes back in full when that process exits -- so "放下来" has to mean "kill the
    child".  A single process holding the session can neither give the memory back nor
    load a second 件.
  * One slot.  Two apply() runs in the same window is exactly what 台账 §册三 could not
    attribute, so /load while something is loaded is a 409 unless evict=true.
  * Per-model special steps stay in the model's own directory (ERNIE's padfix / narrow
    readback / 511-slot clamp, Qwen3's 3-fp32 token_dynamic_fm, whole-tensor readback
    and board-side tokenizer).  Nothing model-specific is in this file.
  * Handshake and logs are files, never pipes (台账 §十五: an ssh drop must not take the
    run's output with it).  The control channel is loopback TCP, not AF_UNIX, so the
    same code paths get exercised on the PC before anything reaches the board.

Run on the board:
  python3 server.py --host 127.0.0.1 --port 18080                      # foreground
  nohup python3 -u server.py --port 18080 >/tmp/npuserv.out 2>&1 &     # detached

API (JSON in, JSON out; curl lines and the adapter contract in README.md):
  GET  /health   GET /models   GET /status   GET /ping
  POST /load     POST /unload   POST /generate
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import wire                                            # noqa: E402

PROTOCOL = 1
TERMINATE_GRACE_S = 8


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def log(msg):
    sys.stderr.write("[%s] %s\n" % (now(), msg))
    sys.stderr.flush()


def read_proc_mem():
    """/proc/meminfo readings that matter on this board; {} on Windows."""
    want = ("CmaFree", "MemAvailable", "MemFree")
    out = {}
    try:
        with open("/proc/meminfo") as fh:
            for ln in fh:
                k = ln.split(":")[0].strip()
                if k in want:
                    out[k + "_kB"] = int(ln.split()[1])
    except Exception:
        pass
    return out


def read_worker_rss(pid):
    try:
        with open("/proc/%d/status" % pid) as fh:
            for ln in fh:
                if ln.startswith("VmRSS:"):
                    return int(ln.split()[1])
    except Exception:
        pass
    return None


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


class ModelError(Exception):
    pass


class Model(object):
    """One registry entry, resolved to paths on the machine running the server."""

    def __init__(self, entry, root):
        self.id = entry["id"]
        self.raw = entry
        self.adapter = os.path.normpath(os.path.join(root, entry["adapter"]))
        self.defaults = entry.get("defaults", {})
        self.env = entry.get("env", {})
        self.capabilities = entry.get("capabilities", {})
        self.note = entry.get("note", "")
        # Which iCraft build produced this 件.  The board runtime has to match it; nothing
        # here verifies the match, but a caller choosing between models can read it.
        self.compiled_with = entry.get("compiled_with")

    def describe(self):
        d = {"id": self.id, "adapter": self.adapter,
             "adapter_exists": os.path.exists(self.adapter),
             "compiled_with": self.compiled_with,
             "capabilities": self.capabilities, "defaults": self.defaults,
             "note": self.note}
        if self.raw.get("artifacts"):
            d["artifacts"] = self.raw["artifacts"]
            d["artifacts_present"] = dict(
                (k, os.path.exists(v)) for k, v in self.raw["artifacts"].items())
        return d


class Supervisor(object):
    """Single-slot holder of the NPU.  self.lock guards the slot, self.req_lock the session."""

    def __init__(self, registry_path, state_dir, python=None):
        here = os.path.dirname(os.path.abspath(registry_path))
        data = json.load(open(registry_path, encoding="utf-8"))
        if data.get("proto") != PROTOCOL:
            raise SystemExit("registry proto %r != %d" % (data.get("proto"), PROTOCOL))
        # A registry names the directory its "adapter" paths hang off.  The server lives in
        # lang/npuserv and the models do not, so this cannot be "wherever the registry
        # sits"; ".." (the parent directory) stays the default.
        root = os.path.normpath(os.path.join(here, data.get("root", os.pardir)))
        self.models = {}
        for entry in data["models"]:
            m = Model(entry, root)
            self.models[m.id] = m
        self.state_dir = state_dir
        self.python = python or sys.executable
        os.makedirs(state_dir, exist_ok=True)
        self.lock = threading.Lock()
        self.req_lock = threading.Lock()
        self.job = None
        self.started = time.time()
        self.history = []

    # ---------- readings ----------
    def status(self):
        j = self.job
        out = {"state": (j or {}).get("state", "idle"),
               "model": (j or {}).get("model"), "job": (j or {}).get("id"),
               "pid": (j or {}).get("pid"), "port": (j or {}).get("port"),
               "loading_s": (j or {}).get("loading_s"), "apply_s": (j or {}).get("apply_s"),
               "info": (j or {}).get("info"), "requests": (j or {}).get("requests", 0),
               "generating": self.req_lock.locked(),
               "last_error": (j or {}).get("last_error"),
               "log": (j or {}).get("log"), "params": (j or {}).get("params"),
               "server_uptime_s": round(time.time() - self.started, 1),
               "models": sorted(self.models)}
        out.update(read_proc_mem())
        if (j or {}).get("pid"):
            out["worker_rss_kB"] = read_worker_rss(j["pid"])
        if (j or {}).get("last_result"):
            out["last_result"] = j["last_result"]
            out["last_ids_len"] = j.get("last_ids_len")
        out["history"] = list(self.history)
        return out

    # ---------- slot ----------
    def _reap(self, j, why):
        """Make sure the child is really gone before the slot is handed out again."""
        p = j.get("proc")
        rc = j.get("rc")
        if p is not None:
            try:
                if p.poll() is None:
                    self._ask_bye(j)
                    p.terminate()
                    try:
                        rc = p.wait(timeout=TERMINATE_GRACE_S)
                    except subprocess.TimeoutExpired:
                        p.kill()
                        rc = p.wait(timeout=TERMINATE_GRACE_S)
                else:
                    rc = p.poll()
            except Exception as e:
                j["last_error"] = "reap failed: %r" % (e,)
        sock = j.get("sock")
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
            j["sock"] = None
            j["rd"] = None
        j["stop_reason"] = why
        j["rc"] = rc
        j["stopped_at"] = now()
        self.history.append({"job": j["id"], "model": j["model"], "why": why, "rc": rc,
                             "at": j["stopped_at"]})
        del self.history[:-20]
        return rc

    def _ask_bye(self, j):
        """Politely tell the worker to exit; it may be dead or mid-apply, either is fine."""
        sock = j.get("sock")
        if sock is None:
            return
        try:
            wire.send_obj(sock, {"cmd": "bye"})
            j["rd"].read(timeout=1.0)
        except Exception:
            pass

    def unload(self, model=None):
        with self.lock:
            j = self.job
            if j is None or j["state"] == "idle":
                return {"unloaded": False, "state": "idle", "mem": read_proc_mem()}
            if model and model != j["model"]:
                raise Conflict("loaded model is %s, not %s" % (j["model"], model))
            before = read_proc_mem()
            rc = self._reap(j, "unload")
            self.job = None
        return {"unloaded": True, "model": j["model"], "job": j["id"], "pid": j["pid"],
                "rc": rc, "log": j["log"], "mem_before": before,
                "mem_after": read_proc_mem(),
                "note": "CMA is only fully back a moment after the process dies; "
                        "re-read /status or /proc/meminfo"}

    # ---------- load ----------
    def load(self, model_id, params=None, wait=True, timeout=420.0, evict=False):
        with self.lock:
            j = self.job
            if j is not None and j["state"] in ("loading", "ready"):
                if not evict:
                    raise Conflict("model %s is %s; pass evict=true to replace it"
                                   % (j["model"], j["state"]))
                self._reap(j, "evict")
                self.job = None
            m = self.models.get(model_id)
            if m is None:
                raise NotFound("no model %r in registry; GET /models lists the ids" % model_id)
            if not os.path.exists(m.adapter):
                raise NotFound("adapter missing: %s" % m.adapter)

            job_id = uuid.uuid4().hex[:8]
            base = os.path.join(self.state_dir, "%s_%s" % (job_id, model_id))
            handshake = base + ".handshake"
            log_file = base + ".log"
            merged = dict(m.defaults)
            merged.update(params or {})
            merged["model"] = model_id
            with open(base + ".params.json", "w", encoding="utf-8") as fh:
                json.dump(merged, fh, ensure_ascii=False)

            env = dict(os.environ)
            env.update(m.env)
            env.update({"NPUSERV_HOME": HERE, "NPUSERV_PARAMS": base + ".params.json",
                        "NPUSERV_HANDSHAKE": handshake, "NPUSERV_IDLE": "0",
                        "PYTHONUNBUFFERED": "1"})
            popen_kw = {"stdin": subprocess.DEVNULL, "env": env,
                        "stdout": open(log_file, "ab"), "stderr": subprocess.STDOUT}
            if os.name != "nt":
                popen_kw["start_new_session"] = True
            proc = subprocess.Popen([self.python, "-u", m.adapter], **popen_kw)
            j = {"id": job_id, "model": model_id, "state": "loading", "pid": proc.pid,
                 "proc": proc, "log": log_file, "params": merged, "requests": 0,
                 "capabilities": m.capabilities, "started_at": now(), "last_error": None}
            self.job = j
        log("spawn %s pid=%d job=%s log=%s" % (model_id, proc.pid, job_id, log_file))

        th = threading.Thread(target=self._finish_load,
                              args=(j, handshake, time.time(), timeout))
        th.daemon = True
        j["thread"] = th
        th.start()
        if not wait:
            return {"loaded": False, "state": "loading", "model": model_id,
                    "job": job_id, "pid": proc.pid, "log": log_file,
                    "note": "poll /status; ready after apply (50-132 s on this board)"}
        th.join(timeout + 60)
        if j["state"] != "ready":
            raise ModelError(j.get("last_error") or "load did not reach ready (log %s)"
                             % log_file)
        return {"loaded": True, "model": model_id, "job": j["id"], "pid": j["pid"],
                "port": j["port"], "apply_s": j["apply_s"], "loading_s": j["loading_s"],
                "info": j["info"], "log": log_file, "mem": read_proc_mem()}

    def _finish_load(self, j, handshake, t0, timeout):
        """Poll the handshake, open the control socket, read the worker's `ready`.

        The handshake is written twice: once before apply() (so a slow load is
        distinguishable from a dead worker) and once after, with loaded=true or error.
        """
        deadline = t0 + timeout
        hs = None
        while time.time() < deadline:
            hs = _read_json(handshake) or hs          # keep the last good read
            if hs and (hs.get("loaded") or hs.get("error")):
                break
            if j["proc"].poll() is not None:
                # A worker that dies during apply() writes its reason first and exits
                # within the same millisecond; reading the file one last time is the
                # difference between "apply raised <reason>" and "worker died, unknown why".
                hs = _read_json(handshake) or hs
                break
            time.sleep(0.5)
        with self.lock:
            if self.job is not j:
                return
            if hs and hs.get("error"):
                j["last_error"] = "worker failed to load: %s" % hs["error"]
                self._reap(j, "load-error")
                self.job = None
                log("load failed %s: %s" % (j["model"], j["last_error"]))
                return
            if not hs or not hs.get("loaded"):
                died = j["proc"].poll() is not None
                rc = self._reap(j, "load-died" if died else "load-timeout")
                j["last_error"] = ("worker exited rc=%s before handshake; log %s"
                                   % (rc, j["log"]) if died else
                                   "no loaded handshake within %ss; log %s" % (timeout, j["log"]))
                self.job = None
                log("load failed %s: %s" % (j["model"], j["last_error"]))
                return
            try:
                sock = wire.connect(hs["port"], timeout=15.0)
                rd = wire.LineReader(sock)
                ready = rd.read(timeout=60.0)
            except Exception as e:
                j["last_error"] = "cannot reach worker port %s: %r" % (hs.get("port"), e)
                self._reap(j, "connect-fail")
                self.job = None
                log("load failed %s: %s" % (j["model"], j["last_error"]))
                return
            j.update({"state": "ready", "port": hs["port"], "sock": sock, "rd": rd,
                      "apply_s": (ready or {}).get("apply_s"),
                      "info": (ready or {}).get("info"),
                      "loading_s": round(time.time() - t0, 1)})
        log("ready %s job=%s apply_s=%s loading_s=%.1f"
            % (j["model"], j["id"], j["apply_s"], j["loading_s"]))

    # ---------- generate ----------
    def generate(self, body, wait_s=10.0, timeout=900.0, stream=None):
        with self.lock:
            j = self.job
            if j is None or j["state"] != "ready":
                raise Conflict("nothing loaded (state=%s); POST /load first"
                               % (j or {}).get("state", "idle"))
        if not self.req_lock.acquire(timeout=wait_s):
            raise Conflict("the one NPU session is busy; retry or raise wait_s")
        try:
            req = dict(body or {})
            req["cmd"] = req.get("cmd", "generate")
            if "max_new" not in req and "max_new_tokens" in req:
                req["max_new"] = req["max_new_tokens"]
            events = []
            try:
                wire.send_obj(j["sock"], req)
                while True:
                    ev = j["rd"].read(timeout=timeout)
                    if ev is None:
                        raise ModelError("worker closed the connection mid-request")
                    if stream:
                        stream(ev)
                    events.append(ev)
                    if ev.get("end") or ev.get("err"):
                        break
            except ModelError:
                self._drop(j, "worker-lost")
                raise
            except Exception as e:
                self._drop(j, "generate-error")
                raise ModelError("%r; log %s" % (e, j["log"]))
            last = events[-1] if events else {}
            if last.get("err"):
                j["last_error"] = last["err"]
                raise ModelError(last["err"])
            j["requests"] = j.get("requests", 0) + 1
            j["last_result"] = dict((k, v) for k, v in last.items() if k != "ids")
            j["last_ids_len"] = len(last.get("ids") or [])
            last["events"] = len(events)
            last["job"] = j["id"]
            return last
        finally:
            self.req_lock.release()

    def _drop(self, j, why):
        """A dead socket means the session is gone: free the slot instead of lying about it."""
        with self.lock:
            if self.job is not j:
                return
            self._reap(j, why)
            self.job = None

    def ping(self):
        with self.lock:
            j = self.job
            if j is None or j["state"] != "ready":
                return {"alive": False, "state": (j or {}).get("state", "idle")}
            try:
                wire.send_obj(j["sock"], {"cmd": "ping"})
                ev = j["rd"].read(timeout=15.0)
            except Exception as e:
                self._reap(j, "ping-fail")
                self.job = None
                return {"alive": False, "error": repr(e)}
            return {"alive": bool(ev and ev.get("pong")), "reply": ev,
                    "pid": j["pid"], "model": j["model"]}


def make_handler(sv):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "npuserv/1"

        def log_message(self, fmt, *args):
            log("http " + (fmt % args))

        def _send(self, code, obj):
            body = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _chunk(self, data):
            self.wfile.write(("%X\r\n" % len(data)).encode("ascii"))
            self.wfile.write(data + b"\r\n")
            self.wfile.flush()

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            obj = json.loads(self.rfile.read(n).decode("utf-8"))
            if not isinstance(obj, dict):
                raise ValueError("body must be a JSON object")
            return obj

        def do_GET(self):
            path = self.path.split("?")[0]
            try:
                if path in ("/", "/health"):
                    return self._send(200, {"ok": True, "proto": PROTOCOL,
                                            "state": sv.status()})
                if path == "/models":
                    return self._send(200, {"proto": PROTOCOL,
                                            "models": [(m.describe() if hasattr(m, "describe") else
                                                sv.describe_model(k, m)) for k, m in sorted(
                                                sv.models.items())]})
                if path == "/status":
                    return self._send(200, sv.status())
                if path == "/ping":
                    return self._send(200, sv.ping())
                self._send(404, {"error": "no route %s" % path})
            except Exception as e:
                self._send(500, {"error": repr(e)})

        def do_POST(self):
            path = self.path.split("?")[0]
            try:
                body = self._body()
                if path == "/load":
                    return self._send(200, sv.load(
                        body.get("model"), body.get("params"),
                        wait=bool(body.get("wait", True)),
                        timeout=float(body.get("timeout", 420)),
                        evict=bool(body.get("evict"))))
                if path == "/unload":
                    return self._send(200, sv.unload(body.get("model")))
                if path == "/generate":
                    want = bool(body.pop("stream", False))
                    wait_s = float(body.pop("wait_s", 10.0))
                    timeout = float(body.pop("timeout", 900.0))
                    if not want:
                        return self._send(200, sv.generate(body, wait_s, timeout))
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson")
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    try:
                        sv.generate(body, wait_s, timeout, stream=lambda ev: self._chunk(
                            (json.dumps(ev, ensure_ascii=False) + "\n").encode("utf-8")))
                    except Exception as e:
                        self._chunk((json.dumps({"err": str(e)}, ensure_ascii=False) + "\n")
                                    .encode("utf-8"))
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
                    return
                self._send(404, {"error": "no route %s" % path})
            except NotFound as e:
                self._send(404, {"error": str(e)})
            except Conflict as e:
                self._send(409, {"error": str(e)})
            except ModelError as e:
                self._send(500, {"error": str(e)})
            except ValueError as e:
                self._send(400, {"error": "bad request: %r" % (e,)})
            except Exception as e:
                self._send(500, {"error": repr(e),
                                 "trace": traceback.format_exc()[-800:]})

    return Handler


class ResidentSupervisor(object):
    """One resident worker process; /load stacks model sessions inside it.

    Experimental (2026-11-08). The worker bootstraps the device once and stacks
    sessions per /load — measured OK for alternating forwards and clean
    reload cycles on 113 (see docs/COEXISTENCE.md §七). CMA: each LLM apply
    still eats the pool, so resident LLM + camera owner remain mutually
    exclusive — that part does NOT change with this architecture.
    """

    def __init__(self, registry_path, state_dir, python=None):
        here = os.path.dirname(os.path.abspath(registry_path))
        data = json.load(open(registry_path, encoding='utf-8'))
        if data.get('proto') != 1:
            raise SystemExit('registry proto mismatch')
        root = os.path.normpath(os.path.join(here, data.get('root', os.pardir)))
        self.models = {}
        for entry in data['models']:
            self.models[entry['id']] = {
                'adapter': os.path.normpath(os.path.join(root, entry['adapter'])),
                'defaults': entry.get('defaults', {}),
                'env': entry.get('env', {}),
                'capabilities': entry.get('capabilities', {}),
            }
        self.state_dir = state_dir
        self.python = python or sys.executable
        os.makedirs(state_dir, exist_ok=True)
        self.lock = threading.Lock()
        self.req_lock = threading.Lock()
        self.job = None
        self.started = time.time()
        self.history = []
        self.resident_adapter = os.path.join(HERE, 'resident_worker.py')

    def describe_model(self, model_id, m):
        # id 来自 registry 的 key（self.models[entry['id']]），value 里没有 'id' 字段
        return {'id': model_id, 'adapter': m['adapter'],
                'adapter_exists': os.path.exists(m['adapter']),
                'capabilities': m['capabilities'], 'defaults': m['defaults']}

    def status(self):
        j = self.job or {}
        out = {'state': j.get('state', 'idle'), 'resident': True,
               'engines': j.get('engines', []), 'job': j.get('id'),
               'pid': j.get('pid'), 'port': j.get('port'),
               'loading_s': j.get('loading_s'), 'requests': j.get('requests', 0),
               'generating': self.req_lock.locked(),
               'last_error': j.get('last_error'),
               'server_uptime_s': round(time.time() - self.started, 1),
               'models': sorted(self.models)}
        out.update(read_proc_mem())
        if j.get('pid'):
            out['worker_rss_kB'] = read_worker_rss(j['pid'])
        out['history'] = list(self.history)
        return out

    def _wire_roundtrip(self, req, timeout, j):
        wire.send_obj(j['sock'], req)
        last = None
        while True:
            ev = j['rd'].read(timeout=timeout)
            if ev is None:
                raise ModelError('resident worker closed the connection')
            if ev.get('err'):
                raise ModelError(ev['err'])
            if ev.get('end') or ev.get('acked') or ev.get('pong'):
                last = ev
                break
        return last

    def _ensure_worker(self, timeout):
        j = self.job
        if j and j.get('state') in ('ready',):
            return j
        if j and j['proc'].poll() is not None:
            self._reap(j, 'worker-died')
            self.job = None
        job_id = uuid.uuid4().hex[:8]
        base = os.path.join(self.state_dir, 'resident_%s' % job_id)
        handshake = base + '.handshake'
        log_file = base + '.log'
        env = dict(os.environ)
        env.update({'NPUSERV_HOME': HERE, 'NPUSERV_HANDSHAKE': handshake,
                    'NPUSERV_IDLE': '0', 'PYTHONUNBUFFERED': '1',
                    'NPUSERV_BOOTSTRAP': os.path.join(HERE, 'bootstrap_zg330.py')})
        popen_kw = {'stdin': subprocess.DEVNULL, 'env': env,
                    'stdout': open(log_file, 'ab'), 'stderr': subprocess.STDOUT}
        if os.name != 'nt':
            popen_kw['start_new_session'] = True
        proc = subprocess.Popen([self.python, '-u', self.resident_adapter], **popen_kw)
        j = {'id': job_id, 'model': '<resident>', 'state': 'loading', 'pid': proc.pid,
             'proc': proc, 'log': log_file, 'params': {}, 'requests': 0,
             'capabilities': {}, 'started_at': now(), 'last_error': None,
             'engines': []}
        self.job = j
        log('spawn resident worker pid=%d job=%s' % (proc.pid, job_id))
        deadline = time.time() + timeout
        hs = None
        while time.time() < deadline:
            hs = _read_json(handshake) or hs
            if hs and (hs.get('loaded') or hs.get('error')):
                break
            if proc.poll() is not None:
                hs = _read_json(handshake) or hs
                break
            time.sleep(0.5)
        if not hs or hs.get('error') or not hs.get('loaded'):
            self._reap(j, 'resident-bootstrap-failed')
            self.job = None
            raise ModelError('resident bootstrap failed: %s' % ((hs or {}).get('error') or 'timeout'))
        sock = wire.connect(hs['port'], timeout=15.0)
        rd = wire.LineReader(sock)
        j.update({'state': 'ready', 'port': hs['port'], 'sock': sock, 'rd': rd,
                  'loading_s': round(time.time() - time.time(), 1)})
        log('resident ready pid=%d' % proc.pid)
        return j

    def load(self, model_id, params=None, wait=True, timeout=420.0, evict=False):
        m = self.models.get(model_id)
        if m is None:
            raise NotFound('no model %r in registry' % model_id)
        if not os.path.exists(m['adapter']):
            raise NotFound('adapter missing: %s' % m['adapter'])
        merged = dict(m['defaults'])
        merged.update(params or {})
        merged['model'] = model_id
        merged['adapter_module'] = m['adapter']
        with self.req_lock:
            j = self._ensure_worker(timeout)
            last = self._wire_roundtrip({'cmd': 'load', 'model': model_id,
                                         'module': m['adapter'], 'params': merged},
                                        timeout, j)
            if model_id not in j.setdefault('engines', []):
                j['engines'].append(model_id)
            j['requests'] = j.get('requests', 0) + 1
        return {'loaded': True, 'model': model_id, 'resident': True,
                'apply_s': last.get('apply_s'), 'info': last.get('info'),
                'residents': j['engines'], 'mem': read_proc_mem()}

    def generate(self, body, wait_s=10.0, timeout=900.0, stream=None):
        with self.lock:
            j = self.job
            if not j or j.get('state') != 'ready':
                raise Conflict('resident worker not ready')
            mid = (body or {}).get('model')
            if not mid:
                raise Conflict('resident mode requires "model" in the body')
        if not self.req_lock.acquire(timeout=wait_s):
            raise Conflict('the NPU is busy; retry')
        try:
            req = dict(body or {})
            req['cmd'] = 'generate'
            events = []
            try:
                wire.send_obj(j['sock'], req)
                while True:
                    ev = j['rd'].read(timeout=timeout)
                    if ev is None:
                        raise ModelError('resident worker closed mid-request')
                    if stream:
                        stream(ev)
                    events.append(ev)
                    if ev.get('end') or ev.get('err'):
                        break
            except ModelError:
                self._drop(j, 'worker-lost')
                raise
            except Exception as e:
                self._drop(j, 'generate-error')
                raise ModelError('%r; log %s' % (e, j['log']))
            last = events[-1] if events else {}
            if last.get('err'):
                j['last_error'] = last['err']
                raise ModelError(last['err'])
            j['requests'] = j.get('requests', 0) + 1
            last['events'] = len(events)
            last['job'] = j['id']
            return last
        finally:
            self.req_lock.release()

    def _reap(self, j, why):
        p = j.get('proc')
        if p is not None:
            try:
                if p.poll() is None:
                    try:
                        wire.send_obj(j['sock'], {'cmd': 'bye'})
                    except Exception:
                        pass
                    p.terminate()
                    try:
                        p.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        p.kill()
                else:
                    p.poll()
            except Exception as e:
                j['last_error'] = repr(e)
        sock = j.get('sock')
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
        j['stop_reason'] = why
        j['stopped_at'] = now()
        self.history.append({'job': j['id'], 'why': why, 'at': j['stopped_at']})
        del self.history[:-20]

    def _drop(self, j, why):
        with self.lock:
            self._reap(j, why)
            self.job = None

    def unload(self, model=None):
        with self.lock:
            j = self.job
            if not j or j.get('state') != 'ready':
                return {'unloaded': False, 'state': 'idle'}
        with self.req_lock:
            try:
                last = self._wire_roundtrip({'cmd': 'unload', 'model': model}, 30, j)
            except ModelError as e:
                if model:
                    raise
                self._drop(j, 'unload-failed')
                raise
            if model and model in j.get('engines', []):
                j['engines'].remove(model)
            return {'unloaded': True, 'model': model, 'result': last,
                    'residents': j.get('engines', [])}

    def ping(self, model=None):
        with self.lock:
            j = self.job
            if not j or j.get('state') != 'ready':
                return {'alive': False, 'state': 'idle'}
        try:
            last = self._wire_roundtrip({'cmd': 'ping'}, 15, j)
            return {'alive': True, 'reply': last, 'engines': j.get('engines', [])}
        except Exception as e:
            self._drop(j, 'ping-fail')
            return {'alive': False, 'error': repr(e)}

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=18080)
    ap.add_argument("--registry", default=os.path.join(HERE, "models.json"))
    ap.add_argument("--state-dir", default=os.path.join(HERE, "state"))
    ap.add_argument("--python", default=None, help="interpreter for workers (default: mine)")
    ap.add_argument("--resident", action="store_true",
                    help="one resident worker holds one device; /load stacks model sessions (experimental)")
    a = ap.parse_args()
    if getattr(a, 'resident', False):
        sv = ResidentSupervisor(a.registry, a.state_dir, a.python)
    else:
        sv = Supervisor(a.registry, a.state_dir, a.python)

    httpd = ThreadingHTTPServer((a.host, a.port), make_handler(sv))
    httpd.daemon_threads = True
    log("npuserv on http://%s:%d  models=%s  state=%s"
        % (a.host, a.port, ",".join(sorted(sv.models)), a.state_dir))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            sv.unload()
        except Exception as e:
            log("unload on shutdown failed: %r" % (e,))
        httpd.server_close()


if __name__ == "__main__":
    main()


