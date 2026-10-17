"""Distribution families and fixed dependency commands; never execute os-release."""
from __future__ import annotations

import re
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path

from .common import CliError


def read_os_release(path: Path = Path("/etc/os-release")) -> dict:
    result = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if not separator or not re.fullmatch(r"[A-Z_][A-Z_0-9]*", key):
                continue
            try:
                tokens = shlex.split(value, comments=True)
            except ValueError:
                continue
            if len(tokens) == 1:
                result[key] = tokens[0]
    except OSError:
        pass
    return result


@dataclass(frozen=True)
class Adapter:
    family: str
    manager: str
    query: str
    runtime_packages: tuple
    tool_packages: tuple

    @property
    def allowed_packages(self) -> tuple:
        return tuple(dict.fromkeys((*self.runtime_packages, *(package for _, package in self.tool_packages))))

    def required_packages(self, lookup=None) -> list:
        lookup = lookup or shutil.which
        return list(dict.fromkeys((*self.runtime_packages,
                                   *(package for tool, package in self.tool_packages if not lookup(tool)))))

    def query_command(self, package: str) -> list:
        self.validate_packages([package])
        if self.query == "dpkg-query":
            return ["dpkg-query", "-W", "-f=${Status}", package]
        if self.query == "rpm":
            return ["rpm", "-q", "--quiet", package]
        return ["pacman", "-Q", package]

    def validate_packages(self, packages: list) -> None:
        if any(package not in self.allowed_packages for package in packages):
            raise CliError("Dependency command contains an unregistered package.", "invalid_dependencies", 2)

    def install_commands(self, packages: list) -> list:
        self.validate_packages(packages)
        if not packages:
            return []
        if self.manager == "apt-get":
            return [["apt-get", "update"],
                    ["apt-get", "install", "-y", "--no-install-recommends", *packages]]
        if self.manager in {"dnf", "yum"}:
            return [[self.manager, "-y", "install", *packages]]
        if self.manager == "zypper":
            return [["zypper", "--non-interactive", "install", "--no-recommends", *packages]]
        # Arch: use the existing sync database. Never perform a partial -Sy refresh
        # or an unsolicited full system upgrade. The operator must keep Arch current.
        return [["pacman", "-S", "--needed", "--noconfirm", *packages]]


def detect_adapter(release: dict | None = None, lookup=None) -> Adapter | None:
    release = read_os_release() if release is None else release
    lookup = lookup or shutil.which
    identities = [release.get("ID", "").lower(), *release.get("ID_LIKE", "").lower().split()]
    for identity in identities:
        if identity in {"ubuntu", "debian", "linuxmint", "pop", "kali", "raspbian"}:
            if lookup("apt-get") and lookup("dpkg-query"):
                return Adapter("debian", "apt-get", "dpkg-query",
                               ("ca-certificates", "lib32gcc-s1", "lib32stdc++6", "lib32z1"),
                               (("ss", "iproute2"), ("useradd", "passwd"), ("runuser", "util-linux"),
                                ("ldd", "libc-bin"), ("nologin", "util-linux")))
        elif identity in {"opensuse", "opensuse-leap", "opensuse-tumbleweed", "suse", "sles", "sled"}:
            if lookup("zypper") and lookup("rpm"):
                return Adapter("suse", "zypper", "rpm",
                               ("ca-certificates", "glibc-32bit", "libgcc_s1-32bit",
                                "libstdc++6-32bit", "libz1-32bit"),
                               (("ss", "iproute2"), ("useradd", "shadow"), ("runuser", "util-linux"),
                                ("ldd", "glibc"), ("nologin", "util-linux")))
        elif identity in {"fedora", "rhel", "centos", "rocky", "almalinux", "ol", "amzn"}:
            manager = next((name for name in ("dnf", "yum") if lookup(name)), None)
            if manager and lookup("rpm"):
                return Adapter("rpm", manager, "rpm",
                               ("ca-certificates", "glibc.i686", "libgcc.i686", "libstdc++.i686", "zlib.i686"),
                               (("ss", "iproute"), ("useradd", "shadow-utils"), ("runuser", "util-linux"),
                                ("ldd", "glibc-common"), ("nologin", "util-linux")))
        elif identity in {"arch", "archlinux", "manjaro", "endeavouros"}:
            if lookup("pacman"):
                return Adapter("arch", "pacman", "pacman",
                               ("ca-certificates", "lib32-glibc", "lib32-gcc-libs", "lib32-zlib"),
                               (("ss", "iproute2"), ("useradd", "shadow"), ("runuser", "util-linux"),
                                ("ldd", "glibc"), ("nologin", "util-linux")))
    return None
