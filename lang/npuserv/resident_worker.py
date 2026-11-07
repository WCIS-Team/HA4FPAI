# -*- coding: utf-8 -*-
"""Resident worker: one process, one device, N stacked model sessions.

Spawned by the server with NPUSERV_BOOTSTRAP naming a bootstrap module:
    def bootstrap() -> device        # openDevice exactly once

Server protocol over the control socket (wire JSON lines):
    {"cmd":"load","model":id,"module":<adapter .py path>,"params":{...}}
    {"cmd":"unload","model":id}
    {"cmd":"generate","model":id, ...}   (routed to that engine)
    {"cmd":"ping"} / {"cmd":"bye"}

Adapters loaded this way receive load(params, device=<shared>) when they accept a
second parameter; legacy adapters (load(params)) keep opening their own device —
which works only while they are the FIRST and ONLY session, so resident adapters
should be updated to accept the device (see Qwen3 q3_infer_board.init_device).
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import adapter_lib                                        # noqa: E402


def main():
    boot_path = os.environ.get("NPUSERV_BOOTSTRAP", "")
    if not boot_path:
        raise SystemExit("NPUSERV_BOOTSTRAP must name a bootstrap module")
    import importlib.util
    spec = importlib.util.spec_from_file_location("npuserv_bootstrap", boot_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "bootstrap"):
        raise SystemExit("bootstrap module has no bootstrap()")
    adapter_lib.run_resident(mod.load_one, mod.bootstrap)


if __name__ == "__main__":
    main()
