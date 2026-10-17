"""Configuration and lifecycle operations for the one registered service."""
from __future__ import annotations

import os
import socket
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from . import a2s, host, network, packages, storage
from .common import CliError, confirm, deadline_remaining, progress, run
from .installer import apply_configuration, registered_config, runtime_check, validate_target
from .model import Config, SERVICE, Source, game_path, validate_vanilla_secrets
from .storage import PATHS


def public_config(config: Config) -> dict:
    return asdict(config)


def installed() -> tuple[Config, dict]:
    state = storage.read_json(PATHS.state)
    if state.get("status") != "installed":
        raise CliError("Server is not installed. Run install, or resume the recorded failed stage.", "not_installed", 4,
                       {"installation": state.get("status", "not_installed"), "phase": state.get("phase")})
    config = storage.load_config()
    if state.get("game_dir") != config.game_dir:
        raise CliError("Stored installation and config targets differ.", "target_conflict", 4)
    return config, state


def recover_mutation(config: Config) -> None:
    if storage.recover(config):
        host.systemctl("daemon-reload")
        restored = storage.load_config()
        host.systemctl("enable" if restored.autostart else "disable")
        progress("Recovered the interrupted configuration transaction.")


def running_config(config: Config, state: dict, service: dict) -> Config:
    if (state.get("running_invocation_id") == service.get("InvocationID") and state.get("running_config")):
        current = Config(**state["running_config"])
        current.validate()
        return current
    # An autostart or engine recovery may have created a new systemd invocation.
    # Read systemd's loaded arguments for this fixed service; never a shell string.
    response = run(["systemctl", "show", SERVICE, "--no-pager", "--property=ExecStart", "--value"], timeout=5, check=False)
    if not response.returncode:
        import re
        text = response.stdout
        port = re.search(r'(?:^|\s)-port\s+"?([0-9]+)', text)
        bind = re.search(r'(?:^|\s)-ip\s+"?([0-9.]+)', text)
        if port and bind:
            return Config(**{**asdict(config), "port": int(port[1]), "bind": bind[1]})
    return config


def query_address(config: Config) -> str:
    if config.bind != "0.0.0.0":
        return config.bind
    # The vanilla Steam query responder can ignore loopback sources. Ask the
    # kernel for a routed local IPv4 address; UDP connect sends no packets.
    # TEST-NET-1 is only used for route selection, never as a query target.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            address = probe.getsockname()[0]
            import ipaddress
            candidate = ipaddress.IPv4Address(address)
            if not candidate.is_loopback and not candidate.is_unspecified:
                return address
    except (OSError, ValueError):
        pass
    return "127.0.0.1"


def connection_guide(config: Config) -> dict:
    group = config.visibility == "group"
    return {"local_query": f"{query_address(config)}:{config.port}",
            "visibility": config.visibility, "steam_group_id": config.steam_group_id or None,
            "join_method": "steam_group_lobby" if group else "direct",
            "lobby_only": group,
            "player_command": None if group else f"connect YOUR_SERVER_IP:{config.port}",
            "public_reachability": "unverified", "client_join": "unverified",
            "steps": (["Use an account in the configured Steam group; check Steam Group Servers in L4D2 and join through a lobby.",
                       "Native group priority applies when the room is empty; it is not a strict member-only whitelist.",
                       "Steam group listing and actual member/nonmember joins require a real client test."] if group else []) +
                     [f"Allow the actual game UDP port {config.port} in the host firewall and cloud security group.",
                      "If behind a router, forward that UDP port to this host; carrier NAT may prevent inbound access.",
                      "Use an external vanilla client to confirm joining and playing. Local A2S is not that proof.",
                      "Check actual engine side ports during Linux acceptance before publishing firewall guidance."]}


def status(*, query_timeout: float = 3) -> dict:
    host.require_platform()
    state = storage.read_json(PATHS.state)
    if not state:
        return {"installation": "not_installed", "service": None, "query": None, "game_ready": False}
    config = registered_config()
    service = host.service_info()
    current = running_config(config, state, service) if service.get("MainPID") else config
    query, error, ownership = None, None, None
    if service.get("MainPID"):
        ownership = host.udp_owner(current.port, service["MainPID"])
        try:
            query = a2s.query(query_address(current), current.port, query_timeout)
        except CliError as exc:
            error = {"code": exc.code, "message": str(exc)}
    active = service.get("ActiveState") == "active"
    ready = bool(active and query and ownership is True)
    desired_revision = config.revision
    applied = state.get("applied_revision") if state.get("running_invocation_id") == service.get("InvocationID") else None
    return {"installation": state.get("status", "unknown"), "phase": state.get("phase"), "service": service,
            "desired_config": public_config(config),
            "running_config": public_config(current) if service.get("MainPID") and applied is not None else None,
            "running_endpoint": {"bind": current.bind, "port": current.port},
            "query": query, "query_error": error, "port_owned_by_service": ownership, "game_ready": ready,
            "readiness": "ready" if ready else ("ownership_unknown" if active and query and ownership is None else "not_ready"),
            "desired_revision": desired_revision, "applied_revision": applied,
            "configuration_pending": desired_revision != applied if applied is not None else "unknown",
            "connection": connection_guide(current), "pending_transaction": PATHS.journal.exists()}


def configure(config: Config, secrets: dict, *, dry_run: bool, yes: bool) -> dict:
    host.require_platform()
    old, state = installed()
    config.validate()
    if config.game_dir != old.game_dir:
        raise CliError("Game directory cannot be changed by configure.", "target_conflict", 4)
    validate_vanilla_secrets(secrets)
    if not config.name.isascii():
        raise CliError("Vanilla L4D2 truncates non-ASCII server names. Use an ASCII --name in this version.",
                       "unsupported_server_name", 2)
    target = game_path(config.game_dir)
    if config.map not in packages.installed_maps(target):
        raise CliError("Selected official map is not installed.", "map_not_installed", 2)
    info = host.service_info()
    host.verify_service(info)
    live = running_config(old, state, info) if info.get("MainPID") else old
    if not (info.get("MainPID") and live.port == config.port and host.udp_owner(live.port, info["MainPID"]) is True):
        host.check_udp_available(config.bind, config.port)
    changed = {key: {"before": asdict(old)[key], "after": value} for key, value in asdict(config).items()
               if key != "revision" and asdict(old)[key] != value}
    plan = {"target": config.game_dir, "service": SERVICE, "changes": changed,
            "private_settings": {key: bool(secrets.get(key)) for key in ("join_password", "rcon_password")},
            "restart_required": bool(info.get("MainPID")), "restarts_server": False, "requires_root": True}
    if dry_run:
        return {"dry_run": True, "plan": plan}
    host.require_root()
    confirm("Save main server configuration; the current game will continue until a separate restart. "
            f"Autostart={config.autostart}.", yes)
    with storage.operation_lock():
        recover_mutation(old)
        old, state = installed()
        validate_target(old, state)
        storage.check_managed_hashes(old)
        _, gid, _ = host.game_account(target)
        if gid < 0:
            raise CliError("Registered game account is missing.", "missing_game_user", 4)
        previous_secrets = storage.load_secrets()
        if asdict(config) == asdict(old) and secrets == previous_secrets:
            return {"unchanged": True, "configuration": public_config(old)}
        config.revision = old.revision + 1
        if info.get("MainPID") and state.get("running_invocation_id") != info.get("InvocationID"):
            state.update(running_invocation_id=info.get("InvocationID"), running_config=asdict(live),
                         applied_revision=old.revision)
        state["config"] = asdict(config)
        apply_configuration(config, secrets, state, gid)
        return {"saved": True, "configuration": public_config(config), "restart_required": bool(info.get("MainPID"))}


def _start(config: Config, state: dict, deadline: float) -> dict:
    validate_vanilla_secrets(storage.load_secrets())
    storage.check_managed_hashes(config)
    runtime_check(game_path(config.game_dir), deadline_remaining(deadline))
    info = host.service_info()
    if info.get("MainPID"):
        host.verify_service(info)
        existing = status(query_timeout=min(3, deadline_remaining(deadline)))
        if existing["game_ready"]:
            return {"already_running": True, **existing}
        raise CliError("Service is already running but not verified ready. Inspect doctor/logs or request a restart.", "running_not_ready", 4, existing)
    host.check_udp_available(config.bind, config.port)
    if config.map not in packages.installed_maps(game_path(config.game_dir)):
        raise CliError("Configured map is missing.", "map_not_installed", 2)
    host.systemctl("daemon-reload", deadline_remaining(deadline))
    host.verify_service(host.service_info(timeout=min(5, deadline_remaining(deadline))))
    attempts = 0
    previous = None
    try:
        host.systemctl("start", deadline_remaining(deadline))
        next_query = time.monotonic()
        spacing = max(1, deadline_remaining(deadline) / 3)
        while True:
            remaining = deadline_remaining(deadline)
            info = host.service_info(timeout=min(5, deadline_remaining(deadline)))
            if info.get("ActiveState") == "failed":
                raise CliError("Service failed during startup; inspect server logs.", "startup_failed")
            if info.get("MainPID") and time.monotonic() >= next_query and attempts < 3:
                attempts += 1
                next_query = time.monotonic() + spacing
                ownership = host.udp_owner(config.port, info["MainPID"], timeout=min(5, deadline_remaining(deadline)))
                try:
                    query = a2s.query(query_address(config), config.port, min(3, deadline_remaining(deadline)))
                    if ownership is True and info.get("ActiveState") == "active":
                        state.update(applied_revision=config.revision, running_config=asdict(config),
                                     running_invocation_id=info.get("InvocationID"))
                        storage.save_json(PATHS.state, state)
                        return {"started": True, "service": info, "query": query, "game_ready": True,
                                "connection": connection_guide(config)}
                    previous = "Game response could not be associated with the managed process."
                except CliError as exc:
                    previous = str(exc)
            if attempts >= 3:
                raise CliError(f"Game did not become ready after three queries: {previous}", "startup_not_ready")
            time.sleep(min(0.5, deadline_remaining(deadline)))
    except BaseException:
        # Queue cleanup without waiting past the user's readiness deadline.
        try:
            run(["systemctl", "--no-block", "stop", SERVICE], timeout=5)
            progress("Startup did not complete; queued stop for the managed service. Check status for cleanup completion.")
        except CliError:
            progress("Startup failed and cleanup could not be queued; inspect the managed service.")
        raise


def _stop(config: Config, state: dict, deadline: float) -> dict:
    before = host.service_info()
    host.verify_service(before)
    current = running_config(config, state, before) if before.get("MainPID") else config
    old_group = before.get("ControlGroup")
    already_stopped = not before.get("MainPID") and before.get("ActiveState") in {"inactive", "failed"}
    host.systemctl("stop", deadline_remaining(deadline))
    while True:
        info = host.service_info(timeout=min(5, deadline_remaining(deadline)))
        # Retain the pre-stop cgroup path even when systemd clears its live property.
        descendants = host.service_pids({**info, "ControlGroup": old_group or info.get("ControlGroup")})
        if not info.get("MainPID") and not descendants and info.get("ActiveState") in {"inactive", "failed"}:
            host.check_udp_available(current.bind, current.port)
            return {"stopped": True, "already_stopped": already_stopped, "service": info,
                    "service_processes_remaining": [], "game_port_released": True}
        time.sleep(min(0.5, deadline_remaining(deadline)))


def lifecycle(action: str, *, dry_run: bool, yes: bool, timeout: float, as_json: bool) -> dict:
    host.require_platform()
    config, state = installed()
    plan = {"action": action, "service": SERVICE, "target": config.game_dir,
            "disconnects_players": action in {"stop", "restart"}, "requires_root": True}
    if dry_run:
        return {"dry_run": True, "plan": plan}
    host.require_root()
    if action in {"stop", "restart"}:
        current = status()
        query = current.get("query") or {}
        confirm(f"{action.capitalize()} main ({SERVICE}), current map={query.get('map', 'unknown')}, "
                f"human players={query.get('human_players', 'unknown')}. Connected players will be disconnected.", yes, as_json=as_json)
    deadline = time.monotonic() + timeout
    with storage.operation_lock():
        recover_mutation(config)
        config, state = installed()
        validate_target(config, state)
        storage.check_managed_hashes(config)
        storage.load_secrets()  # Register secrets before reading any engine output.
        if action == "stop":
            return _stop(config, state, deadline)
        if action == "restart":
            _stop(config, state, deadline)
        return _start(config, state, deadline)


def logs(lines: int, since: str | None, follow: bool) -> dict:
    host.require_platform()
    installed()
    host.require_root()  # Ensures actual known secret values are available for redaction.
    storage.load_secrets()
    if not 1 <= lines <= 10000:
        raise CliError("--lines must be between 1 and 10000.", "invalid_lines", 2)
    args = ["journalctl", "--unit", SERVICE, "--lines", str(lines), "--no-pager", "--output=short-iso", "--quiet"]
    if since:
        try:
            datetime.fromisoformat(since)
        except ValueError:
            raise CliError("--since expects an ISO date or datetime, e.g. 2026-10-11 12:00:00.", "invalid_since", 2) from None
        args.extend(["--since", since.replace("T", " ")])
    if follow:
        args.append("--follow")
    run(args, timeout=None if follow else 30, stream=True, stream_stdout=True)
    return {}


def doctor() -> tuple[dict, bool]:
    platform = host.platform_report()
    report = {"platform": platform, "checks": [], "public_reachability": "unverified", "client_join": "unverified"}
    checks = report["checks"]
    if not platform["supported"]:
        checks.append({"name": "platform", "status": "fail",
                       "message": "Linux host requirements are unmet: " + ", ".join(platform["unmet_requirements"])})
        return report, False
    missing = host.missing_dependencies()
    checks.append({"name": "dependencies", "status": "fail" if missing else "pass", "missing": missing})
    state = storage.read_json(PATHS.state)
    report["network"] = network.check(Source(), needs_dependencies=bool(missing))
    checks.append({"name": "network", "status": "fail" if report["network"].get("required_failures") and state.get("status") != "installed" else "warn",
                   "message": "Lightweight probes do not verify the complete Steam download protocol."})
    if state.get("status") != "installed":
        checks.append({"name": "installation", "status": "fail", "message": state.get("phase", "not installed")})
        return report, False
    current = status()
    report["server"] = current
    try:
        host.verify_service(current["service"])
        checks.append({"name": "registered_unit", "status": "pass"})
    except CliError as exc:
        checks.append({"name": "registered_unit", "status": "fail", "message": str(exc)})
    checks.append({"name": "service", "status": "pass" if current["service"].get("ActiveState") == "active" else "fail"})
    checks.append({"name": "local_game_query", "status": "pass" if current["query"] else "fail"})
    checks.append({"name": "query_ownership", "status": "pass" if current["port_owned_by_service"] is True else
                   ("unknown" if current["port_owned_by_service"] is None else "fail")})
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        try:
            storage.check_managed_hashes(storage.load_config())
            storage.load_secrets()
            checks.append({"name": "managed_configuration", "status": "pass"})
        except CliError as exc:
            checks.append({"name": "managed_configuration", "status": "fail", "message": str(exc)})
    else:
        checks.append({"name": "private_configuration", "status": "unknown", "message": "Use sudo doctor to check private file integrity."})
    checks.append({"name": "external_client", "status": "unknown", "message": "External player connection and gameplay have not been verified."})
    return report, not any(check["status"] == "fail" for check in checks)
