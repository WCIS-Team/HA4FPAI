#!/usr/bin/env python3
"""Inspect the final deb/archive without installing or executing their contents."""
import argparse
import ast
import hashlib
import io
from pathlib import Path, PurePosixPath
import sys
import tarfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, file_digest, write_json
sys.path.insert(0, str(ROOT / "lang/server"))
from cfg import parse

SECRET_KEYS = {"api_key", "token", "secret", "password", "access_token", "client_secret", "authorization", "keyfile"}


def ar_members(blob):
    if not blob.startswith(b"!<arch>\n"):
        raise ValueError("Not a Debian ar archive")
    offset, result = 8, {}
    while offset < len(blob):
        header = blob[offset:offset + 60]
        if len(header) != 60 or header[58:60] != b"`\n":
            raise ValueError("Invalid ar header")
        name = header[:16].decode("ascii").strip().rstrip("/")
        size = int(header[48:58].decode("ascii").strip())
        offset += 60
        if size < 0 or offset + size > len(blob) or name in result:
            raise ValueError("Truncated or duplicate ar member")
        result[name] = blob[offset:offset + size]
        offset += size + size % 2
    if offset != len(blob) or set(result) != {"debian-binary", "control.tar.gz", "data.tar.gz"}:
        raise ValueError("Unexpected Debian archive members")
    if result["debian-binary"] != b"2.0\n":
        raise ValueError("Invalid Debian format version")
    return result


def tar_members(blob):
    result = {}
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as archive:
        for item in archive:
            path = PurePosixPath(item.name)
            if path.is_absolute() or ".." in path.parts or item.issym() or item.islnk():
                raise ValueError("Unsafe archive member")
            name = str(path)
            if name in result:
                raise ValueError("Duplicate tar member")
            if item.isdir():
                continue
            if not item.isfile() or item.size > 512 * 1024 * 1024:
                raise ValueError("Unexpected file type or size")
            result[name] = (archive.extractfile(item).read(), item.mode)
    return result


def check_defaults(value):
    for key, item in value.items():
        if isinstance(item, dict):
            check_defaults(item)
        elif key.lower() in SECRET_KEYS and item:
            raise ValueError("Default configuration includes nonempty credentials")
        elif isinstance(item, str) and len(item) > 2 and item[1:3] in (":/", ":\\"):
            raise ValueError("Default configuration includes a workstation path")


def inspect(package, archive, architecture):
    members = ar_members(package.read_bytes())
    control, data = tar_members(members["control.tar.gz"]), tar_members(members["data.tar.gz"])
    fields = dict(line.split(": ", 1) for line in control["control"][0].decode("utf-8").splitlines() if ": " in line)
    if fields.get("Package") != "lang-server" or fields.get("Architecture") != architecture:
        raise ValueError("Package name/architecture mismatch")
    required = {"usr/lib/lang-server/lang/server/app.py", "usr/lib/lang-server/lang/server/core.py",
                "usr/lib/lang-server/model_install.py", "usr/lib/lang-server/requirements.lock",
                "usr/lib/lang-server/wheelhouse.SHA256", "usr/lib/lang-server/default-config/online.yaml"}
    required |= {"lib/systemd/system/lang-server-" + unit + ".service" for unit in ("http", "voice", "loop", "model-download")}
    if not required <= set(data):
        raise ValueError("Required package files missing: " + ", ".join(sorted(required - set(data))))
    for name in ("preinst", "postinst", "prerm", "postrm"):
        text, mode = control[name]
        if not mode & 0o111 or b"\r" in text or not text.startswith(b"#!/bin/sh"):
            raise ValueError("Maintainer scripts need executable mode, shebang and LF: " + name)
    expected = dict((name, checksum) for checksum, name in
                    (line.split("  ", 1) for line in control["md5sums"][0].decode("utf-8").splitlines()))
    if set(expected) != set(data):
        raise ValueError("md5sums does not describe the complete data archive")
    wheels, sources = 0, 0
    for name, (blob, mode) in data.items():
        if hashlib.md5(blob).hexdigest() != expected[name]:
            raise ValueError("Package file checksum mismatch: " + name)
        parts = PurePosixPath(name).parts
        if set(parts).intersection((".git", ".env", "__pycache__", "archive")):
            raise ValueError("Unintended development/state file in package")
        if name.endswith(".py"):
            ast.parse(blob.decode("utf-8-sig"), filename=name, feature_version=8)
            sources += 1
        if name.startswith("usr/lib/lang-server/default-config/") and name.endswith(".yaml"):
            check_defaults(parse(blob.decode("utf-8")))
        if name.endswith(".whl"):
            wheels += 1
    if wheels == 0:
        raise ValueError("No offline dependency wheels included")
    release = tar_members(archive.read_bytes())
    packaged = [(name, blob) for name, (blob, mode) in release.items() if name.endswith(".deb")]
    if len(packaged) != 1 or hashlib.sha256(packaged[0][1]).hexdigest() != file_digest(package):
        raise ValueError("Install archive does not contain exactly the validated deb")
    scripts = [(blob, mode) for name, (blob, mode) in release.items() if name.endswith("/install.sh")]
    if len(scripts) != 1 or not scripts[0][1] & 0o111 or b"\r" in scripts[0][0]:
        raise ValueError("Invalid archive installer")
    return {"schema_version": 1, "status": "passed", "kind": "package-static", "architecture": architecture,
            "version": fields["Version"], "files": len(data), "python_files": sources, "wheels": wheels,
            "package_sha256": file_digest(package), "archive_sha256": file_digest(archive),
            "notes": ["Static validation does not replace install/upgrade/remove/purge in a disposable Ubuntu container."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--architecture", choices=("arm64", "amd64"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        packages, archives = list(args.directory.glob("*.deb")), list(args.directory.glob("*.tar.gz"))
        if len(packages) != 1 or len(archives) != 1:
            raise ValueError("Expected exactly one deb and one install archive")
        result = inspect(packages[0], archives[0], args.architecture)
    except Exception as exc:
        result = {"schema_version": 1, "status": "failed", "kind": "package-static", "error": "%s: %s" % (type(exc).__name__, exc)}
    write_json(args.output, result)
    print("Package static validation: %s; %s" % (result["status"], args.output))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
