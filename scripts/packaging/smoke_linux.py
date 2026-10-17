"""Unpack and run a native CLI bundle without Steam or system mutations."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile

from check_linux import inspect_archive
from release_metadata import ROOT, basename, validate_tag


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--assets", required=True, type=Path)
    parser.add_argument("--unpack", required=True, type=Path)
    args = parser.parse_args()
    version = validate_tag(args.tag)
    name = basename(args.tag)
    archive_path = args.assets / (name + ".tar.gz")
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    if (args.assets / (name + ".sha256")).read_text().strip() != digest + "  " + name + ".tar.gz":
        raise ValueError("Executable checksum file mismatch.")
    issues = inspect_archive(archive_path, ROOT)
    if issues:
        raise ValueError("Native archive check failed: " + ", ".join(issues))
    if args.unpack.exists():
        raise ValueError("Choose a fresh unpack directory.")
    args.unpack.mkdir(parents=True)
    with tarfile.open(archive_path, "r:gz") as archive:
        archive.extractall(args.unpack, filter="data")
    cli = (args.unpack / name / "l4d2").resolve()
    environment = dict(os.environ)
    for key in ["PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"]:
        environment.pop(key, None)
    commands = [[], ["server", "install"], ["server", "configure"], ["server", "start"],
                ["server", "stop"], ["server", "restart"], ["server", "status"], ["server", "logs"],
                ["doctor"], ["network", "check"], ["download", "steamcmd"], ["download", "server"], ["auth", "login"]]
    result = subprocess.run([str(cli), "--version"], cwd="/tmp", env=environment,
                            check=True, stdout=subprocess.PIPE, text=True, timeout=20)
    if result.stdout.strip() != "l4d2-server-cli " + version:
        raise ValueError("Native entrypoint version mismatch.")
    for command in commands:
        subprocess.run([str(cli)] + command + ["--help"], cwd="/tmp", env=environment,
                       check=True, stdout=subprocess.DEVNULL, timeout=20)
    result = subprocess.run([str(cli), "server", "status", "--invalid-option", "--json"],
                            cwd="/tmp", env=environment, stdout=subprocess.PIPE, text=True, timeout=20)
    if result.returncode != 2 or json.loads(result.stdout)["error"]["code"] != "invalid_arguments":
        raise ValueError("Native argument/JSON error behavior changed.")
    print("Native archive, SHA256, version, 13 help routes and JSON error checks passed.")


if __name__ == "__main__":
    main()
