#!/usr/bin/env python3
"""Small Actions adapters; all executable inputs travel through argv or environment."""
import argparse
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CI, ROOT, config, load_json, write_json


def adapter_pin():
    sources = config()["adapter_sources"]
    if len(sources) != 1:
        raise ValueError("The adapter workflow currently expects one external source")
    source = sources[0]
    repository, path = source["repository"], source["path"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Invalid adapter repository")
    if (ROOT not in (ROOT / path).resolve().parents or "\n" in path or "\r" in path):
        raise ValueError("Invalid adapter checkout path")
    tree = subprocess.run(["git", "-C", str(ROOT), "ls-tree", "HEAD", "--", path], check=True,
                          capture_output=True, text=True, encoding="utf-8", timeout=15).stdout
    parts = tree.partition("\t")[0].split()
    if len(parts) != 3 or parts[:2] != ["160000", "commit"] or not re.fullmatch(r"[0-9a-f]{40}", parts[2]):
        raise ValueError("Adapter source must have a pinned gitlink in the parent checkout")
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8", newline="\n") as stream:
        for key, value in (("repository", repository), ("path", path), ("ref", parts[2])):
            stream.write(key + "=" + value + "\n")


def append_summary(path):
    if path.is_file():
        text = path.read_text(encoding="utf-8")
    else:
        text = "CI did not produce its expected summary: `%s`. Inspect the uploaded logs.\n" % path.name
    destination = os.environ.get("GITHUB_STEP_SUMMARY")
    if destination:
        with Path(destination).open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(text + "\n")
    print(text)


def device(output):
    output.mkdir(parents=True, exist_ok=True)
    mode = os.environ.get("DEVICE_MODE", "health")
    if mode not in ("health", "benchmark", "soak"):
        raise ValueError("Invalid device mode")
    manifest_path = ROOT / os.environ.get("DEVICE_MANIFEST", "ci/device.package.json")
    if ROOT not in manifest_path.resolve().parents:
        raise ValueError("Device manifest must be inside the trusted checkout")
    target = os.environ.get("DEVICE_TARGET", "")
    if not target:
        raise ValueError("Configure HA4FPAI_SSH_TARGET in the device-ci environment")
    # Keys are ephemeral runner files and never placed in an uploaded report directory.
    with tempfile.TemporaryDirectory(prefix="ha4fpai-ssh-", dir=os.environ.get("RUNNER_TEMP")) as temporary:
        directory = Path(temporary)
        identity, known = directory / "identity", directory / "known_hosts"
        for path, variable in ((identity, "DEVICE_SSH_KEY"), (known, "DEVICE_KNOWN_HOSTS")):
            value = os.environ.get(variable, "").strip()
            if not value:
                raise ValueError("Missing environment secret: " + variable)
            with path.open("w", encoding="utf-8", newline="\n") as stream:
                stream.write(value + "\n")
            path.chmod(0o600)
        command = [sys.executable, "-I", "-B", str(CI / "remote.py"), "--target", target,
                   "--identity", str(identity), "--known-hosts", str(known), "--mode", mode,
                   "--manifest", str(manifest_path), "--timeout", "900", "--output", str(output / "device.json")]
        if os.environ.get("DEVICE_ALLOW_INFERENCE") == "true":
            command.append("--allow-inference")
        clean = {key: value for key, value in os.environ.items() if key not in ("DEVICE_SSH_KEY", "DEVICE_KNOWN_HOSTS")}
        result = subprocess.run(command, env=clean, timeout=1000)
    record = load_json(output / "device.json")
    lines = ["# HA4FPAI device %s: %s" % (mode, record["status"]), "",
             "| Check | Status | Detail |", "|---|---|---|"]
    lines.extend("| %s | %s | %s |" % (r["id"], r["status"], r.get("error_type", r.get("reason", ""))) for r in record.get("checks", []))
    if "message" in record:
        lines.extend(["", record["message"]])
    with (output / "summary.md").open("w", encoding="utf-8", newline="\n") as stream:
        stream.write("\n".join(lines) + "\n")
    if mode == "health" or result.returncode:
        return result.returncode
    baseline = Path(os.environ.get("DEVICE_BASELINE", ""))
    requested = bool(os.environ.get("DEVICE_BASELINE", ""))
    if requested and not baseline.is_file():
        raise ValueError("Requested device baseline is missing")
    command = [sys.executable, "-I", "-B", str(CI / "report.py"), "--candidate", str(output / "device.json"),
               "--gates", str(manifest_path), "--output", str(output / "comparison")]
    if requested:
        command += ["--baseline", str(baseline)]
    if os.environ.get("DEVICE_ALLOW_CONFIG_CHANGE") == "true":
        command.append("--allow-config-change")
    compared = subprocess.run(command, timeout=30)
    append_summary(output / "comparison.md")
    return compared.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("matrix", "adapter-pin", "summary", "status", "device"))
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--file", default="summary.md")
    args = parser.parse_args()
    if args.action == "matrix":
        matrix = json.dumps({"include": config()["test_matrix"]}, separators=(",", ":"))
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8", newline="\n") as stream:
            stream.write("matrix=" + matrix + "\n")
    elif args.action == "adapter-pin":
        adapter_pin()
    elif args.action == "status":
        states = json.loads(os.environ["NEEDS_JSON"])
        if not states or any(value["result"] != "success" for value in states.values()):
            print("Required CI dependencies did not all succeed")
            return 1
        print("All required checks passed")
    elif args.action == "summary":
        append_summary(args.directory / args.file)
    else:
        try:
            return device(args.directory)
        except Exception as exc:
            args.directory.mkdir(parents=True, exist_ok=True)
            write_json(args.directory / "action-error.json", {"status": "failed", "error_type": type(exc).__name__,
                                                             "message": str(exc)})
            print("Device action failed: %s" % type(exc).__name__)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
