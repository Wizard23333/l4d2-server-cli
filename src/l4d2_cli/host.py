"""Linux host adapter. Only the fixed registered service is controlled."""
from __future__ import annotations

import os
import platform
import shutil
import socket
from pathlib import Path

from .common import CliError, run
from . import cgroups
from .model import SERVICE, USER
from .platforms import detect_adapter, read_os_release


def platform_report() -> dict:
    import sys
    release = read_os_release()
    adapter = detect_adapter(release)
    libc, libc_version = platform.libc_ver()
    hierarchy = cgroups.mode(cgroups.mounts())
    requirements = {
        "linux": platform.system() == "Linux",
        "x86_64": platform.machine() in {"x86_64", "amd64"},
        "glibc": libc == "glibc",
        "python_3_8": sys.version_info >= (3, 8),
        "systemd": Path("/run/systemd/system").is_dir() and bool(shutil.which("systemctl")),
        "package_adapter": adapter is not None,
        "systemd_cgroup": hierarchy != "unavailable",
    }
    return {"supported": all(requirements.values()), "system": platform.system(), "architecture": platform.machine(),
            "distribution": release.get("ID", "unknown"), "version": release.get("VERSION_ID", "unknown"),
            "systemd": requirements["systemd"], "libc": libc, "libc_version": libc_version,
            "python": platform.python_version(), "cgroup": hierarchy,
            "package_family": adapter.family if adapter else None,
            "package_manager": adapter.manager if adapter else None,
            "unmet_requirements": [name for name, available in requirements.items() if not available]}


def require_platform() -> None:
    report = platform_report()
    if not report["supported"]:
        raise CliError("Linux host requirements are unmet: " + ", ".join(report["unmet_requirements"]) +
                       ". Require x86_64/glibc, Python 3.8+, running systemd and an APT, DNF/YUM, Zypper or Pacman adapter.",
                       "unsupported_platform", 4, {"platform": report})


def dependency_adapter():
    adapter = detect_adapter()
    if not adapter:
        raise CliError("No supported package manager/database for this distribution.", "unsupported_package_manager", 4)
    return adapter


def require_root() -> None:
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise CliError("Administrator permission required; use sudo on the target server.", "permission_denied", 3)


def missing_dependencies() -> list[str]:
    adapter = dependency_adapter()
    result = []
    for dependency in adapter.required_packages():
        status = run(adapter.query_command(dependency), check=False, timeout=5)
        if status.returncode or (adapter.query == "dpkg-query" and "install ok installed" not in status.stdout):
            result.append(dependency)
    return result


def install_dependencies(missing: list[str], timeout: float) -> None:
    if not missing:
        return
    adapter = dependency_adapter()
    commands = adapter.install_commands(missing)
    environment = os.environ.copy()
    environment["DEBIAN_FRONTEND"] = "noninteractive"
    # Fixed dependencies only, using existing repositories and signature policy.
    import time
    from .common import deadline_remaining
    deadline = time.monotonic() + timeout
    for command in commands:
        run(command, timeout=deadline_remaining(deadline), env=environment, stream=True)
    remaining = missing_dependencies()
    tools = [name for name in ("useradd", "runuser", "ldd", "ss", "nologin") if not shutil.which(name)]
    if remaining or tools:
        raise CliError("Dependency installation is incomplete; packages: " + ", ".join(remaining) +
                       "; commands: " + ", ".join(tools), "missing_dependencies")


def check_udp_available(bind: str, port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.bind((bind, port))
        except OSError as exc:
            raise CliError(f"UDP {bind}:{port} cannot be bound: {exc}", "port_conflict", 4) from exc


def udp_owner(port: int, main_pid: int, timeout: float = 5) -> bool | None:
    if not main_pid or not shutil.which("ss"):
        return None
    queried = run(["ss", "-H", "-lunp", f"sport = :{port}"], check=False, timeout=timeout)
    if queried.returncode or not queried.stdout.strip():
        return False
    import re
    owners = [int(value) for value in re.findall(r"pid=(\d+)", queried.stdout)]
    if not owners:
        return None
    for pid in owners:
        seen = set()
        while pid > 1 and pid not in seen:
            if pid == main_pid:
                return True
            seen.add(pid)
            try:
                record = Path(f"/proc/{pid}/stat").read_text()
                pid = int(record[record.rfind(")") + 2:].split()[1])
            except (OSError, ValueError, IndexError):
                break
    return False


def service_info(timeout: float = 10, *, service: str = SERVICE) -> dict:
    properties = ["LoadState", "ActiveState", "SubState", "MainPID", "InvocationID",
                  "ExecMainStartTimestamp", "NRestarts", "ExecMainStatus", "UnitFileState", "ControlGroup",
                  "User", "Group", "FragmentPath", "DropInPaths"]
    response = run(["systemctl", "show", service, "--no-pager", "--property=" + ",".join(properties)], check=False, timeout=timeout)
    if response.returncode:
        raise CliError(f"Cannot query {service}: {response.stdout}", "service_query_failed")
    result = dict(line.split("=", 1) for line in response.stdout.splitlines() if "=" in line)
    for key in ("MainPID", "NRestarts", "ExecMainStatus"):
        result[key] = int(result[key]) if result.get(key, "").isdigit() else None
    return result


def verify_service(info: dict) -> None:
    expected = str(Path("/etc/systemd/system") / SERVICE)
    if (info.get("FragmentPath") != expected or info.get("DropInPaths") or
            info.get("User") != USER or info.get("Group") != USER):
        raise CliError("Loaded service differs from the registered unit/user or has unmanaged drop-ins.", "service_conflict", 4)


def service_pids(info: dict) -> list[int]:
    return cgroups.service_pids(info)


def nologin_shell() -> str:
    for name in ("/usr/sbin/nologin", "/sbin/nologin", "/usr/bin/nologin", "/bin/nologin"):
        if Path(name).is_file() and os.access(name, os.X_OK):
            return name
    raise CliError("A nologin shell is required for the game account.", "missing_dependencies")


def systemctl(action: str, timeout: float = 60) -> None:
    if action not in {"start", "stop", "enable", "disable", "daemon-reload"}:
        raise CliError("Unregistered service action.", "invalid_service_action", 2)
    args = ["systemctl", action]
    if action != "daemon-reload":
        args.append(SERVICE)
    run(args, timeout=timeout)


def game_account(game_dir: Path) -> tuple[int, int, bool]:
    import pwd
    import grp
    try:
        account = pwd.getpwnam(USER)
    except KeyError:
        return -1, -1, False
    group = grp.getgrgid(account.pw_gid)
    if (account.pw_uid == 0 or account.pw_gid == 0 or group.gr_name != USER or group.gr_mem or
            account.pw_dir != str(game_dir) or
            Path(account.pw_shell).resolve() not in
            {Path(name).resolve() for name in ("/usr/sbin/nologin", "/sbin/nologin", "/usr/bin/nologin", "/bin/nologin")}):
        raise CliError(f"Existing user {USER} does not match this installation.", "user_conflict", 4)
    return account.pw_uid, account.pw_gid, True


def create_account(game_dir: Path, timeout: float = 60) -> tuple[int, int]:
    uid, gid, exists = game_account(game_dir)
    if not exists:
        run(["useradd", "--system", "--user-group", "--home-dir", str(game_dir),
             "--no-create-home", "--shell", nologin_shell(), USER], timeout=timeout)
        uid, gid, _ = game_account(game_dir)
    return uid, gid


def own_tree(root: Path, uid: int, gid: int) -> None:
    # Never follow symlinks while changing the already validated managed tree.
    os.chown(root, uid, gid, follow_symlinks=False)
    for directory, directories, files in os.walk(root, followlinks=False):
        for name in [*directories, *files]:
            os.chown(Path(directory) / name, uid, gid, follow_symlinks=False)


def as_game_user(args: list[str], home: Path) -> tuple[list[str], dict]:
    environment = os.environ.copy()
    environment["HOME"] = str(home)
    environment["USER"] = environment["LOGNAME"] = USER
    # runuser chooses the unprivileged account; no password is needed/printed.
    # runuser resets HOME to the account's passwd entry. Reapply the private
    # SteamCMD HOME after switching identity so cloned login caches are found.
    return ["runuser", "-u", USER, "--", "env", f"HOME={home}", *args], environment
