#!/usr/bin/env python3
"""Bounded host orchestrator soak; never loads models or invokes hardware."""
import argparse
from pathlib import Path
import socket
import sys
import threading
import tracemalloc
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CI, ROOT, config, write_json


def current_rss():
    status = Path("/proc/self/status")
    if status.is_file():
        for line in status.read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    return None


def snapshot():
    directory = Path("/proc/self/fd")
    return {"rss_kib": current_rss(), "python_bytes": tracemalloc.get_traced_memory()[0],
            "threads": threading.active_count(),
            "fds": len(list(directory.iterdir())) if directory.is_dir() else None}


def run(settings):
    sys.path.insert(0, str(ROOT / "lang/server"))
    from cfg import Config
    from core import Chain
    chain = Chain(Config(str(CI / "fixtures")))
    iterations = settings["iterations"]
    if type(iterations) is not int or not 3 <= iterations <= 100000:
        raise ValueError("Invalid soak iteration count")
    # Trace retained Python objects, not transient full audio buffers.
    tracemalloc.start()
    for _ in range(3):
        result = chain.run_text("长跑检查", {"tts": False})
        if result.get("code") != 0 or result.get("stages_error"):
            raise AssertionError("Soak warmup failed")
    before = snapshot()
    for _ in range(iterations):
        result = chain.run_text("长跑检查", {"tts": False})
        if result.get("code") != 0 or result.get("stages_error") or not result.get("text"):
            raise AssertionError("Soak request failed")
        if chain.gate_snapshot()["running"] != 0:
            raise AssertionError("Admission slot leaked")
    after = snapshot()
    tracemalloc.stop()
    for key in ("threads", "fds"):
        if before[key] is not None and after[key] > before[key]:
            raise AssertionError("Resource count grew: " + key)
    limit = settings["max_rss_growth_kib"]
    if after["python_bytes"] - before["python_bytes"] > limit * 1024:
        raise AssertionError("Retained Python allocations grew beyond the limit")
    if before["rss_kib"] is not None and after["rss_kib"] - before["rss_kib"] > limit:
        raise AssertionError("RSS grew beyond the limit")
    return {"schema_version": 1, "status": "passed", "iterations": iterations,
            "before": before, "after": after, "gate": chain.gate_snapshot(),
            "notes": ["Text orchestration without TTS; model/native allocator endurance is a separate device test.",
                      "RSS is sampled at the boundaries, not a peak measurement; unavailable on Windows."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CI / "config.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        with patch.object(socket.socket, "connect", side_effect=AssertionError("Network disabled in host soak")):
            result = run(config(args.config)["soak"])
    except Exception as exc:
        result = {"schema_version": 1, "status": "failed", "error": "%s: %s" % (type(exc).__name__, exc)}
    write_json(args.output, result)
    print("Soak: %s; %s" % (result["status"], args.output))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
