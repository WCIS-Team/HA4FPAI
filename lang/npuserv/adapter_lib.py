# -*- coding: utf-8 -*-
"""Worker side of npuserv: the framework each model adapter runs inside.

Why a worker *process* per loaded model instead of a class inside the server: the NPU
session is bound to the process that called apply(), and the board's single 256 MiB CMA
pool only comes back in full when that process exits (台账 §册三 / [[板端内存上限与心跳日志]]).
So "把模型放下来" has to mean "this process dies" -- which the server must not do to
itself.  The server stays up holding the API; the model lives here.

Contract an adapter implements (see npuserv/README.md):

    import adapter_lib

    def load(params):
        # heavy imports and apply() go INSIDE load(), after the handshake is written
        return engine                 # object with .info and .generate(req, emit)

    if __name__ == "__main__":
        adapter_lib.run(load)

Environment (all set by the supervisor):
    NPUSERV_PARAMS      JSON file: registry defaults merged with this load's params
    NPUSERV_HANDSHAKE   file this worker writes {"proto":1,"port":N,...} into
    NPUSERV_IDLE        seconds to live with no client attached; 0/absent = forever
"""
import json
import os
import signal
import socket
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import wire                                            # noqa: E402  (sibling of this file)

PROTOCOL = 1
_stop = {"flag": False}


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def log(msg):
    sys.stderr.write("[%s][w%d] %s\n" % (_now(), os.getpid(), msg))
    sys.stderr.flush()


def _read_params(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _write_handshake(path, obj):
    """Atomic: the supervisor polls this file and must never see a half-written one."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    if os.path.exists(path):
        os.remove(path)
    os.rename(tmp, path)


def _bind():
    ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    ls.bind(("127.0.0.1", 0))
    ls.listen(4)
    return ls


def _handle_conn(conn, engine, idle):
    conn.settimeout(None)
    rd = wire.LineReader(conn)
    wire.send_obj(conn, {"ready": True, "model": getattr(engine, "name", "?"),
                         "apply_s": round(getattr(engine, "apply_s", 0.0), 1),
                         "info": getattr(engine, "info", {})})
    last = time.time()
    while not _stop["flag"]:
        try:
            req = rd.read(timeout=1.0)
        except socket.timeout:
            if idle and time.time() - last > idle:
                log("idle %d s, worker exiting" % idle)
                return "idle"
            continue
        except (ConnectionError, OSError) as e:
            log("client socket gone: %r" % (e,))
            return "eof"
        if req is None:
            return "eof"
        last = time.time()
        cmd = req.get("cmd", "generate")
        try:
            if cmd == "bye":
                wire.send_obj(conn, {"acked": True, "bye": True})
                return "bye"
            if cmd == "ping":
                wire.send_obj(conn, {"pong": True, "steps": getattr(engine, "n_steps", 0),
                                     "info": getattr(engine, "info", {})})
                continue
            if cmd == "stats":
                wire.send_obj(conn, {"stats": True,
                                     "steps": getattr(engine, "n_steps", 0),
                                     "apply_s": getattr(engine, "apply_s", None),
                                     "info": getattr(engine, "info", {})})
                continue
            out = engine.generate(req, lambda ev: wire.send_obj(conn, ev))
            ev = dict(out or {})
            ev["end"] = True
            wire.send_obj(conn, ev)
        except Exception as e:                      # a model bug must not kill the session
            log("command %r failed: %r" % (cmd, e))
            try:
                wire.send_obj(conn, {"err": repr(e)})
            except Exception:
                return "eof"
    return "term"


def run(load):
    params_path = os.environ.get("NPUSERV_PARAMS", "")
    hs_path = os.environ.get("NPUSERV_HANDSHAKE", "")
    if not params_path or not hs_path:
        raise SystemExit("NPUSERV_PARAMS and NPUSERV_HANDSHAKE must be set by the server")
    params = _read_params(params_path)
    idle = int(os.environ.get("NPUSERV_IDLE", "0"))

    ls = _bind()
    port = ls.getsockname()[1]
    # Handshake first, load second: importing icraft/numpy prints and takes seconds, and
    # the supervisor needs to know this worker is alive before the 50-132 s apply runs.
    _write_handshake(hs_path, {"proto": PROTOCOL, "port": port, "pid": os.getpid(),
                               "model": params.get("model"), "loaded": False})

    def on_term(signum, frame):
        _stop["flag"] = True
    try:
        signal.signal(signal.SIGTERM, on_term)
        signal.signal(signal.SIGINT, on_term)
    except ValueError:
        pass

    try:
        t0 = time.time()
        engine = load(params)
        engine.apply_s = getattr(engine, "apply_s", time.time() - t0)
        if not hasattr(engine, "n_steps"):     # a counter the engine owns stays its own
            engine.n_steps = 0
    except BaseException as e:
        log("load failed: %r" % (e,))
        _write_handshake(hs_path, {"proto": PROTOCOL, "port": port, "pid": os.getpid(),
                                   "model": params.get("model"), "loaded": False,
                                   "error": repr(e)})
        return 1
    _write_handshake(hs_path, {"proto": PROTOCOL, "port": port, "pid": os.getpid(),
                               "model": params.get("model"), "loaded": True,
                               "apply_s": round(engine.apply_s, 1),
                               "info": getattr(engine, "info", {})})
    log("loaded in %.1f s on 127.0.0.1:%d" % (engine.apply_s, port))

    why = "forever"
    try:
        while not _stop["flag"]:
            try:
                r = _select([ls])
            except (OSError, ValueError) as e:
                log("select on the listen socket raised %r -- exiting" % (e,))
                why = "socket-closed"
                break
            if not r:
                continue
            conn, _ = ls.accept()
            log("supervisor attached")
            why = _handle_conn(conn, engine, idle)
            try:
                conn.close()
            except Exception:
                pass
            if why in ("bye", "idle", "term"):
                break
    finally:
        try:
            ls.close()
        except Exception:
            pass
        close = getattr(engine, "close", None)
        if callable(close):
            try:
                close()
            except Exception as e:
                log("close() raised %r" % (e,))
        log("worker exit (%s)" % why)
    return 0


def _select(reads):
    import select as _s
    r, _, _ = _s.select(reads, [], [], 1.0)
    return r


if __name__ == "__main__":
    raise SystemExit("import me from a model adapter, don't run me")


# ---------------------------------------------------------------------------
# Resident mode (2026-11-08): one long-lived worker process holds ONE device and
# stacks N model sessions on it. Key insight measured on 113: openDevice() may be
# called exactly once per process -- a second call corrupts other sessions' DMA
# channels -- but any number of sessions can be layered on that one device, and
# sessions can be torn down and rebuilt cleanly (RELOAD_CYCLES_OK).
#
# The server sends: {"cmd":"load","model":id,"module":path,"params":{...}}
#                   {"cmd":"unload","model":id}
#                   {"cmd":"generate","model":id,...}   (routed by model)
#                   {"cmd":"ping"} / {"cmd":"stats"} / {"cmd":"bye"}
# The loader callable receives (params, device) and must reuse the device when
# given (npuserv injects the resident one). Engines are kept per model id.
# ---------------------------------------------------------------------------

def run_resident(load_one, bootstrap):
    """bootstrap() -> device (called once); load_one(params, device) -> engine."""
    hs_path = os.environ.get("NPUSERV_HANDSHAKE", "")
    if not hs_path:
        raise SystemExit("NPUSERV_HANDSHAKE must be set by the server")
    # NPUSERV_PARAMS is optional here: per-model params arrive with each load cmd.
    ls = _bind()
    port = ls.getsockname()[1]
    _write_handshake(hs_path, {"proto": PROTOCOL, "port": port, "pid": os.getpid(),
                               "loaded": False})

    def on_term(signum, frame):
        _stop["flag"] = True
    try:
        signal.signal(signal.SIGTERM, on_term)
        signal.signal(signal.SIGINT, on_term)
    except ValueError:
        pass

    engines = {}
    try:
        device = bootstrap()
        log("resident device ready")
    except BaseException as e:
        log("device bootstrap failed: %r" % (e,))
        _write_handshake(hs_path, {"proto": PROTOCOL, "port": port, "pid": os.getpid(),
                                   "loaded": False, "error": repr(e)})
        return 1
    _write_handshake(hs_path, {"proto": PROTOCOL, "port": port, "pid": os.getpid(),
                               "loaded": True, "apply_s": 0.0,
                               "info": {"resident": True, "engines": []}})
    log("resident worker up on 127.0.0.1:%d" % port)

    def load_model(mid, module_path, mparams):
        if mid in engines:
            return {"loaded": True, "model": mid, "note": "already resident",
                    "info": getattr(engines[mid], "info", {})}
        import importlib.util
        spec = importlib.util.spec_from_file_location("npuserv_adapter_%s" % mid,
                                                      module_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["npuserv_adapter_%s" % mid] = mod
        spec.loader.exec_module(mod)
        if not hasattr(mod, "load"):
            raise RuntimeError("adapter %s has no load()" % module_path)
        t0 = time.time()
        try:
            eng = mod.load(dict(mparams or {}), device=device)
        except TypeError:
            eng = mod.load(dict(mparams or {}))     # legacy adapters that open their own
        eng.apply_s = getattr(eng, "apply_s", round(time.time() - t0, 1))
        if not hasattr(eng, "n_steps"):
            eng.n_steps = 0
        engines[mid] = eng
        return {"loaded": True, "model": mid, "apply_s": eng.apply_s,
                "info": getattr(eng, "info", {}),
                "residents": sorted(engines)}

    def unload_model(mid):
        eng = engines.pop(mid, None)
        if eng is None:
            return {"unloaded": False, "model": mid}
        close = getattr(eng, "close", None)
        if callable(close):
            try:
                close()
            except Exception as e:
                log("close(%s) raised %r" % (mid, e))
        return {"unloaded": True, "model": mid, "residents": sorted(engines)}

    why = "forever"
    try:
        while not _stop["flag"]:
            try:
                r = _select([ls])
            except (OSError, ValueError) as e:
                log("select raised %r -- exiting" % (e,))
                why = "socket-closed"
                break
            if not r:
                continue
            conn, _ = ls.accept()
            log("supervisor attached")
            try:
                conn.settimeout(None)
                rd = wire.LineReader(conn)
                wire.send_obj(conn, {"ready": True, "resident": True,
                                     "engines": sorted(engines)})
                last = time.time()
                while not _stop["flag"]:
                    try:
                        req = rd.read(timeout=1.0)
                    except socket.timeout:
                        continue
                    except (ConnectionError, OSError):
                        break
                    if req is None:
                        break
                    cmd = req.get("cmd", "generate")
                    mid = req.get("model")
                    try:
                        if cmd == "bye":
                            wire.send_obj(conn, {"acked": True, "bye": True})
                            why = "bye"
                            break
                        if cmd == "ping":
                            wire.send_obj(conn, {"pong": True, "resident": True,
                                                 "engines": sorted(engines)})
                            continue
                        if cmd == "load":
                            wire.send_obj(conn, dict(
                                load_model(mid, req["module"], req.get("params")),
                                end=True))
                            continue
                        if cmd == "unload":
                            wire.send_obj(conn, dict(unload_model(mid), end=True))
                            continue
                        eng = engines.get(mid)
                        if eng is None:
                            wire.send_obj(conn, {"err": "model %r not resident; /load first" % mid,
                                                 "end": True})
                            continue
                        req.pop("cmd", None)
                        out = eng.generate(req, lambda ev: wire.send_obj(conn, ev))
                        ev = dict(out or {})
                        ev["end"] = True
                        wire.send_obj(conn, ev)
                    except Exception as e:
                        log("cmd %r failed: %r" % (cmd, e))
                        try:
                            wire.send_obj(conn, {"err": repr(e), "end": True})
                        except Exception:
                            break
                try:
                    conn.close()
                except Exception:
                    pass
            except Exception as e:
                log("conn handling failed: %r" % (e,))
            if why in ("bye",):
                break
    finally:
        try:
            ls.close()
        except Exception:
            pass
        for mid, eng in list(engines.items()):
            close = getattr(eng, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as e:
                    log("close(%s) raised %r" % (mid, e))
        log("resident worker exit (%s)" % why)
    return 0
