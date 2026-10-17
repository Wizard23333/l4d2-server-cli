"""Source-aware bounded network probes. Never execute SteamCMD as a probe."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlparse
from urllib.request import getproxies, proxy_bypass

from .common import CliError, REDACTOR
from .model import STEAMCMD_URL, Source
from .platforms import detect_adapter
from .repositories import targets as repository_targets

# A subprocess gives DNS, TLS and HTTP a hard wall-clock deadline.
_PROBE = r'''
import json, socket, ssl, sys, urllib.error, urllib.parse, urllib.request
kind, target, seconds = sys.argv[1], sys.argv[2], float(sys.argv[3])
try:
    if kind == 'dns':
        addresses = sorted({item[4][0] for item in socket.getaddrinfo(target, None)})
        output = {'status':'pass', 'message':'DNS resolved', 'addresses':addresses}
    else:
        class Redirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                parsed = urllib.parse.urlparse(newurl)
                if parsed.scheme not in ('http','https') or parsed.username or parsed.password:
                    raise ValueError('Unsafe redirect')
                if kind == 'archive':
                    hosts = ('steamcdn-a.akamaihd.net','media.steampowered.com', 'cdn.cloudflare.steamstatic.com')
                    if parsed.scheme != 'https' or parsed.hostname not in hosts:
                        raise ValueError('Unexpected archive redirect target')
                return super().redirect_request(req, fp, code, msg, headers, newurl)
        opener = urllib.request.build_opener(Redirect())
        request = urllib.request.Request(target, headers={'User-Agent':'l4d2-server-cli/0.1', 'Range':'bytes=0-255'})
        with opener.open(request, timeout=seconds) as response:
            content = response.read(256)
            if kind == 'archive' and not content.startswith(b'\x1f\x8b'):
                raise ValueError('Archive endpoint did not return gzip data (possible verification page)')
            if kind == 'repository' and (b'<html' in content.lower() or b'<!doctype' in content.lower()):
                raise ValueError('Repository returned an HTML page')
            output = {'status':'pass','message':'HTTP endpoint reachable','http_status':response.status}
except urllib.error.HTTPError as exc:
    output = {'status':'fail','category':'http','message':'HTTP '+str(exc.code)}
except socket.gaierror:
    output = {'status':'fail','category':'dns','message':'DNS resolution failed'}
except (ssl.SSLError, ssl.CertificateError) as exc:
    output = {'status':'fail','category':'tls','message':str(exc)}
except (TimeoutError, socket.timeout):
    output = {'status':'fail','category':'timeout','message':'Connection timed out'}
except urllib.error.URLError as exc:
    reason = exc.reason
    category = 'tls' if isinstance(reason, ssl.SSLError) else ('dns' if isinstance(reason, socket.gaierror) else 'connection')
    output = {'status':'fail','category':category,'message':str(reason)}
except Exception as exc:
    output = {'status':'fail','category':'response','message':str(exc)}
print(json.dumps(output))
'''


def probe(kind: str, target: str, timeout: float) -> dict:
    started = time.monotonic()
    try:
        completed = subprocess.run([sys.executable, "-c", _PROBE, kind, target, str(timeout)],
                                   capture_output=True, text=True, timeout=timeout)
        if completed.returncode:
            raise ValueError("Probe process failed")
        entry = json.loads(completed.stdout)
    except subprocess.TimeoutExpired:
        entry = {"status": "fail", "category": "timeout", "message": "Probe exceeded its deadline"}
    except (OSError, ValueError):
        entry = {"status": "unknown", "category": "probe", "message": "Probe could not produce a valid result"}
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
