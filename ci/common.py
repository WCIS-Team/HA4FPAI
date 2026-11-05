#!/usr/bin/env python3
"""Shared CI data and reporting helpers; Python 3.8+, standard library only."""
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys

CI = Path(__file__).resolve().parent
ROOT = CI.parent


def load_json(path):
    def reject_constant(value):
        raise ValueError("Non-finite JSON value: " + value)
    with Path(path).open(encoding="utf-8") as stream:
        return json.load(stream, parse_constant=reject_constant)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2,
                                allow_nan=False) + "\n")
    temporary.replace(path)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    allow_nan=False).encode("utf-8")).hexdigest()


def file_digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def distribution(values):
    values = sorted(float(value) for value in values)
    if not values or not all(math.isfinite(x) and x >= 0 for x in values):
        raise ValueError("Samples must be finite, nonnegative and nonempty")
    return {"n": len(values), "min": values[0], "max": values[-1],
            "mean": statistics.mean(values), "p50": statistics.median(values),
            "p95": values[max(0, math.ceil(0.95 * len(values)) - 1)],
            "stdev": statistics.stdev(values) if len(values) > 1 else 0.0}


def config(path=None):
    obj = load_json(path or CI / "config.json")
    if obj.get("schema_version") != 1:
        raise ValueError("Unsupported CI configuration schema")
    benchmark = obj["benchmark"]
    for key, low, high in (("warmup", 0, 1000), ("samples", 3, 10000)):
        value = benchmark[key]
        if type(value) is not int or not low <= value <= high:
            raise ValueError("Invalid benchmark " + key)
    seen = set()
    for variant in benchmark["variants"]:
        if not variant["id"] or variant["id"] in seen:
            raise ValueError("Variant identifiers must be unique and nonempty")
        seen.add(variant["id"])
        if not 1 <= variant["max_chars"] <= 1000 or variant["out_rate"] not in (8000, 16000, 22050, 24000, 44100, 48000):
            raise ValueError("Invalid benchmark variant parameters")
    if not seen:
        raise ValueError("At least one benchmark variant is required")
    for gate in benchmark["gates"].values():
        if gate["direction"] not in ("lower", "higher") or not 0 <= gate["relative"] <= 10 or gate["absolute"] < 0:
            raise ValueError("Invalid benchmark regression gate")
    if not 0 < benchmark["input_seconds"] <= 30 or benchmark["input_rate"] not in (8000, 16000, 48000):
        raise ValueError("Invalid benchmark audio input")
    return obj


def git_metadata(root):
    def command(*args):
        try:
            result = subprocess.run(["git", "-C", str(root)] + list(args),
                                    capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", timeout=15)
            return result.stdout.strip() if result.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            return None
    top = command("rev-parse", "--show-toplevel")
    if not top or Path(top).resolve() != Path(root).resolve():
        return {"commit": None, "dirty": None}
    status = command("status", "--porcelain", "--untracked-files=normal")
    return {"commit": command("rev-parse", "HEAD"),
            "dirty": bool(status) if status is not None else None}


def environment():
    cpu = platform.processor()
    path = Path("/proc/cpuinfo")
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.lower().startswith(("model name", "hardware")):
                cpu = line.partition(":")[2].strip()
                break
    return {"device_id": platform.node(), "system": platform.system(),
            "architecture": platform.machine(), "python": platform.python_version(),
            "implementation": platform.python_implementation(), "cpu": cpu,
            "cpus": os.cpu_count()}


def rss_kib():
    try:
        import resource
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return value / 1024.0 if sys.platform == "darwin" else float(value)
    except ImportError:
        return None


def source_fingerprint(root):
    paths = set()
    for relative in ("lang/server", "lang/split", "lang/kws"):
        for path in (root / relative).rglob("*.py"):
            if not set(path.relative_to(root).parts).intersection(("tests", "archive", "__pycache__")):
                paths.add(path.relative_to(root).as_posix())
    if not {"lang/server/core.py", "lang/server/cfg.py", "lang/split/tts_split.py"} <= paths:
        raise ValueError("Benchmark source tree is incomplete")
    return {path: file_digest(root / path) for path in sorted(paths)}
