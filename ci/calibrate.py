#!/usr/bin/env python3
"""Demonstrate that isolated fault variants turn their specific contract tests red."""
import argparse
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, write_json

MUTATIONS = [
    ("FM01", "lang/server/cfg.py", "return v[1:end] if end != -1 else v[1:]", "return v[1:].split('#')[0].strip()", "test_contracts", "Contracts.test_FM01_configuration_overlay_and_registry"),
    ("FM02", "lang/split/tts_split.py", "    s = text.strip()", "    s = text.strip()[:1]", "test_contracts", "Contracts.test_FM02_audio_and_parameter_matrix"),
    ("FM03", "lang/server/core.py", "                if not hit:", "                if False:", "test_contracts", "Contracts.test_FM03_wake_gate_and_silence_stop_downstream"),
    ("FM04", "lang/server/core.py", '                    out["stages_error"]["llm"] = ("%s: %s" % (type(exc).__name__, exc))[:200]', "                    pass", "test_contracts", "Contracts.test_FM04_transient_persistent_oom_io_and_compound_failure"),
    ("FM05", "lang/server/core.py", "            self._gate.release()", "            pass", "test_contracts", "Contracts.test_FM05_gate_timeout_and_exception_release"),
    ("FM06", "lang/server/app.py", '\n        return self._send(404, {"code": 404, "msg": "no such path: " + path})', '\n        return self._send(200, {"code": 404, "msg": "no such path: " + path})', "test_contracts", "Contracts.test_FM06_http_content_and_compatibility"),
    ("FM07", "klar/klar_executor.py", "\n    if dry_run:\n", "\n    if False:\n", "test_contracts", "Contracts.test_FM07_executor_dry_run_and_partial_outage"),
    ("FM08", "ci/report.py", 'regression = change > max(abs(before) * gate["relative"], gate["absolute"])', "regression = False", "test_infrastructure", "Infrastructure.test_FM08_regression_and_tolerances"),
]


def invoke(source, module, method, log):
    process = subprocess.run([sys.executable, "-I", "-B", "-X", "utf8",
                              str(source / "ci/tests" / (module + ".py"))] + ([method] if method else []),
                             capture_output=True, encoding="utf-8", errors="replace", timeout=45)
    text = process.stdout + process.stderr
    with log.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
    match = re.search(r"Ran (\d+) tests? in", text)
    return process.returncode, int(match.group(1)) if match else 0, text


def calibrate(output):
    scratch = output.parent / ("calibration-source-" + uuid.uuid4().hex[:12])
    scratch.mkdir(parents=True)
    for relative in ("ci", "lang/server", "lang/split", "lang/kws", "klar"):
        for path in (ROOT / relative).rglob("*.py"):
            if set(path.parts).intersection(("__pycache__", "archive", "models")):
                continue
            target = scratch / path.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(str(path), str(target))
    (scratch / "ci/fixtures").mkdir(exist_ok=True)
    for name in ("mock.yaml", "asr-zh.wav.b64", "asr-zh.json"):
        shutil.copyfile(str(ROOT / "ci/fixtures" / name), str(scratch / "ci/fixtures" / name))
    rows = []
    for module in ("test_contracts", "test_infrastructure"):
        code, count, _ = invoke(scratch, module, None, output.parent / ("calibration-green-" + module + ".log"))
        if code or count == 0:
            raise AssertionError("Unmodified calibration source did not pass: " + module)
        rows.append({"id": module, "status": "green", "test_methods": count})
    for identity, relative, old, new, module, method in MUTATIONS:
        path = scratch / relative
        original = path.read_text(encoding="utf-8")
        if original.count(old) != 1:
            raise AssertionError("Mutation point changed; update the injection: " + identity)
        try:
            with path.open("w", encoding="utf-8", newline="\n") as stream:
                stream.write(original.replace(old, new))
            code, count, text = invoke(scratch, module, method, output.parent / ("calibration-red-" + identity + ".log"))
            killed = code != 0 and count == 1 and ("FAIL: test_" + identity in text or "ERROR: test_" + identity in text)
            rows.append({"id": identity, "status": "killed" if killed else "survived", "exit_code": code,
                         "test": method, "file": relative})
            print("Calibration %s: %s" % (identity, rows[-1]["status"]), flush=True)
        finally:
            with path.open("w", encoding="utf-8", newline="\n") as stream:
                stream.write(original)
    return {"schema_version": 1, "status": "passed" if all(r["status"] in ("green", "killed") for r in rows) else "failed",
            "checks": rows, "scratch": str(scratch),
            "notes": ["Targeted behavioral mutations demonstrate sensitivity, not exhaustive mutation coverage.",
                      "Shared working tree and device code were never mutated."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = calibrate(args.output.resolve())
    except Exception as exc:
        result = {"schema_version": 1, "status": "failed", "error": "%s: %s" % (type(exc).__name__, exc)}
    write_json(args.output, result)
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
