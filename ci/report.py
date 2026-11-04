#!/usr/bin/env python3
"""Compare performance JSON and emit JSON, Markdown and a standalone HTML report."""
import argparse
import html
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CI, config, distribution, file_digest, load_json, write_json


def ensure_separate_output(prefix, inputs):
    protected = {Path(path).resolve() for path in inputs if path is not None}
    outputs = {Path(str(prefix) + suffix).resolve() for suffix in (".json", ".json.tmp", ".md", ".html")}
    if protected.intersection(outputs):
        raise ValueError("Report output would overwrite an input file; choose a different prefix")


def validate_record(record):
    if record.get("schema_version") != 1 or record.get("status") != "passed":
        raise ValueError("Measurement did not pass correctness checks")
    if not record.get("correctness") or not record.get("metrics"):
        raise ValueError("Missing correctness evidence or measurements")
    required = {"kind", "environment", "workload_sha256", "harness_sha256"}
    if not required <= set(record.get("context", {})):
        raise ValueError("Missing reproducibility metadata")
    for name, metric in record["metrics"].items():
        if not isinstance(metric, dict) or not {"p50", "p95", "value"}.intersection(metric):
            raise ValueError("Invalid measurement: " + name)
        for key in ("p50", "p95", "value"):
            if key in metric:
                value = metric[key]
                if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                    raise ValueError("Invalid numeric measurement: " + name)
        if "p95" in metric or "p50" in metric:
            values = record.get("samples", {}).get(name)
            if (type(metric.get("n")) is not int or metric["n"] < 3 or
                    not isinstance(values, list) or len(values) != metric["n"]):
                raise ValueError("Missing or insufficient raw samples: " + name)
            actual = distribution(values)
            if any(key in metric and not math.isclose(metric[key], actual[key], rel_tol=1e-9, abs_tol=1e-9)
                   for key in ("p50", "p95")):
                raise ValueError("Summary does not match raw samples: " + name)


def compare(candidate, baseline, gates, allow_config_change=False):
    try:
        if not isinstance(gates, dict):
            raise ValueError("Regression gates must be an object")
        for gate in gates.values():
            if (gate.get("direction") not in ("lower", "higher") or
                    any(type(gate.get(key)) not in (int, float) or not math.isfinite(gate[key]) or gate[key] < 0
                        for key in ("relative", "absolute"))):
                raise ValueError("Invalid regression gate")
        validate_record(candidate)
        if baseline is not None:
            validate_record(baseline)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return {"status": "invalid", "reason": str(exc), "rows": []}
    if baseline is None:
        return {"status": "no-baseline", "reason": "Measured candidate; no speedup claim without a baseline", "rows": []}
    left, right = dict(baseline.get("context", {})), dict(candidate.get("context", {}))
    required = {"kind", "environment", "workload_sha256", "harness_sha256"}
    if not required <= set(left) or not required <= set(right):
        return {"status": "invalid", "reason": "Missing reproducibility metadata", "rows": []}
    changes = []
    if allow_config_change:
        before, after = left.pop("configuration_sha256", None), right.pop("configuration_sha256", None)
        if before != after:
            changes.append("Deployed configuration changed; explicitly accepted for this experiment")
    if left != right:
        keys = sorted(key for key in set(left) | set(right) if left.get(key) != right.get(key))
        return {"status": "incomparable", "reason": "Conditions differ: " + ", ".join(keys), "rows": []}
    if candidate.get("correctness") != baseline.get("correctness"):
        return {"status": "invalid", "reason": "Deterministic correctness fingerprint changed", "rows": []}
    old, new = baseline.get("metrics", {}), candidate.get("metrics", {})
    if not new or set(new) != set(old):
        return {"status": "invalid", "reason": "Missing or additional metrics; re-establish a complete baseline", "rows": []}
    rows, applied, errors = [], set(), []
    for metric in sorted(new):
        for stat in ("p50", "p95", "value"):
            if stat not in new[metric] and stat not in old[metric]:
                continue
            before, after = old[metric].get(stat), new[metric].get(stat)
            if any(type(x) not in (int, float) or not math.isfinite(x) or x < 0 for x in (before, after)):
                errors.append(metric + "." + stat)
                continue
            selector = metric.rsplit(".", 1)[-1] + "." + stat
            gate = gates.get(metric + "." + stat) or gates.get(selector)
            delta = after - before
            percent = 100.0 * delta / before if before else None
            regression = False
            if gate:
                applied.add(metric + "." + stat if metric + "." + stat in gates else selector)
                change = delta if gate["direction"] == "lower" else -delta
                regression = change > max(abs(before) * gate["relative"], gate["absolute"])
                if stat != "value" and (new[metric].get("n", 0) < 3 or old[metric].get("n", 0) < 3):
                    errors.append(metric + " has fewer than 3 samples")
            rows.append({"metric": metric + "." + stat, "baseline": before, "candidate": after,
                         "delta": delta, "percent": percent, "regression": regression,
                         "gated": bool(gate)})
    missing = set(gates) - applied
    if missing:
        errors.append("Configured gates matched no measurements: " + ", ".join(sorted(missing)))
    status = "invalid" if errors else ("regression" if any(row["regression"] for row in rows) else "passed")
    if status == "regression" and candidate.get("source_files") and candidate["source_files"] == baseline.get("source_files"):
        changes.append("Source fingerprints are unchanged; assess runtime/load variation before attributing the measured regression to code")
    return {"status": status, "reason": "; ".join(errors + changes) or "Comparable measurements",
            "rows": rows}


def render(result, candidate):
    lines = ["# HA4FPAI performance", "", "Result: **%s**" % result["status"], "", result["reason"], "",
             "Source: `%s`; dirty: `%s`" % (candidate.get("source", {}).get("commit"),
                                             candidate.get("source", {}).get("dirty")), "",
             "| Metric | Baseline | Candidate | Change | Gate |", "|---|---:|---:|---:|---|"]
    rows = result["rows"]
    if not rows:
        for metric, values in sorted(candidate.get("metrics", {}).items()):
            for stat in ("p50", "p95", "value"):
                if (isinstance(values, dict) and type(values.get(stat)) in (int, float)
                        and math.isfinite(values[stat])):
                    lines.append("| %s.%s | — | %.4f | — | %s |" %
                                 (metric, stat, values[stat], "no baseline" if result["status"] == "no-baseline" else "not comparable"))
    for row in rows:
        change = "%+.2f%%" % row["percent"] if row["percent"] is not None else "new (zero baseline)"
        gate = "REGRESSION" if row["regression"] else ("pass" if row["gated"] else "observe")
        lines.append("| %s | %.4f | %.4f | %s | %s |" %
                     (row["metric"], row["baseline"], row["candidate"], change, gate))
    lines.extend(["", "Negative change means faster for latency; verify the metric's direction for other units.",
                  "A gate fails only when the regression exceeds both its relative and absolute tolerances.",
                  "All raw samples, conditions, source hashes and correctness checks remain in the JSON artifact."])
    if result.get("experiment"):
        details = result["experiment"]
        lines.extend(["", "## Parameter experiment", "", details["note"], "",
                      "| Condition/output | Baseline | Candidate |", "|---|---|---|"])
        for key in ("parameters", "chunks", "frames", "audio_rate"):
            lines.append("| %s | %s | %s |" % (key, details["baseline"][key], details["candidate"][key]))
    markdown = "\n".join(lines) + "\n"
    bars = []
    for row in rows:
        scale = max(row["baseline"], row["candidate"], 0.000001)
        color = "#b91c1c" if row["regression"] else "#0f766e"
        bars.append('<div class="metric"><b>%s</b><div>baseline <i style="width:%.1f%%;background:#94a3b8"></i> %.4f</div>'
                    '<div>candidate <i style="width:%.1f%%;background:%s"></i> %.4f</div></div>' %
                    (html.escape(row["metric"]), row["baseline"] / scale * 60, row["baseline"],
                     row["candidate"] / scale * 60, color, row["candidate"]))
    document = ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
                '<title>HA4FPAI performance</title><style>body{font:16px system-ui;max-width:1080px;margin:40px auto;padding:0 24px;color:#172033}'
                'pre{white-space:pre-wrap;background:#f1f5f9;padding:20px;border-radius:8px}.metric{margin:24px 0}.metric div{display:flex;align-items:center;gap:12px;margin:6px 0}'
                'i{display:inline-block;height:18px;border-radius:3px}h1{font-size:28px}</style><h1>HA4FPAI performance: %s</h1><p>%s</p>%s<pre>%s</pre></html>' %
                (html.escape(result["status"]), html.escape(result["reason"]), "".join(bars), html.escape(markdown)))
    return markdown, document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--config", type=Path, default=CI / "config.json")
    parser.add_argument("--gates", type=Path, help="Device manifest, whose regression_gates replace host gates")
    parser.add_argument("--allow-config-change", action="store_true")
    parser.add_argument("--output", type=Path, required=True, help="Report filename prefix")
    options = parser.parse_args()
    try:
        ensure_separate_output(options.output, (options.candidate, options.baseline, options.config, options.gates))
    except ValueError as exc:
        parser.error(str(exc))
    candidate = load_json(options.candidate)
    gates = (load_json(options.gates)["regression_gates"] if options.gates else
             config(options.config)["benchmark"]["gates"])
    baseline = load_json(options.baseline) if options.baseline else None
    result = compare(candidate, baseline, gates, options.allow_config_change)
    result["gates"] = gates
    result["comparison_tool_sha256"] = file_digest(CI / "report.py")
    result["conditions"] = candidate.get("context", {})
    markdown, document = render(result, candidate)
    write_json(str(options.output) + ".json", result)
    for suffix, text in ((".md", markdown), (".html", document)):
        with Path(str(options.output) + suffix).open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
    print(markdown)
    return 1 if result["status"] == "regression" else (2 if result["status"] in ("invalid", "incomparable") else 0)


if __name__ == "__main__":
    raise SystemExit(main())
