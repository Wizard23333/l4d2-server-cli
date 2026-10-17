"""Find systemd's process hierarchy on cgroup v1, v2 and hybrid hosts."""
from __future__ import annotations

import re
from pathlib import Path

from .common import CliError


def _unescape(value: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), value)


def mounts(path: Path = Path("/proc/self/mountinfo")) -> list:
    found = []
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return found
    for line in lines:
        before, separator, after = line.partition(" - ")
        fields, filesystem = before.split(), after.split()
        if not separator or len(fields) < 6 or len(filesystem) < 3:
            continue
        kind = filesystem[0]
        if kind == "cgroup2" or (kind == "cgroup" and "name=systemd" in filesystem[2].split(",")):
            found.append({"kind": kind, "root": _unescape(fields[3]), "mount": _unescape(fields[4])})
    return sorted(found, key=lambda entry: entry["kind"] == "cgroup2")


def mode(entries: list) -> str:
    kinds = {entry["kind"] for entry in entries}
    if len(kinds) == 2:
        return "hybrid"
    return "v2" if "cgroup2" in kinds else ("v1" if "cgroup" in kinds else "unavailable")


def service_pids(info: dict, entries: list | None = None) -> list:
    group = info.get("ControlGroup", "")
    if not group:
        if info.get("MainPID"):
            raise CliError("Running service has no inspectable ControlGroup.", "process_query_failed")
        return []
    if not group.startswith("/") or ".." in Path(group).parts:
        raise CliError("Invalid service cgroup path.", "process_query_failed")
    entries = mounts() if entries is None else entries
    if not entries:
        raise CliError("No readable systemd cgroup hierarchy was found.", "process_query_failed")
    errors = []
    matched = False
    found = set()
    for entry in entries:
        try:
            relative = Path(group).relative_to(entry["root"])
        except ValueError:
            continue
        matched = True
        root = Path(entry["mount"]) / relative
        if not root.exists():
            continue
        try:
            for path in [root / "cgroup.procs", *root.glob("**/cgroup.procs")]:
                if path.exists():
                    found.update(int(pid) for pid in path.read_text().split() if pid.isdigit())
            if not (root / "cgroup.procs").exists():
                raise OSError("cgroup.procs is unavailable")
        except OSError as exc:
            errors.append(str(exc))
            continue
    if errors or not matched or (info.get("MainPID") and info["MainPID"] not in found):
        raise CliError("Cannot inspect service process hierarchy.", "process_query_failed")
    return sorted(found)
