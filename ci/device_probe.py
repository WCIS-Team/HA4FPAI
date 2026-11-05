#!/usr/bin/env python3
"""Standalone Linux device probe, invoked over SSH with its manifest on stdin."""
import argparse
import base64
import hashlib
import io
import json
import math
from pathlib import Path
import platform
import signal
import socket
import statistics
import sys
import time
import urllib.request
import wave

MAX_BODY = 16 * 1024 * 1024
HEALTH_PATHS = {"/health", "/components", "/stats", "/status", "/api/health", "/api/status"}
INFERENCE_PATHS = {"/api/v1/asr", "/v1/audio/transcriptions", "/tts"}


class ProbeDeadline(BaseException):
    """Escape per-check exception handlers when the whole probe expires."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        raise ValueError("Redirects are not permitted in device probes")


def validate(manifest, mode):
    if manifest.get("schema_version") != 1 or mode not in ("health", "benchmark", "soak"):
        raise ValueError("Unsupported manifest schema or mode")
    for key, default, low, high in (("samples", 10, 3, 10000), ("warmup", 2, 0, 100),
                                    ("soak_iterations", 100, 3, 10000),
                                    ("max_rss_growth_kib", 16384, 0, 1048576)):
        value = manifest.get(key, default)
        if type(value) is not int or not low <= value <= high:
            raise ValueError("Invalid manifest parameter: " + key)
    if not isinstance(manifest.get("source_root"), str) or not isinstance(manifest.get("configuration_root", ""), str):
        raise ValueError("Source and configuration roots must be strings")
    for key in ("source_files", "configuration_files", "process_patterns"):
        items = manifest.get(key, [])
        if not isinstance(items, list) or any(not isinstance(p, str) or not p for p in items):
            raise ValueError("Invalid file/process list: " + key)
    seen, enabled = set(), {}
    for service in manifest.get("services", []):
        identity = service.get("id")
        if not isinstance(identity, str) or not identity or identity in seen:
            raise ValueError("Service identifiers must be unique and nonempty")
        seen.add(identity)
        if type(service.get("enabled", True)) is not bool:
            raise ValueError("enabled must be a boolean")
        if not service.get("enabled", True):
            continue
        if service.get("host", "127.0.0.1") not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("Only loopback services are allowed")
        if type(service.get("port")) is not int or not 1 <= service["port"] <= 65535:
            raise ValueError("Invalid service port")
        timeout = service.get("timeout_s", 10)
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 120:
            raise ValueError("Invalid service timeout")
        if not service.get("checks"):
            raise ValueError("Enabled services need health checks")
        paths = set()
        for endpoint in service["checks"]:
            path = endpoint.get("path")
            if path not in HEALTH_PATHS or path in paths or not isinstance(endpoint.get("expect", {}), dict):
                raise ValueError("Invalid or duplicate health contract")
            paths.add(path)
        enabled[identity] = service
    if not enabled:
        raise ValueError("At least one enabled service with health checks is required")
    for spec in manifest.get("wyoming", []):
        if not spec.get("enabled", True):
            continue
        if (spec.get("capability") not in ("wake", "asr", "tts") or
                type(spec.get("port")) is not int or not 1 <= spec["port"] <= 65535 or
                not 0 < spec.get("timeout_s", 5) <= 30):
            raise ValueError("Invalid Wyoming contract")
    workload_ids = set()
    for spec in manifest.get("workloads", []):
        identity = spec.get("id")
        if not isinstance(identity, str) or not identity or identity in workload_ids:
            raise ValueError("Workload identifiers must be unique and nonempty")
        workload_ids.add(identity)
        if not spec.get("enabled", True):
            continue
        if spec.get("service") not in enabled:
            raise ValueError("Workload refers to a disabled or missing service")
        allowed = {"tts": {"/tts"}, "asr": {"/api/v1/asr", "/v1/audio/transcriptions"}}
        if spec.get("kind") not in allowed or spec.get("path") not in allowed[spec["kind"]]:
            raise ValueError("Only ASR and TTS inference is allowed; action-capable chains need an isolated test")
        if spec["kind"] == "tts" and (not isinstance(spec.get("text"), str) or not spec["text"].strip()):
            raise ValueError("TTS needs nonempty text")
        if "expected_text" in spec:
            reference, limit = spec["expected_text"], spec.get("max_cer", 0)
            if (not isinstance(reference, str) or not reference.strip() or len(reference) > 4096 or
                    type(limit) not in (int, float) or not math.isfinite(limit) or not 0 <= limit <= 10):
                raise ValueError("Invalid transcription reference or CER limit")
    if mode != "health" and not any(w.get("enabled", True) for w in manifest.get("workloads", [])):
        raise ValueError("Inference mode needs at least one enabled workload")
    return enabled


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def stats(values):
    values = sorted(values)
    return {"n": len(values), "mean": statistics.mean(values), "p50": statistics.median(values),
            "p95": values[math.ceil(0.95 * len(values)) - 1], "min": values[0], "max": values[-1]}


def get_path(obj, field):
    for key in field.split("."):
        obj = obj[key]
    return obj


def require_fields(obj, expected):
    if not isinstance(obj, dict):
        raise ValueError("Expected a JSON object")
    for key, value in expected.items():
        actual = get_path(obj, key)
        if type(actual) is not type(value) or actual != value:
            raise ValueError("Response field does not match contract: " + key)


def http(service, path, data=None, content_type=None):
    host, port = service.get("host", "127.0.0.1"), service["port"]
    if host not in ("127.0.0.1", "localhost", "::1") or type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Probes must address a loopback service with a valid port")
    if path not in (INFERENCE_PATHS if data is not None else HEALTH_PATHS):
        raise ValueError("Endpoint is not in the read-only/inference allowlist")
    if host == "::1":
        host = "[::1]"
    request = urllib.request.Request("http://%s:%d%s" % (host, port, path), data=data,
                                     headers={"Content-Type": content_type} if content_type else {})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    started = time.perf_counter_ns()
    with opener.open(request, timeout=service.get("timeout_s", 10)) as response:
        raw = response.read(MAX_BODY + 1)
        if len(raw) > MAX_BODY:
            raise ValueError("Response exceeded the probe size limit")
        status, content = response.status, response.headers.get("Content-Type", "")
    elapsed = (time.perf_counter_ns() - started) / 1e6
    if status != 200:
        raise ValueError("HTTP response was not 200")
    if "json" in content:
        return json.loads(raw.decode("utf-8")), elapsed, "json"
    return raw, elapsed, content


def wyoming(spec):
    with socket.create_connection(("127.0.0.1", spec["port"]), timeout=spec.get("timeout_s", 5)) as connection:
        connection.sendall(b'{"type":"describe"}\n')
        stream = connection.makefile("rb")
        with stream:
            line = stream.readline(65537)
            if len(line) > 65536 or not line.endswith(b"\n"):
                raise ValueError("Invalid Wyoming frame header")
            header = json.loads(line)
            length = header.get("data_length", 0)
            if type(length) is not int or not 0 <= length <= 1024 * 1024:
                raise ValueError("Invalid Wyoming data length")
            data = header.get("data", {})
            if length:
                raw = stream.read(length)
                if len(raw) != length:
                    raise ValueError("Truncated Wyoming info frame")
                data = json.loads(raw)
            if header.get("type") != "info" or not data.get(spec["capability"]):
                raise ValueError("Missing Wyoming capability: " + spec["capability"])


def hashes(root, paths):
    result = {}
    root = Path(root).resolve()
    for relative in paths:
        path = (root / relative).resolve()
        if root not in path.parents or path.name in ("online.yaml", ".env"):
            raise ValueError("Invalid or secret-bearing fingerprint path")
        if not path.is_file():
            raise FileNotFoundError("Missing fingerprint file: " + relative)
        result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def resources(patterns):
    result = {"processes": [], "rss_kib": 0, "cpu_ticks": 0,
              "pattern_counts": {pattern: 0 for pattern in patterns}}
    for directory in Path("/proc").glob("[0-9]*"):
        try:
            argv = (directory / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
            if not any(pattern in argv for pattern in patterns):
                continue
            status = (directory / "status").read_text(encoding="utf-8")
            values = {line.split(":", 1)[0]: line.split(":", 1)[1].strip() for line in status.splitlines() if ":" in line}
            memory = int(values.get("VmRSS", "0 kB").split()[0])
            fields = (directory / "stat").read_text(encoding="utf-8").rpartition(")")[2].split()
            ticks = int(fields[11]) + int(fields[12])
            result["processes"].append({"pid": int(directory.name), "rss_kib": memory})
            for pattern in patterns:
                if pattern in argv:
                    result["pattern_counts"][pattern] += 1
            result["rss_kib"] += memory
            result["cpu_ticks"] += ticks
        except (OSError, ValueError, IndexError):
            continue
    return result


def inference(service, spec):
    path = spec["path"]
    kind = spec["kind"]
    if kind == "tts":
        payload = json.dumps({"text": spec["text"]}, ensure_ascii=False).encode("utf-8")
        content = "application/json"
    elif kind in ("asr", "chain"):
        payload, content = base64.b64decode(spec["audio_base64"], validate=True), "audio/wav"
    else:
        raise ValueError("Unknown inference workload kind")
    obj, elapsed, ctype = http(service, path, payload, content)
    extra = {}
    if kind == "tts":
        if not isinstance(obj, bytes) or not obj.startswith(b"RIFF"):
            raise ValueError("TTS did not produce a WAV file")
        with wave.open(io.BytesIO(obj), "rb") as audio:
            frames = audio.getnframes()
            pcm = audio.readframes(frames)
            seconds = frames / float(audio.getframerate())
            if (seconds <= 0 or audio.getsampwidth() != 2 or
                    len(pcm) != frames * audio.getnchannels() * 2 or not any(pcm)):
                raise ValueError("TTS produced empty, silent or invalid PCM16 audio")
            extra["rtf"] = elapsed / (seconds * 1000)
    else:
        if (not isinstance(obj, dict) or type(obj.get("code", 0)) is not int or
                obj.get("code", 0) != 0 or obj.get("stages_error")):
            raise ValueError("Inference response contains a stage failure")
        text = obj.get("asr_text" if kind == "chain" else "text", "")
        if not isinstance(text, str) or not text.strip() or len(text) > 4096:
            raise ValueError("Inference response contains no bounded transcript")
        if "expected_text" in spec:
            error = character_error_rate(spec["expected_text"], text)
            if error > spec.get("max_cer", 0):
                raise ValueError("Transcript character error rate exceeds the limit")
            extra["cer"] = error
        for stage, value in obj.get("stages_ms", {}).items():
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError("Invalid stage duration")
            extra["stage_" + stage + "_ms"] = value
        if spec.get("input_seconds"):
            extra["rtf"] = elapsed / (spec["input_seconds"] * 1000)
    return elapsed, extra


def character_error_rate(expected, actual):
    # Strip whitespace only; punctuation/case are deliberately part of the corpus contract.
    expected, actual = "".join(expected.split()), "".join(actual.split())
    if not expected:
        raise ValueError("CER requires a nonempty reference transcript")
    previous = list(range(len(actual) + 1))
    for i, char in enumerate(expected, 1):
        current = [i]
        for j, other in enumerate(actual, 1):
            current.append(min(current[-1] + 1, previous[j] + 1,
                               previous[j - 1] + (char != other)))
        previous = current
    return previous[-1] / float(len(expected))


def probe(manifest, mode, allow_inference=False):
    services = validate(manifest, mode)
    samples, warmup = manifest.get("samples", 10), manifest.get("warmup", 2)
    rows, metrics, raw = [], {}, {}
    if mode != "health" and not allow_inference:
        raise ValueError("Inference requires --allow-inference")
    initial_files = {}
    for category, key in (("source", "source_files"), ("configuration", "configuration_files")):
        try:
            file_root = manifest.get("configuration_root", manifest["source_root"]) if category == "configuration" else manifest["source_root"]
            initial_files[category] = hashes(file_root, manifest.get(key, []))
            if not initial_files[category]:
                raise ValueError("No fingerprint files configured")
        except Exception as exc:
            initial_files[category] = {}
            rows.append({"id": category + "-fingerprint", "status": "failed", "error_type": type(exc).__name__})
    for item in manifest["services"]:
        if not item.get("enabled", True):
            rows.append({"id": item["id"], "status": "disabled"})
    for identity, service in services.items():
        for endpoint in service["checks"]:
            try:
                obj, elapsed, _ = http(service, endpoint["path"])
                require_fields(obj, endpoint.get("expect", {}))
                observed = {key: obj[key] for key in ("service", "config", "stages") if key in obj} if endpoint["path"] == "/health" else {}
                rows.append({"id": identity + endpoint["path"], "status": "passed", "observed": observed})
                metrics[identity + endpoint["path"] + ".health_ms"] = {"value": elapsed}
            except Exception as exc:
                rows.append({"id": identity + endpoint["path"], "status": "failed",
                             "error_type": type(exc).__name__})
    for spec in manifest.get("wyoming", []):
        if not spec.get("enabled", True):
            rows.append({"id": spec["id"], "status": "disabled"})
            continue
        try:
            wyoming(spec)
            rows.append({"id": spec["id"], "status": "passed"})
        except Exception as exc:
            rows.append({"id": spec["id"], "status": "failed", "error_type": type(exc).__name__})
    before = resources(manifest.get("process_patterns", []))
    if any(count == 0 for count in before["pattern_counts"].values()):
        rows.append({"id": "required-processes", "status": "failed", "error_type": "ProcessLookupError"})
    warmed = before
    if mode != "health" and not any(row["status"] == "failed" for row in rows):
        for spec in manifest.get("workloads", []):
            if not spec.get("enabled", True):
                continue
            try:
                for _ in range(warmup):
                    inference(services[spec["service"]], spec)
            except Exception as exc:
                rows.append({"id": spec["id"] + "-warmup", "status": "failed", "error_type": type(exc).__name__})
        if warmup:
            warmed = resources(manifest.get("process_patterns", []))
    if mode == "health":
        rows.extend({"id": spec["id"], "status": "not-run", "reason": "health mode"}
                    for spec in manifest.get("workloads", []) if spec.get("enabled", True))
    elif any(row["status"] == "failed" for row in rows):
        rows.extend({"id": spec["id"], "status": "not-run", "reason": "preflight failed"}
                    for spec in manifest.get("workloads", []) if spec.get("enabled", True))
    if mode != "health" and not any(row["status"] == "failed" for row in rows):
        for spec in manifest.get("workloads", []):
            if not spec.get("enabled", True):
                rows.append({"id": spec["id"], "status": "disabled"})
                continue
            if not allow_inference:
                raise ValueError("Inference requires --allow-inference; health mode never performs inference")
            values, extras = [], {}
            runs = manifest.get("soak_iterations", 100) if mode == "soak" else samples
            try:
                for _ in range(runs):
                    elapsed, extra = inference(services[spec["service"]], spec)
                    values.append(elapsed)
                    for key, value in extra.items():
                        extras.setdefault(key, []).append(value)
                raw[spec["id"] + ".latency_ms"] = values
                metrics[spec["id"] + ".latency_ms"] = stats(values)
                for key, values in extras.items():
                    if len(values) != runs:
                        raise ValueError("A stage measurement disappeared during the run")
                    metrics[spec["id"] + "." + key] = stats(values)
                    raw[spec["id"] + "." + key] = values
                rows.append({"id": spec["id"], "status": "passed"})
            except Exception as exc:
                rows.append({"id": spec["id"], "status": "failed", "error_type": type(exc).__name__})
    after = resources(manifest.get("process_patterns", []))
    if before["processes"]:
        metrics["services.rss_kib"] = {"value": after["rss_kib"]}
        metrics["services.rss_growth_kib"] = {"value": max(0, after["rss_kib"] - warmed["rss_kib"])}
        if sorted(p["pid"] for p in before["processes"]) != sorted(p["pid"] for p in after["processes"]):
            rows.append({"id": "process-restart", "status": "failed"})
        if mode == "soak" and after["rss_kib"] - warmed["rss_kib"] > manifest.get("max_rss_growth_kib", 16384):
            rows.append({"id": "memory-growth", "status": "failed"})
    fingerprint, configuration = initial_files["source"], initial_files["configuration"]
    for category, key in (("source", "source_files"), ("configuration", "configuration_files")):
        try:
            file_root = manifest.get("configuration_root", manifest["source_root"]) if category == "configuration" else manifest["source_root"]
            if hashes(file_root, manifest.get(key, [])) != initial_files[category]:
                rows.append({"id": category + "-changed-during-run", "status": "failed"})
        except (OSError, ValueError):
            rows.append({"id": category + "-missing-at-end", "status": "failed"})
    workload = {key: value for key, value in manifest.items() if key not in
                ("source_root", "configuration_root", "source_files", "configuration_files", "regression_gates")}
    workload_details = []
    for spec in manifest.get("workloads", []):
        detail = {key: spec[key] for key in ("id", "enabled", "service", "kind", "path", "audio_sha256", "input_seconds", "max_cer") if key in spec}
        for key in ("text", "expected_text"):
            if key in spec:
                detail[key + "_sha256"] = hashlib.sha256(spec[key].encode("utf-8")).hexdigest()
        workload_details.append(detail)
    return {"schema_version": 1, "status": "passed" if rows and not any(r["status"] == "failed" for r in rows) else "failed",
            "context": {"kind": "device-" + mode, "environment": {"device_id": platform.node(),
                         "architecture": platform.machine(), "python": platform.python_version(),
                         "kernel": platform.release()}, "workload_sha256": digest(workload),
                         "configuration_sha256": digest({"files": configuration, "active_stages": {
                             row["id"]: row["observed"]["stages"] for row in rows
                             if row.get("observed", {}).get("stages")}}),
                         "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
            "source_files": fingerprint, "configuration_files": configuration,
            "checks": rows, "metrics": metrics, "samples": raw, "workloads": workload_details,
            "resources_before": before, "resources_warmed": warmed, "resources_after": after,
            "correctness": {"contracts": [{"id": r["id"], "status": r["status"]} for r in rows]},
            "notes": ["Health is liveness/contract evidence, not a model accuracy or physical I/O acceptance test.",
                      "Inference uses deployed services; cloud model/provider drift can affect results."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("health", "benchmark", "soak"), default="health")
    parser.add_argument("--allow-inference", action="store_true")
    parser.add_argument("--deadline", type=int, default=300)
    args = parser.parse_args()
    if not 1 <= args.deadline <= 3600:
        parser.error("Deadline must be between 1 and 3600 seconds")
    if hasattr(signal, "SIGALRM"):
        def expired(signum, frame):
            raise ProbeDeadline("Device probe deadline expired")
        signal.signal(signal.SIGALRM, expired)
        signal.alarm(args.deadline)
    try:
        manifest = json.load(sys.stdin)
        result = probe(manifest, args.mode, args.allow_inference)
    except (Exception, ProbeDeadline) as exc:
        result = {"schema_version": 1, "status": "failed", "error_type": type(exc).__name__,
                  "message": str(exc) if isinstance(exc, (ValueError, FileNotFoundError, ProbeDeadline)) else "Probe failed; inspect checks and device availability"}
    finally:
        if hasattr(signal, "SIGALRM"):
            signal.alarm(0)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
