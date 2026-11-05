#!/usr/bin/env python3
"""Compare two declared parameter variants measured in the same benchmark run."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CI, config, digest, load_json, write_json
from report import compare, ensure_separate_output, render, validate_record


def select(record, variant):
    validate_record(record)
    parameters = record["parameters"][variant]
    observed = record["correctness"][variant]
    if "".join("".join(observed["chunks"]).split()) != "".join(observed["text"].split()):
        raise ValueError("Variant lost text during chunking")
    result = deepcopy(record)
    result["context"]["kind"] = "host-mock-parameter-experiment"
    result["context"]["configuration_sha256"] = digest(parameters)
    result["correctness"] = {"text": observed["text"], "audio_rate": observed["audio_rate"]}
    prefix = variant + "."
    for key in ("metrics", "samples"):
        result[key] = {name[len(prefix):]: value for name, value in record[key].items() if name.startswith(prefix)}
    return result, {"parameters": parameters, "chunks": len(observed["chunks"]), "frames": observed["frames"],
                    "audio_rate": observed["audio_rate"], "text": observed["text"]}


def experiment(record, baseline_variant, candidate_variant, gates):
    if baseline_variant == candidate_variant:
        raise ValueError("Select two distinct variants")
    before, old = select(record, baseline_variant)
    after, new = select(record, candidate_variant)
    if old["audio_rate"] != new["audio_rate"]:
        result = {"status": "incomparable", "reason": "Parameter experiment requires the same output sample rate", "rows": []}
    else:
        result = compare(after, before, gates, allow_config_change=True)
        result["reason"] += "; parameter tradeoff with identical input and source; inspect chunk count and audio duration"
    result["experiment"] = {"baseline": old, "candidate": new,
                             "note": "Mock TTS duration depends on chunking. This comparison shows parameter cost and output size, not speech quality."}
    return result, after


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--measurement", type=Path, required=True)
    parser.add_argument("--baseline-variant", required=True)
    parser.add_argument("--candidate-variant", required=True)
    parser.add_argument("--config", type=Path, default=CI / "config.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        ensure_separate_output(args.output, (args.measurement, args.config))
    except ValueError as exc:
        parser.error(str(exc))
    record = load_json(args.measurement)
    gates = config(args.config)["benchmark"]["gates"]
    try:
        result, candidate = experiment(record, args.baseline_variant, args.candidate_variant, gates)
    except (ValueError, KeyError, TypeError) as exc:
        result, candidate = {"status": "invalid", "reason": str(exc), "rows": []}, record
    result["gates"] = gates
    markdown, document = render(result, candidate)
    write_json(str(args.output) + ".json", result)
    for suffix, text in ((".md", markdown), (".html", document)):
        with Path(str(args.output) + suffix).open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
    print(markdown)
    return 1 if result["status"] == "regression" else (2 if result["status"] in ("invalid", "incomparable") else 0)


if __name__ == "__main__":
    raise SystemExit(main())
