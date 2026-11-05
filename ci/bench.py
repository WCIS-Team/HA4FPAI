#!/usr/bin/env python3
"""Measure the real orchestrator with deterministic mock backends, without models."""
import argparse
import base64
import io
from pathlib import Path
import socket
import sys
import time
from unittest.mock import patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (CI, ROOT, config, digest, distribution, environment, file_digest,
                    git_metadata, rss_kib, source_fingerprint, write_json)


def run(options):
    settings = config(options.config)["benchmark"]
    source = Path(options.source).resolve(strict=True)
    fingerprint = source_fingerprint(source)
    sys.path.insert(0, str(source / "lang/server"))
    from cfg import Config
    from core import Chain
    from backends import audioio
    metrics, raw, checks = {}, {}, {}
    env = environment()
    context = {"kind": "host-mock", "environment": env,
               "workload_sha256": digest({key: value for key, value in settings.items() if key != "gates"}),
               "harness_sha256": digest({name: file_digest(CI / name) for name in
                                         ("bench.py", "common.py", "fixtures/mock.yaml")})}
    for variant in settings["variants"]:
        cfg = Config(str(CI / "fixtures"))
        cfg.data["tokenizer_profiles"]["tts_split"]["max_chars"] = str(variant["max_chars"])
        cfg.data["server"]["out_rate"] = str(variant["out_rate"])
        chain = Chain(cfg)
        wav = audioio.encode_wav(audioio.sine(settings["input_rate"], settings["input_seconds"]),
                                 settings["input_rate"])
        text = settings["text"]
        tokenizer = chain.stage("tokenizer")
        all_times = {"chain_ms": [], "split_ms": []}
        first_ms, check = None, None
        for iteration in range(1 + settings["warmup"] + settings["samples"]):
            start = time.perf_counter_ns()
            chunks = tokenizer.run(text)
            split_ms = (time.perf_counter_ns() - start) / 1e6
            if not chunks or "".join(chunks) != text or any(len(c) > variant["max_chars"] for c in chunks):
                raise AssertionError("Tokenizer correctness regressed")
            start = time.perf_counter_ns()
            result = chain.run_audio(wav)
            chain_ms = (time.perf_counter_ns() - start) / 1e6
            if iteration == 0:
                first_ms = chain_ms
            if result.get("code") != 0 or result.get("stages_error") or not result.get("asr_text"):
                raise AssertionError("Mock chain did not complete successfully")
            if not {"sar", "asr", "llm", "tts"} <= set(result["stages_ms"]):
                raise AssertionError("Benchmark bypassed an intended stage")
            audio = base64.b64decode(result["audio_b64"], validate=True)
            with wave.open(io.BytesIO(audio), "rb") as stream:
                if (stream.getframerate() != variant["out_rate"] or stream.getnframes() <= 0 or
                        not any(stream.readframes(stream.getnframes()))):
                    raise AssertionError("Invalid or silent benchmark audio")
                observed = {"text": result["text"], "chunks": result["chunks"],
                            "audio_rate": stream.getframerate(), "frames": stream.getnframes()}
                if check is not None and observed != check:
                    raise AssertionError("Benchmark correctness changed between iterations")
                check = observed
            if iteration > settings["warmup"]:
                all_times["split_ms"].append(split_ms)
                all_times["chain_ms"].append(chain_ms)
        prefix = variant["id"]
        for name, values in all_times.items():
            raw[prefix + "." + name] = values
            metrics[prefix + "." + name] = distribution(values)
        metrics[prefix + ".first_request_ms"] = {"value": first_ms}
        checks[prefix] = check
    if source_fingerprint(source) != fingerprint:
        raise AssertionError("Source changed during the benchmark")
    peak = rss_kib()
    if peak is not None:
        metrics["process.peak_rss_kib"] = {"value": peak}
    result = {"schema_version": 1, "status": "passed", "context": context,
              "source": git_metadata(source), "source_files": fingerprint,
              "metrics": metrics, "samples": raw, "correctness": checks,
              "workload": {key: value for key, value in settings.items() if key != "gates"},
              "parameters": {variant["id"]: variant for variant in settings["variants"]},
              "notes": ["Mock ASR/LLM and sine TTS measure orchestration, not recognition or model inference.",
                        "First request excludes process import and is separate from warmed samples.",
                        "Wall-clock milliseconds use perf_counter_ns; p95 uses nearest rank."]}
    write_json(options.output, result)
    print("Benchmark: %d metrics; output=%s" % (len(metrics), options.output))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT)
    parser.add_argument("--config", type=Path, default=CI / "config.json")
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    # A benchmark must never silently switch to a real backend or a cloud API.
    with patch.object(socket.socket, "connect", side_effect=AssertionError("Network disabled in mock benchmark")):
        try:
            run(options)
        except Exception as exc:
            write_json(options.output, {"schema_version": 1, "status": "failed",
                                        "error": "%s: %s" % (type(exc).__name__, exc)})
            raise


if __name__ == "__main__":
    main()
