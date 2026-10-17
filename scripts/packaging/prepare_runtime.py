"""Fetch verified build inputs and safely extract the pinned Linux runtime."""
from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
from pathlib import Path, PurePosixPath
import sys
import tarfile
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]


def download_verified(url, destination, expected, limit=128 * 1024 * 1024):
    if not url.startswith("https://") or len(expected) != 64:
        raise ValueError("HTTPS URL and SHA256 are required.")
    partial = destination.with_suffix(destination.suffix + ".part")
    for attempt in range(3):
        try:
            digest, size = hashlib.sha256(), 0
            with urlopen(Request(url, headers={"User-Agent": "l4d2-cli-release-builder"}), timeout=30) as response, partial.open("wb") as output:
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    size += len(block)
                    if size > limit:
                        raise ValueError("Build input exceeds size limit.")
                    digest.update(block)
                    output.write(block)
            if digest.hexdigest() != expected:
                raise ValueError("Build input checksum mismatch: " + destination.name)
            partial.replace(destination)
            return
        except (OSError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def validate_members(members):
    names = [member.name.rstrip("/") for member in members]
    if len(set(names)) != len(names):
        raise ValueError("Duplicate runtime archive member.")
    links = []
    total = 0
    for member in members:
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "python":
            raise ValueError("Runtime member escapes python directory.")
        if member.issym():
            target = posixpath.normpath(posixpath.join(str(path.parent), member.linkname))
            if member.linkname.startswith("/") or not target.startswith("python/") or target not in names:
                raise ValueError("Unsafe runtime symlink.")
            links.append(path)
        elif member.isfile():
            total += member.size
        elif not member.isdir():
            raise ValueError("Unsupported runtime member type.")
    if total > 1024 * 1024 * 1024:
        raise ValueError("Runtime archive exceeds unpacked size limit.")
    if any(link in PurePosixPath(name).parents for link in links for name in names):
        raise ValueError("Runtime member under symlink.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.version_info < (3, 12):
        parser.error("Prepare build inputs with Python 3.12+.")
    output = args.output.resolve()
    if output.exists():
        parser.error("Choose a fresh build-input directory.")
    output.mkdir(parents=True)
    runtime = json.loads((ROOT / "scripts/packaging/runtime.json").read_text())
    archive_path = output / runtime["filename"]
    download_verified(runtime["url"], archive_path, runtime["sha256"])
    with tarfile.open(archive_path, "r:gz") as archive:
        validate_members(archive.getmembers())
        archive.extractall(output / "runtime", filter="data")
    licenses = output / "licenses"
    licenses.mkdir()
    records = json.loads((ROOT / "scripts/packaging/runtime-licenses.json").read_text())
    for record in records:
        name = record["name"]
        if PurePosixPath(name).name != name or not name.startswith("LICENSE"):
            raise ValueError("Invalid runtime license name.")
        download_verified(record["url"], licenses / name, record["sha256"], limit=1024 * 1024)
    print("Verified pinned runtime and %d license files." % len(records))


if __name__ == "__main__":
    main()
