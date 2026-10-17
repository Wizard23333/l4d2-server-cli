"""Verify the CLI executable archive without executing its binaries."""
from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
from pathlib import Path, PurePosixPath
import re
import tarfile


def inspect_archive(path, root):
    issues = []
    def fail(rule):
        issues.append(rule)
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        names = [m.name for m in members]
        if len(set(names)) != len(names) or not names:
            return ["duplicate_or_empty_archive"]
        prefix = PurePosixPath(names[0]).parts[0]
        payloads = {}
        symlinks = []
        for entry in members:
            name = PurePosixPath(entry.name)
            if name.is_absolute() or ".." in name.parts or not name.parts or name.parts[0] != prefix:
                fail("unsafe_member_path")
                continue
            relative = PurePosixPath(*name.parts[1:])
            if set(relative.parts) & {"test-results", ".local-docs", ".workbuddy", ".git", "steamcmd", "serverfiles"}:
                fail("private_or_game_content")
            if entry.issym():
                target = posixpath.normpath(posixpath.join(str(name.parent), entry.linkname))
                if entry.linkname.startswith("/") or not target.startswith(prefix + "/") or target not in names:
                    fail("unsafe_member_symlink")
                symlinks.append(name)
            elif entry.isfile():
                if entry.size > 128 * 1024 * 1024:
                    fail("oversized_member")
                    continue
                payloads[relative.as_posix()] = archive.extractfile(entry).read()
            elif not entry.isdir():
                fail("unsupported_member_type")
        if any(link in PurePosixPath(name).parents for link in symlinks for name in names):
            fail("member_under_symlink")
        required = {"l4d2", "install.sh", "build-info.json", "FILES.sha256", "INSTALL.md", "LICENSE", "THIRD_PARTY_NOTICES.md",
                    "licenses/PyInstaller-COPYING.txt", "licenses/python-runtime/CPython-LICENSE.txt",
                    "_internal/l4d2_cli/templates/l4d2-main.service.in"}
        if not required.issubset(payloads):
            return issues + ["missing_bundle_files"]
        executable = payloads["l4d2"]
        if executable[:6] != b"\x7fELF\x02\x01" or int.from_bytes(executable[18:20], "little") != 62:
            fail("not_linux_x86_64_elf")
        if not next(m for m in members if m.name == prefix + "/l4d2").mode & 0o111:
            fail("entry_not_executable")
        if (payloads["install.sh"] != (root / "scripts/packaging/install.sh").read_bytes() or
                not next(m for m in members if m.name == prefix + "/install.sh").mode & 0o111):
            fail("installer_mismatch")
        info = json.loads(payloads["build-info.json"])
        namespace = {}
        exec((root / "src/l4d2_cli/__init__.py").read_text(encoding="utf-8"), namespace)
        if info.get("version") != namespace["__version__"] or info.get("architecture") != "x86_64":
            fail("bundle_metadata_mismatch")
        for name, digest in info.get("source_files", {}).items():
            source = PurePosixPath(name)
            if source.is_absolute() or ".." in source.parts or not (root / name).is_file():
                fail("invalid_source_record")
            elif hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
                fail("bundle_source_changed")
        for name, digest in info.get("license_sha256", {}).items():
            if name not in payloads or hashlib.sha256(payloads[name]).hexdigest() != digest:
                fail("license_mismatch")
        listed = {}
        for line in payloads["FILES.sha256"].decode("utf-8").splitlines():
            match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
            if not match or match[2] in listed:
                fail("invalid_file_manifest")
                continue
            listed[match[2]] = match[1]
        if set(listed) != set(payloads) - {"FILES.sha256"}:
            fail("file_manifest_inventory_mismatch")
        for name, digest in listed.items():
            if name not in payloads or hashlib.sha256(payloads[name]).hexdigest() != digest:
                fail("file_hash_mismatch")
        template = "_internal/l4d2_cli/templates/l4d2-main.service.in"
        if payloads[template] != (root / "src/l4d2_cli/templates/l4d2-main.service.in").read_bytes():
            fail("template_mismatch")
    return sorted(set(issues))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    issues = inspect_archive(args.archive, Path(__file__).resolve().parents[2])
    print(json.dumps({"passed": not issues, "findings": issues}))
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
