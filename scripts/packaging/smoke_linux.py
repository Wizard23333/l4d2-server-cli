"""Unpack and run a native CLI bundle without Steam or system mutations."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

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
    args.unpack.mkdir(mode=0o700, parents=True)
    with tarfile.open(archive_path, "r:gz") as archive:
        archive.extractall(args.unpack, filter="data")
    cli = (args.unpack / name / "l4d2").resolve()
    environment = dict(os.environ)
    for key in ["PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"]:
        environment.pop(key, None)
    commands = [[], ["setup"], ["server", "install"], ["server", "configure"], ["server", "start"],
                ["server", "stop"], ["server", "restart"], ["server", "status"], ["server", "logs"],
                ["doctor"], ["network", "check"], ["download", "steamcmd"], ["download", "server"], ["auth", "login"],
                ["room", "invite"], ["room", "connect-check"], ["room", "update"], ["room", "health"],
                ["map", "share"], ["network", "probe"], ["access", "configure"], ["access", "status"],
                ["schedule", "add"], ["schedule", "run"], ["schedule", "list"], ["schedule", "remove"]]
    result = subprocess.run([str(cli), "--version"], cwd="/tmp", env=environment,
                            check=True, stdout=subprocess.PIPE, text=True, timeout=20)
    if result.stdout.strip() != "l4d2-server-cli " + version:
        raise ValueError("Native entrypoint version mismatch.")
    for command in commands:
        subprocess.run([str(cli)] + command + ["--help"], cwd="/tmp", env=environment,
                       check=True, stdout=subprocess.DEVNULL, timeout=20)
    # A frozen package must carry the actual Skill, not just its CLI parser.
    result = subprocess.run([str(cli), "skill", "show", "--json"], cwd="/tmp", env=environment,
                            check=True, stdout=subprocess.PIPE, text=True, encoding="utf-8", timeout=20)
    bundled_skill = json.loads(result.stdout)["data"]["content"]
    if bundled_skill != (ROOT / "src/l4d2_cli/skills/l4d2-manager/SKILL.md").read_text(encoding="utf-8"):
        raise ValueError("Frozen Skill resource differs from source.")
    skill_project = args.unpack.resolve() / "skill-project"
    skill_project.mkdir()
    for action in ("install", "status", "uninstall"):
        argv = [str(cli), "skill", action, "--agent", "codex", "--project", str(skill_project), "--json"]
        if action != "status":
            argv.append("--yes")
        subprocess.run(argv, cwd="/tmp", env=environment, check=True,
                       stdout=subprocess.DEVNULL, timeout=20)
    result = subprocess.run([str(cli), "server", "status", "--invalid-option", "--json"],
                            cwd="/tmp", env=environment, stdout=subprocess.PIPE, text=True, timeout=20)
    if result.returncode != 2 or json.loads(result.stdout)["error"]["code"] != "invalid_arguments":
        raise ValueError("Native argument/JSON error behavior changed.")

    # Exercise real frozen worker startup and TLS/HTTP module loading. Help-only
    # smoke checks cannot detect a CLI mistakenly spawning itself with -c.
    sys.path.insert(0, str(ROOT / "src"))
    from l4d2_cli.network_probe import WORKER_FLAG
    result = subprocess.run([str(cli), WORKER_FLAG, "dns", "localhost", "3"],
                            cwd="/tmp", env=environment, check=True, stdout=subprocess.PIPE, text=True, timeout=10)
    if json.loads(result.stdout).get("status") != "pass":
        raise ValueError("Frozen DNS worker failed.")

    class ProbeHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"\x1f\x8bprobe-fixture")

        def log_message(self, *args):
            pass

    worker_environment = dict(environment, no_proxy="127.0.0.1,localhost", NO_PROXY="127.0.0.1,localhost")
    server = HTTPServer(("127.0.0.1", 0), ProbeHandler)
    worker_thread = threading.Thread(target=server.serve_forever, daemon=True)
    worker_thread.start()
    try:
        url = "http://127.0.0.1:" + str(server.server_port) + "/archive"
        result = subprocess.run([str(cli), WORKER_FLAG, "archive", url, "3"],
                                cwd="/tmp", env=worker_environment, check=True,
                                stdout=subprocess.PIPE, text=True, timeout=10)
        if json.loads(result.stdout).get("status") != "pass":
            raise ValueError("Frozen HTTP/archive worker failed.")
    finally:
        server.shutdown()
        server.server_close()
        worker_thread.join(timeout=5)
    prefix = args.unpack.resolve() / "global-install"
    subprocess.run(["bash", str(cli.parent / "install.sh"), "--prefix", str(prefix)],
                   cwd="/tmp", env=environment, check=True, stdout=subprocess.DEVNULL, timeout=60)
    environment["PATH"] = str(prefix / "bin") + os.pathsep + environment["PATH"]
    result = subprocess.run(["l4d2", "--version"], cwd="/tmp", env=environment,
                            check=True, stdout=subprocess.PIPE, text=True, timeout=20)
    if result.stdout.strip() != "l4d2-server-cli " + version:
        raise ValueError("Globally registered CLI version mismatch.")
    subprocess.run(["l4d2", "--help"], cwd="/tmp", env=environment,
                   check=True, stdout=subprocess.DEVNULL, timeout=20)
    print("Native archive, SHA256, version, setup/help routes, JSON errors, DNS/HTTP workers and global installation passed.")


if __name__ == "__main__":
    main()
