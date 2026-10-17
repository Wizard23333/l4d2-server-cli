"""Build an offline-installable Linux x86_64 directory bundle. Never publish."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import runpy
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[2]
RELEASE = runpy.run_path(str(ROOT / "scripts/packaging/release_metadata.py"))


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_inventory(root):
    # Never collect reports, game files, caches or arbitrary worktree contents.
    paths = [p for p in (root / "src/l4d2_cli").rglob("*") if p.is_file()
             and "__pycache__" not in p.parts and p.suffix not in {".pyc", ".pyo"}]
    paths.extend(p for p in (root / "scripts/packaging").iterdir() if p.is_file())
    return {p.relative_to(root).as_posix(): sha256(p) for p in sorted(paths)}



def runtime_libraries(entries, runtime):
    """Keep only libraries originating in the verified runtime, plus their links."""
    accepted = []
    for entry in entries:
        if entry[2] == "SYMLINK":
            continue
        try:
            Path(entry[1]).resolve().relative_to(runtime.resolve())
        except ValueError:
            continue
        accepted.append(entry)
    names = {entry[0] for entry in accepted}
    accepted.extend(entry for entry in entries if entry[2] == "SYMLINK" and entry[1] in names)
    return accepted


def validate_bundle(bundle):
    entries = []
    for path in sorted(bundle.rglob("*")):
        relative = path.relative_to(bundle)
        if set(relative.parts) & {"test-results", ".local-docs", ".git", ".workbuddy", "steamcmd", "serverfiles"}:
            raise ValueError("Private or game content in bundle: " + relative.as_posix())
        if path.is_symlink():
            target = Path(os.readlink(path))
            if target.is_absolute():
                raise ValueError("Absolute bundle symlink")
            try:
                path.resolve(strict=True).relative_to(bundle.resolve())
            except (ValueError, OSError):
                raise ValueError("Bundle symlink escapes or is broken") from None
        elif path.is_file():
            entries.append(path)
        elif not path.is_dir():
            raise ValueError("Special file in bundle")
    return entries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-archive", required=True, type=Path)
    parser.add_argument("--runtime-licenses", required=True, type=Path,
                        help="Unmodified LICENSE*.txt files from the pinned runtime source tag")
    parser.add_argument("--tag", help="Preview tag; must match both project version declarations")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--work", type=Path, default=ROOT / "build/native-linux")
    args = parser.parse_args()
    expected = json.loads((ROOT / "scripts/packaging/runtime.json").read_text())
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        parser.error("Build on Linux x86_64; cross-compilation from Windows is not supported.")
    if platform.python_version() != expected["python_version"]:
        parser.error("Use the pinned CPython runtime version.")
    if sha256(args.runtime_archive) != expected["sha256"]:
        parser.error("Runtime archive SHA256 does not match runtime.json.")
    tools = {}
    for line in (ROOT / "scripts/packaging/requirements-linux-build.txt").read_text().splitlines():
        if "==" in line:
            name, version = line.split("==", 1)
            version = version.split()[0]
            tools[name] = importlib.metadata.version(name)
            if tools[name] != version:
                parser.error("Build tool version differs: " + name)
    version = RELEASE["project_version"](ROOT)
    tag = args.tag or RELEASE["preview_tag"](version)
    RELEASE["validate_tag"](tag, ROOT)
    name = RELEASE["basename"](tag)
    output, work = args.output.resolve(), args.work.resolve()
    archive_path = output / (name + ".tar.gz")
    if work.exists() or archive_path.exists():
        parser.error("Work directory/archive already exists; select fresh paths.")
    required_licenses = ["LICENSE.cpython.txt", "LICENSE.openssl-3.txt", "LICENSE.zlib.txt",
                         "LICENSE.bzip2.txt", "LICENSE.expat.txt", "LICENSE.libffi.txt", "LICENSE.liblzma.txt"]
    for license_name in required_licenses:
        if not (args.runtime_licenses / license_name).is_file():
            parser.error("Runtime license missing: " + license_name)
    work.mkdir(parents=True)
    output.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ, PYINSTALLER_CONFIG_DIR=str(work / "cache"))
    subprocess.run([sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm",
                    "--distpath", str(work / "frozen"), "--workpath", str(work / "analysis"),
                    str(ROOT / "scripts/packaging/linux.spec")], cwd=str(ROOT), env=environment, check=True)
    bundle = work / "frozen/l4d2"
    if (bundle / "_internal/l4d2_cli/templates/l4d2-main.service.in").read_bytes() != (
            ROOT / "src/l4d2_cli/templates/l4d2-main.service.in").read_bytes():
        raise ValueError("Service template missing or changed in bundle")
    for public_name in ["LICENSE", "THIRD_PARTY_NOTICES.md"]:
        shutil.copyfile(ROOT / public_name, bundle / public_name)
    guide = (ROOT / "scripts/packaging/INSTALL.md.in").read_text(encoding="utf-8")
    guide = guide.replace("@TAG@", tag).replace("@PYTHON_VERSION@", expected["python_version"])
    guide = guide.replace("@REPOSITORY@", os.environ.get("GITHUB_REPOSITORY", "Wizard23333/l4d2-server-cli"))
    (bundle / "INSTALL.md").write_text(guide, encoding="utf-8")
    license_dir = bundle / "licenses/python-runtime"
    license_dir.mkdir(parents=True)
    for path in sorted(args.runtime_licenses.glob("LICENSE*")):
        if path.is_file():
            shutil.copyfile(path, license_dir / path.name)
    python_license = Path(sys.base_prefix) / "lib/python3.12/LICENSE.txt"
    shutil.copyfile(python_license, license_dir / "CPython-LICENSE.txt")
    distribution = importlib.metadata.distribution("pyinstaller")
    license_record = next(p for p in distribution.files if str(p).endswith("licenses/COPYING.txt"))
    shutil.copyfile(distribution.locate_file(license_record), bundle / "licenses/PyInstaller-COPYING.txt")
    files = validate_bundle(bundle)
    glibc_versions = set()
    for path in files:
        with path.open("rb") as handle:
            elf = handle.read(4) == b"\x7fELF"
        if elf:
            result = subprocess.run(["objdump", "-T", str(path)], stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, check=True)
            glibc_versions.update(re.findall(r"GLIBC_([0-9]+\.[0-9]+(?:\.[0-9]+)?)", result.stdout))
    highest = max(glibc_versions, key=lambda item: tuple(map(int, item.split("."))))
    if tuple(map(int, highest.split("."))) > (2, 31):
        raise ValueError("Bundle exceeds glibc 2.31 baseline: " + highest)
    info = {"format": "l4d2-cli-linux-directory-v1", "version": version, "tag": tag, "architecture": "x86_64",
            "python": platform.python_version(), "runtime": expected, "build_tools": tools,
            "glibc_baseline": "2.31", "highest_required_glibc_symbol": highest,
            "source_files": source_inventory(ROOT),
            "license_sha256": {p.relative_to(bundle).as_posix(): sha256(p)
                               for p in sorted((bundle / "licenses").rglob("*")) if p.is_file()}}
    (bundle / "build-info.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    files = validate_bundle(bundle)
    (bundle / "FILES.sha256").write_text("".join(sha256(p) + "  " + p.relative_to(bundle).as_posix() + "\n"
                                             for p in files), encoding="utf-8")
    with tarfile.open(archive_path, "w:gz", dereference=False) as archive:
        archive.add(bundle, arcname=name)
    digest = sha256(archive_path)
    (output / (name + ".sha256")).write_text(digest + "  " + archive_path.name + "\n", encoding="utf-8")
    print(json.dumps({"archive": archive_path.name, "bytes": archive_path.stat().st_size,
                      "sha256": digest, "python": info["python"],
                      "highest_required_glibc_symbol": highest}))


if __name__ == "__main__":
    main()
