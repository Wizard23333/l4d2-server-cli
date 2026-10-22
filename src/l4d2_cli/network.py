"""Source-aware bounded network probes. Never execute SteamCMD as a probe."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import getproxies, proxy_bypass

from .common import CliError, REDACTOR
from .model import STEAMCMD_URL, STEAMCMD_URLS, Source
from .network_probe import WORKER_FLAG
from .platforms import detect_adapter
from .repositories import targets as repository_targets

# Subprocesses keep DNS/TLS/HTTP bounded. A frozen CLI is not a Python
# interpreter, so it must dispatch through its dedicated internal worker.
def probe_process(kind: str, target: str, timeout: float):
    arguments = [kind, target, str(timeout)]
    if getattr(sys, "frozen", False):
        environment = dict(os.environ)
        if sys.platform.startswith("linux"):
            bundle = str(Path(sys._MEIPASS))
            original = environment.get("LD_LIBRARY_PATH")
            environment["LD_LIBRARY_PATH"] = bundle + (os.pathsep + original if original else "")
        return [sys.executable, WORKER_FLAG, *arguments], environment
    return [sys.executable, str(Path(__file__).with_name("network_probe.py")), *arguments], None


def probe(kind: str, target: str, timeout: float) -> dict:
    started = time.monotonic()
    try:
        command, environment = probe_process(kind, target, timeout)
        completed = subprocess.run(command, env=environment, capture_output=True,
                                   text=True, encoding="utf-8", errors="replace", timeout=timeout)
        if completed.returncode:
            raise ValueError("Probe process failed")
        entry = json.loads(completed.stdout)
        if (not isinstance(entry, dict) or entry.get("status") not in {"pass", "fail"}
                or not isinstance(entry.get("message"), str)):
            raise ValueError("Invalid probe response")
    except subprocess.TimeoutExpired:
        entry = {"status": "fail", "category": "timeout", "message": "Probe exceeded its deadline"}
    except (OSError, ValueError):
        entry = {"status": "fail", "category": "probe", "message": "Network probe failed to execute or returned invalid data"}
    entry.update({"target": REDACTOR.clean(target), "kind": kind,
                  "elapsed_seconds": round(time.monotonic() - started, 3)})
    return entry


def check(source: Source, *, timeout: float = 30, needs_dependencies: bool = False, archive_only: bool = False,
          needs_steam: bool = True) -> dict:
    source.validate()
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "source": source.kind,
              "offline": source.offline, "checks": [], "download_guaranteed": False,
              "scope": "Current lightweight probes only; bootstrap, authentication and full content download remain unverified."}
    entries = report["checks"]
    if source.offline:
        entries.append({"name": "installation_network", "status": "skipped",
                        "message": "Complete local server package; strict offline installation selected."})
        report["required_failures"] = 0
        return report
    requested: list[tuple[str, str, str]] = []
    repository_scopes = {}
    if source.kind == "online" and needs_steam:
        requested.extend([("steamcmd_dns", "dns", urlparse(STEAMCMD_URL).hostname),
                          ("steamcmd_archive", "archive", STEAMCMD_URL)])
    else:
        entries.append({"name": "steamcmd_archive", "status": "skipped",
                        "message": "SteamCMD acquisition is not needed for the selected source."})
    if needs_steam and not source.server_archive and not archive_only:
        requested.append(("steam_entry_dns", "dns", "api.steampowered.com"))
        requested.append(("steam_http_entry", "http", "https://api.steampowered.com/ISteamWebAPIUtil/GetServerInfo/v1/"))
    if needs_dependencies:
        adapter = detect_adapter()
        repositories = repository_targets(adapter.family) if adapter else []
        for number, repository in enumerate(repositories, 1):
            name = f"dependency_repository_{number}"
            requested.append((name, repository["kind"], repository["target"]))
            repository_scopes[name] = repository["scope"]
        if not repositories:
            entries.append({"name": "dependency_repositories", "status": "unknown",
                            "message": "No supported configured HTTP repository could be identified; inspect package manager configuration."})
    proxies = getproxies()
    bootstrap_host = urlparse(STEAMCMD_URL).hostname
    bootstrap_proxied = bool(proxies.get("https")) and not proxy_bypass(bootstrap_host)
    deadline = time.monotonic() + timeout
    exhausted = False
    for name, kind, target in requested:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            entries.append({"name": name, "status": "fail", "category": "timeout",
                            "message": "Network check total deadline exceeded."})
            exhausted = True
            break
        entry = probe(kind, target, min(5, remaining))
        if name == "steamcmd_archive":
            attempts = [dict(entry)]
            for candidate in STEAMCMD_URLS[1:]:
                if entry.get("status") == "pass":
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    exhausted = True
                    break
                entry = probe("archive", candidate, min(5, remaining))
                attempts.append(dict(entry))
            if len(attempts) > 1:
                entry["attempts"] = attempts
            if entry.get("status") == "pass":
                report["steamcmd_url"] = entry.get("target", target)
                if len(attempts) > 1:
                    for previous in entries:
                        if previous.get("name") == "steamcmd_dns":
                            previous["required"] = False
                            if previous["status"] == "fail":
                                previous["status"] = "warn"
                            previous["message"] += "; original CDN DNS is diagnostic after archive fallback."
        # A CONNECT proxy can resolve the archive host itself; direct DNS is then diagnostic.
        if name == "steamcmd_dns" and bootstrap_proxied:
            entry["required"] = False
            if entry["status"] == "fail":
                entry["status"] = "warn"
            entry["message"] += "; direct DNS is diagnostic when the archive uses an HTTPS proxy."
        # API reachability is evidence, not proof of the Steam content protocol.
        elif name.startswith("steam_entry") or name == "steam_http_entry":
            entry["required"] = False
            if entry["status"] == "fail":
                entry["status"] = "warn"
                entry["message"] += "; Steam API reachability does not prove/disprove content access."
        else:
            entry["required"] = True
        entry["name"] = name
        if name in repository_scopes:
            entry["scope"] = repository_scopes[name]
        entries.append(entry)
    entries.append({"name": "proxy_scope", "status": "warn" if proxies else "pass",
                    "message": "HTTP proxy variables apply to HTTP probes/downloads; SteamCMD routing is not verified.",
                    "configured": {key: REDACTOR.clean(value) for key, value in proxies.items()}})
    if needs_steam and not source.server_archive and not archive_only:
        entries.append({"name": "steam_bootstrap_login_content", "status": "unknown",
                        "message": "Not executed during preflight. Use a local complete game package if this phase fails."})
    report["required_failures"] = sum(entry["status"] == "fail" and entry.get("required", True) for entry in entries)
    report["timed_out"] = exhausted
    report["next_steps"] = fallback_steps(source)
    return REDACTOR.object(report)


def require_network(report: dict) -> None:
    if report.get("required_failures"):
        raise CliError("Required network preflight failed before system changes. " + " ".join(report["next_steps"]),
                       "network_preflight_failed", 124 if report.get("timed_out") else 1,
                       {"network": report})


def fallback_steps(source: Source) -> list[str]:
    steps = ["Run: l4d2 network check (or the same source options) to retry diagnosis."]
    if source.kind == "online":
        steps.append("On a connected Linux host: l4d2 download steamcmd --output steamcmd_linux.tar.gz; "
                     "then install with --steamcmd-archive FILE.")
    if not source.server_archive:
        steps.append("For Steam bootstrap/content failures: prepare with l4d2 download server --output server.tar.gz "
                     "on a connected Linux host; transfer and use --server-archive FILE --offline after preparing dependencies.")
    return steps
