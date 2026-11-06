#!/usr/bin/env python3
"""Run dependency-free CI suites sequentially, with timeouts, logs and JUnit."""
import argparse
import ast
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CI, ROOT, config, environment, file_digest, git_metadata, write_json


def validate_sources(settings):
    paths = set()
    for relative in settings["source_roots"]:
        directory = ROOT / relative
        if not directory.is_dir():
            raise ValueError("Missing source root: " + relative)
        for path in directory.rglob("*.py"):
            if not set(path.relative_to(ROOT).parts).intersection(settings["exclude_parts"]):
                paths.add(path)
    if not paths:
        raise ValueError("No Python sources found")
    failures = []
    for path in sorted(paths):
        try:
            # Parse without generating __pycache__ and also enforce the board's grammar.
            text = path.read_text(encoding="utf-8-sig")
            ast.parse(text, filename=str(path), feature_version=settings["python_compatibility"][1])
        except (SyntaxError, UnicodeError) as exc:
            failures.append("%s: %s" % (path.relative_to(ROOT), exc))
    if failures:
        raise ValueError("\n".join(failures))
    print("Validated %d Python files against Python %s grammar" %
          (len(paths), ".".join(map(str, settings["python_compatibility"]))))
    return len(paths)


def execute(identity, args, output, timeout, unittest_suite=False):
    logfile = output / (identity + ".log")
    env = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1")
    scratch = output / "tmp"
    scratch.mkdir(parents=True, exist_ok=True)
    env.update(TMP=str(scratch), TEMP=str(scratch), TMPDIR=str(scratch))
    started = time.perf_counter()
    status, message = "failed", ""
    code = None
    kwargs = {"start_new_session": True} if os.name == "posix" else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    with logfile.open("w", encoding="utf-8", newline="\n") as stream:
        try:
            process = subprocess.Popen([sys.executable, "-I", "-B", "-X", "utf8"] + args,
                                       cwd=str(ROOT), env=env, stdout=stream, stderr=subprocess.STDOUT, **kwargs)
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                message = "Timed out after %s seconds; process tree terminated" % timeout
            finally:
                # The process owns a dedicated group; remove leaked grandchildren too.
                if os.name == "posix":
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                elif process.poll() is None:
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
                process.wait(timeout=15)
        except (OSError, subprocess.SubprocessError) as exc:
            message = "%s: %s" % (type(exc).__name__, exc)
    text = logfile.read_text(encoding="utf-8", errors="replace")
    count = None
    if unittest_suite:
        match = re.search(r"Ran (\d+) tests? in", text)
        count = int(match.group(1)) if match else 0
        if count == 0:
            message = "No unittest cases executed"
    if code == 0 and not message:
        status = "passed"
    row = {"id": identity, "status": status, "exit_code": code, "seconds": time.perf_counter() - started,
           "log": logfile.name, "message": message, "test_methods": count}
    print("%s %s (%.2fs)%s" % (status.upper(), identity, row["seconds"], ": " + message if message else ""), flush=True)
    if status == "failed":
        print(text[-12000:], flush=True)
    return row


def junit(rows, path):
    suite = ET.Element("testsuite", name="HA4FPAI", tests=str(len(rows)),
                       failures=str(sum(r["status"] == "failed" for r in rows)),
                       skipped=str(sum(r["status"] == "not-applicable" for r in rows)),
                       time="%.3f" % sum(r.get("seconds", 0) for r in rows))
    for row in rows:
        case = ET.SubElement(suite, "testcase", name=row["id"], classname="ci.suites", time="%.3f" % row.get("seconds", 0))
        if row["status"] == "failed":
            ET.SubElement(case, "failure", message=row.get("message") or "See " + row.get("log", "report.json"))
        elif row["status"] == "not-applicable":
            ET.SubElement(case, "skipped", message=row["message"])
        ET.SubElement(case, "system-out").text = row.get("log", row.get("message", ""))
    ET.ElementTree(suite).write(str(path), encoding="utf-8", xml_declaration=True)


def evidence_manifest(rows, output, profile, config_path):
    """H06 证据清单：每个套件的状态/退出码/耗时/日志 sha256/字节 + 配置输入摘要。"""
    items = []
    for row in rows:
        rec = {k: row.get(k) for k in
               ("id", "status", "exit_code", "seconds", "test_methods", "message")
               if k in row}
        log = row.get("log")
        if log:
            log_path = output / log
            if log_path.exists():
                rec["log"] = log
                rec["log_sha256"] = file_digest(log_path)
                rec["log_bytes"] = log_path.stat().st_size
        items.append(rec)
    return {"schema_version": 1, "profile": profile,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "inputs": {"config_sha256": file_digest(config_path)},
            "checks": items}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("validate", "quick", "full", "adapters", "soak", "no-npu"), default="quick")
    parser.add_argument("--config", type=Path, default=CI / "config.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--matrix", action="store_true", help="Emit the Actions matrix from configuration")
    args = parser.parse_args()
    settings = config(args.config)
    if args.matrix:
        print(json.dumps({"include": settings["test_matrix"]}, separators=(",", ":")))
        return 0
    if args.profile == "adapters" and sys.platform != "linux":
        parser.error("The adapter integration profile requires Linux and prepared external sources")
    if args.output is None:
        parser.error("--output is required (use an external scratch directory)")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    started = time.perf_counter()
    try:
        count = validate_sources(settings)
        rows.append({"id": "source-validation", "status": "passed", "files": count, "seconds": time.perf_counter() - started})
    except (ValueError, OSError) as exc:
        print(str(exc), flush=True)
        rows.append({"id": "source-validation", "status": "failed", "message": str(exc), "seconds": time.perf_counter() - started})
    timeout = settings["test_timeout_s"]
    if args.profile in ("quick", "full", "adapters", "no-npu"):
        for name in ("contracts", "infrastructure"):
            rows.append(execute(name, [str(CI / "tests" / ("test_" + name + ".py"))], output, timeout, True))
        for suite in settings["legacy_suites"]:
            if args.profile == "no-npu":
                # 明确不扫描/执行 npuserv 的运行档：只跑 quick 档里不含 npuserv 的套件
                if "quick" not in suite["profiles"]:
                    continue
                if "npuserv" in suite.get("path", "") or suite["id"].startswith("npu"):
                    continue
            elif args.profile not in suite["profiles"]:
                continue
            if "all" not in suite["platforms"] and sys.platform not in suite["platforms"]:
                rows.append({"id": suite["id"], "status": "not-applicable", "message": "Requires " + ", ".join(suite["platforms"])})
                continue
            rows.append(execute(suite["id"], [str(ROOT / suite["path"])], output, suite.get("timeout_s", timeout)))
    if args.profile in ("full", "adapters", "soak"):
        rows.append(execute("host-soak", [str(CI / "soak.py"), "--config", str(args.config),
                                           "--output", str(output / "soak.json")], output, timeout))
    if args.profile in ("full", "adapters"):
        rows.append(execute("calibration", [str(CI / "calibrate.py"), "--output", str(output / "calibration.json")], output, timeout))
    external = {}
    if args.profile == "adapters":
        for source in settings["adapter_sources"]:
            directory = ROOT / source["path"]
            try:
                external[source["id"]] = {"checkout": git_metadata(directory),
                                           "files": {name: file_digest(directory / name) for name in source["files"]}}
            except (ValueError, OSError) as exc:
                rows.append({"id": "source-" + source["id"], "status": "failed", "message": str(exc)})
    result = {"schema_version": 1, "status": "failed" if any(r["status"] == "failed" for r in rows) else "passed",
              "profile": args.profile, "environment": environment(), "source": git_metadata(ROOT),
              "external_sources": external, "checks": rows}
    manifest = evidence_manifest(rows, output, args.profile, args.config)
    write_json(output / "evidence.json", manifest)
    result["evidence"] = {"file": "evidence.json", "checks": len(rows),
                          "config_sha256": manifest["inputs"]["config_sha256"]}
    write_json(output / "results.json", result)
    junit(rows, output / "junit.xml")
    markdown = "# HA4FPAI %s: %s\n\n" % (args.profile, result["status"])
    markdown += "| Suite | Status | Seconds | Log |\n|---|---|---:|---|\n"
    markdown += "".join("| %s | %s | %.2f | %s |\n" % (r["id"], r["status"], r.get("seconds", 0), r.get("log", r.get("message", ""))) for r in rows)
    with (output / "summary.md").open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(markdown)
    print("CI %s: %s; %s" % (args.profile, result["status"], output), flush=True)
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
