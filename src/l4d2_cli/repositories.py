"""Read configured repository URLs without refreshing metadata or changing mirrors."""
from __future__ import annotations

import configparser
import glob
import re
from pathlib import Path
from urllib.parse import urlparse


def _read(path: Path) -> str:
    try:
        if path.stat().st_size <= 1024 * 1024:
            return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    return ""


def _apt(root: Path) -> list:
    urls = []
    paths = [root / "etc/apt/sources.list", *sorted((root / "etc/apt/sources.list.d").glob("*.list"))[:40]]
    for path in paths:
        for line in _read(path).splitlines():
            match = re.match(r"^\s*deb\s+(?:\[[^\]]+\]\s+)?(https?://\S+)\s+(\S+)", line)
            if match:
                urls.append(match[1].rstrip("/") + "/dists/" + match[2] + "/InRelease")
    for path in sorted((root / "etc/apt/sources.list.d").glob("*.sources"))[:40]:
        for stanza in re.split(r"\n\s*\n", _read(path)):
            values = {}
            previous = None
            for line in stanza.splitlines():
                if line.startswith("#"):
                    continue
                if line[:1].isspace() and previous:
                    values[previous] += " " + line.strip()
                    continue
                key, separator, value = line.partition(":")
                if separator:
                    previous = key.strip().lower()
                    values[previous] = value.strip()
            if values.get("enabled", "yes").lower() == "no" or "deb" not in values.get("types", "").split():
                continue
            for uri in values.get("uris", "").split():
                for suite in values.get("suites", "").split():
                    urls.append(uri.rstrip("/") + "/dists/" + suite + "/InRelease")
    return urls


def _rpm(root: Path, family: str) -> list:
    directory = root / ("etc/zypp/repos.d" if family == "suse" else "etc/yum.repos.d")
    urls = []
    for path in sorted(directory.glob("*.repo"))[:40]:
        config = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            config.read_string(_read(path))
        except configparser.Error:
            continue
        for section in config.sections():
            values = config[section]
            if values.get("enabled", "1").strip().lower() in {"0", "no", "false"}:
                continue
            for field in ("baseurl", "metalink", "mirrorlist"):
                for url in values.get(field, "").split():
                    urls.append(url.rstrip("/") + "/repodata/repomd.xml" if field == "baseurl" else url)
    return urls


def _arch(root: Path) -> list:
    urls = []
    visited = set()

    def read(path: Path, section: str = "") -> None:
        identity = (path, section)
        if identity in visited or len(visited) >= 40:
            return
        visited.add(identity)
        for line in _read(path).splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1]
            elif "=" in line and section and section != "options":
                key, value = (item.strip() for item in line.split("=", 1))
                if key == "Server":
                    value = value.replace("${repo}", section).replace("$repo", section)
                    urls.append((section, value.rstrip("/") + "/" + section + ".db"))
                elif key == "Include" and value.startswith("/etc/") and ".." not in Path(value).parts:
                    pattern = str(root / value.lstrip("/"))
                    for name in sorted(glob.glob(pattern))[:40]:
                        read(Path(name), section)

    read(root / "etc/pacman.conf")
    urls.sort(key=lambda item: item[0] != "multilib")
    return [url for _, url in urls]


def targets(family: str, root: Path = Path("/")) -> list:
    urls = _apt(root) if family == "debian" else (_arch(root) if family == "arch" else _rpm(root, family))
    found = []
    seen = set()
    for url in urls:
        for variable in ("$basearch", "${basearch}", "$arch", "${arch}"):
            url = url.replace(variable, "x86_64")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        if "$" in url:
            if "$" in parsed.hostname:
                continue
            target = {"kind": "dns", "target": parsed.hostname,
                      "scope": "DNS only: repository URL variables remain unresolved; HTTP metadata is unverified."}
        else:
            target = {"kind": "repository", "target": url,
                      "scope": "Configured HTTP endpoint only; package availability, keys and repository proxy settings are unverified."}
        identity = (target["kind"], target["target"])
        if identity not in seen:
            seen.add(identity)
            found.append(target)
        if len(found) >= 4:
            break
    return found
