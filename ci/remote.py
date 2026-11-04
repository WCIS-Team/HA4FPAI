#!/usr/bin/env python3
"""Run the standalone probe through OpenSSH, with pinned host keys and bounded time."""
import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import uuid
import wave

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CI, ROOT, load_json, write_json


def prepare(manifest, mode, root):
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported device manifest schema")
    if mode == "health":
        return manifest
    for spec in manifest.get("workloads", []):
        if not spec.get("enabled", True) or "audio_file" not in spec:
            continue
        path = Path(spec["audio_file"])
        if not path.is_absolute():
            path = root / path
        if not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("Audio corpus file is missing or exceeds 8 MiB")
        data = path.read_bytes()
        encoding = spec.get("audio_encoding", "wav")
        if encoding == "base64":
            data = base64.b64decode(data.strip(), validate=True)
        elif encoding != "wav":
            raise ValueError("Unsupported audio corpus encoding")
        actual_hash = hashlib.sha256(data).hexdigest()
        if "corpus_metadata" in spec:
            metadata_path = Path(spec["corpus_metadata"])
            metadata = load_json(metadata_path if metadata_path.is_absolute() else root / metadata_path)
            if metadata.get("schema_version") != 1 or metadata.get("sha256") != actual_hash:
                raise ValueError("Corpus does not match its versioned metadata")
            if not isinstance(metadata.get("text"), str) or not metadata["text"].strip():
                raise ValueError("Corpus metadata needs a reference transcript")
            if "expected_text" in spec and spec["expected_text"] != metadata["text"]:
                raise ValueError("Conflicting reference transcripts")
            spec["expected_text"] = metadata["text"]
            del spec["corpus_metadata"]
        with wave.open(io.BytesIO(data), "rb") as audio:
            if (audio.getsampwidth() != 2 or audio.getnchannels() != 1 or
                    audio.getnframes() <= 0 or audio.getframerate() not in (8000, 16000, 22050, 24000, 44100, 48000)):
                raise ValueError("Use nonempty mono PCM16 WAV corpus files at a supported sample rate")
            frames = audio.getnframes()
            if len(audio.readframes(frames)) != frames * 2:
                raise ValueError("Truncated WAV corpus file")
            spec["input_seconds"] = frames / float(audio.getframerate())
        spec["audio_base64"] = base64.b64encode(data).decode("ascii")
        spec["audio_sha256"] = actual_hash
        del spec["audio_file"]
    return manifest


def transport(options, manifest):
    target = options.target
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@-]*", target) or target.count("@") > 1:
        raise ValueError("Invalid SSH target; use a host alias or user@host")
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", options.remote_python):
        raise ValueError("Invalid remote Python executable")
    known_hosts = options.known_hosts.expanduser().resolve(strict=True)
    common = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
              "-o", "ConnectTimeout=10", "-o", "ConnectionAttempts=1",
              "-o", "UserKnownHostsFile=" + str(known_hosts),
              "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2"]
    if options.identity:
        common += ["-i", str(options.identity.expanduser().resolve(strict=True)), "-o", "IdentitiesOnly=yes"]
    if options.port:
        common += ["-o", "Port=" + str(options.port)]
    remote_file = "/tmp/ha4fpai-ci-" + uuid.uuid4().hex + ".py"
    uploaded = False
    try:
        subprocess.run(["scp"] + common + [str(CI / "device_probe.py"), target + ":" + remote_file],
                       capture_output=True, check=True, timeout=45)
        uploaded = True
        command = [options.remote_python, "-I", remote_file, "--mode", options.mode,
                   "--deadline", str(options.timeout)]
        if options.allow_inference:
            command.append("--allow-inference")
        result = subprocess.run(["ssh", "-T"] + common + [target, shlex.join(command)],
                                input=json.dumps(manifest).encode("utf-8"), capture_output=True,
                                timeout=options.timeout + 30)
        try:
            output = json.loads(result.stdout.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise RuntimeError("SSH probe returned no valid JSON; check SSH access and remote Python")
        if not isinstance(output, dict) or output.get("schema_version") != 1:
            raise RuntimeError("SSH probe returned an unsupported report")
        if result.returncode and output.get("status") == "passed":
            output["status"] = "failed"
            output["transport_error"] = "SSH exited with status %s" % result.returncode
        return output
    finally:
        if uploaded:
            try:
                # Only remove the unique script that this invocation uploaded, never product files.
                subprocess.run(["ssh", "-T"] + common + [target, "rm -- " + shlex.quote(remote_file)],
                               capture_output=True, timeout=15)
            except (OSError, subprocess.TimeoutExpired):
                pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, help="Existing SSH host alias or user@host")
    parser.add_argument("--known-hosts", type=Path, default=Path.home() / ".ssh/known_hosts")
    parser.add_argument("--identity", type=Path)
    parser.add_argument("--port", type=int)
    parser.add_argument("--remote-python", default="python3")
    parser.add_argument("--manifest", type=Path, default=CI / "device.package.json")
    parser.add_argument("--corpus-root", type=Path, default=ROOT)
    parser.add_argument("--mode", choices=("health", "benchmark", "soak"), default="health")
    parser.add_argument("--allow-inference", action="store_true")
    parser.add_argument("--timeout", type=int, help="Overall seconds; defaults to 900 for soak, 300 otherwise")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.timeout is None:
        args.timeout = 900 if args.mode == "soak" else 300
    if not 10 <= args.timeout <= 3600 or (args.port and not 1 <= args.port <= 65535):
        parser.error("Timeout must be 10..3600 seconds and port must be valid")
    try:
        manifest = prepare(load_json(args.manifest), args.mode, args.corpus_root)
        if args.mode != "health" and not args.allow_inference and any(w.get("enabled", True) for w in manifest.get("workloads", [])):
            raise ValueError("Use --allow-inference to run the explicitly configured inference workloads")
        output = transport(args, manifest)
    except Exception as exc:
        output = {"schema_version": 1, "status": "failed", "error_type": type(exc).__name__,
                  "message": str(exc) if isinstance(exc, (ValueError, RuntimeError, FileNotFoundError)) else
                  "Device transport failed; check reachability, known_hosts, SSH key and remote Python"}
    write_json(args.output, output)
    print("Device %s: %s; report=%s" % (args.mode, output["status"], args.output))
    for row in output.get("checks", []):
        print("  %s %s%s" % (row["status"], row["id"], " (" + row["error_type"] + ")" if "error_type" in row else ""))
    return 0 if output["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
