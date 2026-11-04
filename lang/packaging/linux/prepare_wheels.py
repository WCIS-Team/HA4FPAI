#!/usr/bin/env python3
"""Download and statically audit pinned CPython 3.8 Linux wheels.

Requires only Python >=3.8 and its existing pip. Never installs packages or
imports a downloaded runtime. Invoke with --architecture arm64|amd64 --output DIR.
"""
import argparse
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import zipfile

from pip._internal.models.target_python import TargetPython
from pip._vendor.packaging.requirements import Requirement
from pip._vendor.packaging.specifiers import SpecifierSet
from pip._vendor.packaging.utils import canonicalize_name, parse_wheel_filename
from pip._vendor.packaging.version import Version

ARCHITECTURES = {"arm64": "aarch64", "amd64": "x86_64"}
ROOTS = [
    "edge-tts==7.3.1", "wyoming==1.10.2", "sherpa-onnx==2.0.1",
    "sherpa-onnx-core==2.0.1", "huggingface-hub==0.30.2", "numpy==1.24.4",
]
PIN = re.compile(r"([A-Za-z0-9_.-]+)==([^\s]+)\s+--hash=sha256:([0-9a-f]{64})")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_lock(path):
    """Accept only exact pins with one SHA256; reject URLs and pip directives."""
    result = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#") or line == "--only-binary=:all:":
            continue
        match = PIN.fullmatch(line)
        if not match:
            raise ValueError("Invalid lock line {}:{}".format(path.name, number))
        name, version, digest = match.groups()
        name = canonicalize_name(name)
        Version(version)
        if name in result:
            raise ValueError("Duplicate lock distribution: " + name)
        result[name] = {"version": version, "sha256": digest}
    if not result:
        raise ValueError("Lock contains no distributions")
    return result


def audit(output, architecture, expected):
    """Check all wheels against the lock, ZIP CRCs and target-only markers."""
    machine = ARCHITECTURES[architecture]
    environment = dict(
        implementation_name="cpython", implementation_version="3.8.10",
        os_name="posix", platform_machine=machine,
        platform_python_implementation="CPython", platform_release="",
        platform_system="Linux", platform_version="", python_full_version="3.8.10",
        python_version="3.8", sys_platform="linux", extra="",
    )
    tags = set(TargetPython(
        platforms=["manylinux2014_" + machine], py_version_info=(3, 8, 10),
        abis=["cp38"], implementation="cp",
    ).get_sorted_tags())
    rows, errors = {}, []
    for path in sorted(output.glob("*.whl")):
        try:
            wheel_name, wheel_version, _, wheel_tags = parse_wheel_filename(path.name)
            with zipfile.ZipFile(path) as archive:
                names = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
                if len(names) != 1:
                    raise ValueError("expected exactly one wheel METADATA")
                metadata = BytesParser().parsebytes(archive.read(names[0]))
                bad = archive.testzip()
                if bad:
                    errors.append("ZIP CRC failure: {}: {}".format(path.name, bad))
            name = canonicalize_name(metadata["Name"])
            version = metadata["Version"]
            if name != canonicalize_name(wheel_name) or Version(version) != wheel_version:
                errors.append("Filename/METADATA identity mismatch: " + path.name)
            if name in rows:
                errors.append("Duplicate distribution: " + name)
            requires_python = metadata.get("Requires-Python", "")
            if requires_python and not SpecifierSet(requires_python).contains("3.8.10"):
                errors.append("Unsupported Requires-Python: {} {}".format(name, requires_python))
            if not wheel_tags.intersection(tags):
                errors.append("Unsupported target wheel tag: " + path.name)
            row = dict(
                name=name, version=version, filename=path.name,
                bytes=path.stat().st_size, sha256=sha256(path),
                requires_python=requires_python,
                requires_dist=metadata.get_all("Requires-Dist", []),
            )
            pin = expected.get(name)
            if pin is None:
                errors.append("Unpinned distribution in wheelhouse: " + name)
            elif row["version"] != pin["version"] or row["sha256"] != pin["sha256"]:
                errors.append("Lock version/SHA256 mismatch: " + path.name)
            rows[name] = row
        except (ValueError, TypeError, KeyError, OSError, zipfile.BadZipFile) as exc:
            errors.append("Cannot audit {}: {}".format(path.name, exc))

    missing = [name + "==" + pin["version"] for name, pin in expected.items() if name not in rows]
    # Start at every locked distribution as well as the product roots, so even
    # a platform-conditional surplus wheel cannot hide an incomplete dependency.
    pending = [Requirement(name + "==" + pin["version"]) for name, pin in expected.items()]
    pending.extend(Requirement(text) for text in ROOTS)
    extras, checked, active, edges = {}, set(), [], set()
    while pending:
        requirement = pending.pop()
        name = canonicalize_name(requirement.name)
        if name not in rows:
            if str(requirement) not in missing:
                missing.append(str(requirement))
            continue
        row = rows[name]
        if requirement.url:
            errors.append("Direct-URL dependency is not supported: " + str(requirement))
        if not requirement.specifier.contains(row["version"]):
            errors.append("Unsatisfied: {}, found {}".format(requirement, row["version"]))
        requested = extras.setdefault(name, set())
        requested.update(requirement.extras)
        key = (name, tuple(sorted(requested)))
        if key in checked:
            continue
        checked.add(key)
        for text in row["requires_dist"]:
            try:
                dependency = Requirement(text)
                applies = dependency.marker is None or any(
                    dependency.marker.evaluate(dict(environment, extra=extra))
                    for extra in [""] + sorted(requested)
                )
            except (ValueError, KeyError) as exc:
                errors.append("Invalid dependency in {}: {}: {}".format(name, text, exc))
                continue
            if applies:
                edge = (name, str(dependency))
                if edge not in edges:
                    edges.add(edge)
                    active.append(dict(parent=name, requirement=str(dependency)))
                pending.append(dependency)
    return dict(
        target=environment, roots=ROOTS, complete=not errors and not missing,
        wheel_count=len(rows), total_bytes=sum(row["bytes"] for row in rows.values()),
        errors=errors, missing=missing, active_dependencies=active,
        wheels=sorted(rows.values(), key=lambda row: row["name"]),
    )


def write_report(output, report):
    (output / "metadata-audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    if report["complete"]:
        lines = [
            "# Ubuntu 20.04 {} / CPython 3.8; wheel METADATA audited.".format(
                report["target"]["platform_machine"]),
            "# Bundled NumPy is for the private venv, not system Python.",
            "--only-binary=:all:", "",
        ]
        lines.extend("{name}=={version} --hash=sha256:{sha256}".format(**row)
                     for row in report["wheels"])
        (output / "requirements.lock").write_text("\n".join(lines) + "\n", encoding="utf-8")
        (output / "wheelhouse.SHA256").write_text(
            "".join("{sha256}  {filename}\n".format(**row) for row in report["wheels"]),
            encoding="utf-8",
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--architecture", choices=sorted(ARCHITECTURES), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    lock = Path(__file__).resolve().with_name("requirements-{}.lock".format(args.architecture))
    expected = read_lock(lock)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    # Keep pip's temporary downloads in the caller's selected artifact directory.
    environment = dict(os.environ, TEMP=str(output), TMP=str(output), TMPDIR=str(output))
    command = [
        sys.executable, "-m", "pip", "--isolated", "download",
        "--index-url", "https://pypi.org/simple", "--disable-pip-version-check",
        "--platform", "manylinux2014_" + ARCHITECTURES[args.architecture],
        "--python-version", "38", "--implementation", "cp", "--abi", "cp38",
        "--only-binary=:all:", "--no-deps", "--require-hashes", "--no-cache-dir",
        "--retries", "2", "--timeout", "30", "--progress-bar", "off",
        "--dest", str(output), "--requirement", str(lock),
    ]
    # Reuse a complete existing output without touching the network. Hashes and
    # all static checks still run, including extra or duplicate wheel rejection.
    report = audit(output, args.architecture, expected)
    if not report["complete"]:
        result = subprocess.run(command, env=environment, check=False)
        report = audit(output, args.architecture, expected)
        if result.returncode:
            report["errors"].append("pip download exited with status {}".format(result.returncode))
            report["complete"] = False
    write_report(output, report)
    print(json.dumps({key: report[key] for key in (
        "complete", "wheel_count", "total_bytes", "errors", "missing",
    )}, ensure_ascii=False, indent=2))
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    sys.exit(main())
