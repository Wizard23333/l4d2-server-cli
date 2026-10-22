"""Dependency-free public command interface."""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from dataclasses import replace
from pathlib import Path

from . import __version__, auth, depot, host, installer, manager, network, packages, steam, storage
from .common import CliError, JSON_CONFIRMATION, REDACTOR, confirm, positive, progress, result
from .model import STEAMCMD_URL, Source, cfg_quote


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise CliError(message, "invalid_arguments", 2)


def output_flags(parser, *, timeout=None, changes=False, confirmation=False):
    parser.add_argument("--json", action="store_true", help="Write one JSON result; progress goes to stderr")
    if timeout is not None:
        parser.add_argument("--timeout", type=positive, default=timeout, metavar="SECONDS", help=f"Total operation budget (default {timeout}s)")
    if changes:
        parser.add_argument("--dry-run", action="store_true", help="Show a read-only plan")
    if confirmation:
        parser.add_argument("--yes", action="store_true", help="Confirm the validated action without a prompt")


def source_flags(parser, *, server=True):
    choices = parser.add_mutually_exclusive_group()
    choices.add_argument("--steamcmd-archive", metavar="FILE", help="Local official Linux SteamCMD bootstrap archive")
    choices.add_argument("--steamcmd-path", metavar="PATH", help="Existing full Linux steamcmd.sh directory; clone into private cache")
    if server:
        choices.add_argument("--server-archive", metavar="FILE", help="Complete Linux bundle created by download server")
        parser.add_argument("--offline", action="store_true", help="Import a complete bundle with no installation network calls")
    parser.add_argument("--sha256", metavar="HASH", help="Expected SHA256 for a local archive")
    return choices


def get_source(args) -> Source:
    source = Source(**{key: getattr(args, key, False if key == "offline" else None)
                       for key in ("steamcmd_archive", "steamcmd_path", "server_archive", "offline", "sha256")})
    source.validate()
    return source


def parser() -> Parser:
    root = Parser(prog="l4d2", description="Left 4 Dead 2 dedicated server operations CLI")
    root.add_argument("--version", action="version", version=f"l4d2-server-cli {__version__}")
    groups = root.add_subparsers(dest="group")
    setup = groups.add_parser("setup", help="Guided first server: preview, install, configure, start and joining instructions")
    setup.add_argument("--dir", metavar="PATH", help="New game directory, or the registered installation to resume")
    setup.add_argument("--name", help="ASCII server name")
    setup.add_argument("--port", type=int)
    setup.add_argument("--map", help="Official map; defaults to Dead Center for a new server")
    setup.add_argument("--difficulty", choices=("easy", "normal", "hard", "impossible"))
    setup.add_argument("--visibility", choices=("public", "group"))
    setup.add_argument("--steam-group-id", metavar="GROUP_ID")
    setup.add_argument("--no-start", action="store_true", help="Install and save settings; leave the server stopped")
    setup.add_argument("--steam-user", default="anonymous", help="Download account with an already verified private SteamCMD cache")
    setup.add_argument("--steam-tcp", action="store_true")
    source_flags(setup)
    output_flags(setup, timeout=3600, changes=True, confirmation=True)
    doctor = groups.add_parser("doctor", help="Read-only host, network and local game diagnosis")
    output_flags(doctor)
    net = groups.add_parser("network", help="Installation network preflight").add_subparsers(dest="action", required=True)
    check = net.add_parser("check", help="Probe only the network paths needed by the selected source")
    source_flags(check)
    output_flags(check, timeout=30)
    download = groups.add_parser("download", help="Prepare local installation inputs").add_subparsers(dest="action", required=True)
    archive = download.add_parser("steamcmd", help="Download the official Linux SteamCMD bootstrap archive")
    archive.add_argument("--output", required=True, metavar="FILE")
    output_flags(archive, timeout=300)
    bundle = download.add_parser("server", help="Download fresh official Linux content and create a portable bundle")
    bundle.add_argument("--output", required=True, metavar="FILE")
    bundle.add_argument("--steam-user", default="anonymous", help="Steam login name; only already cached authorized logins are supported")
    bundle.add_argument("--steam-tcp", action="store_true", help="Use SteamCMD -tcp; does not configure its proxy routing")
    bundle_sources = source_flags(bundle, server=False)
    bundle_sources.add_argument("--qr-cache", metavar="DIR", help="Private verified DepotDownloader QR cache (separate from SteamCMD)")
    output_flags(bundle, timeout=3600, confirmation=True)
    authentication = groups.add_parser("auth", help="Prepare a private native SteamCMD login").add_subparsers(dest="action", required=True)
    login = authentication.add_parser("login", help="Interactive account login and cached-login verification; normal user only")
    login.add_argument("--method", choices=("password", "qr"), default="password", help="Password via SteamCMD (default), or QR via optional DepotDownloader")
    login.add_argument("--depotdownloader-path", metavar="PATH", help="Trusted self-contained Linux x64 DepotDownloader executable, for QR only")
    login.add_argument("--steam-user", help="Steam account login name; prompted when omitted")
    login.add_argument("--fresh", action="store_true", help="Require an empty/new cache and do not import login files from an existing tool")
    login.add_argument("--cache", metavar="DIR", help="Private cache (default ~/.local/share/l4d2-cli/steamcmd-auth, or qr-auth for QR)")
    login.add_argument("--steam-tcp", action="store_true", help="Use SteamCMD -tcp; does not configure proxy routing")
    login.add_argument("--timeout", type=positive, default=300, metavar="SECONDS", help="Total login preparation budget (default 300s)")
    source_flags(login, server=False)
    server = groups.add_parser("server", help="Manage the registered main server").add_subparsers(dest="action", required=True)
    install = server.add_parser("install", help="Install vanilla server; leave it stopped")
    install.add_argument("--dir", metavar="PATH", help="Game directory (first install defaults to /opt/l4d2)")
    install.add_argument("--steam-user", default="anonymous", help="Steam login name for an existing cached authorized login")
    install.add_argument("--steam-tcp", action="store_true", help="Use SteamCMD -tcp; does not configure its proxy routing")
    source_flags(install)
    output_flags(install, timeout=3600, changes=True, confirmation=True)
    configure = server.add_parser("configure", help="Save validated config without restarting; no settings opens a wizard")
    configure.add_argument("--name")
    configure.add_argument("--bind", metavar="IPV4")
    configure.add_argument("--port", type=int)
    configure.add_argument("--map")
    configure.add_argument("--visibility", choices=("public", "group"),
                           help="Public direct joins, or Steam group priority with lobby-only joins")
    configure.add_argument("--steam-group-id", metavar="GROUP_ID",
                           help="Short numeric group ID; implies --visibility group when omitted")
    configure.add_argument("--mode", choices=("coop", "versus"))
    configure.add_argument("--difficulty", choices=("easy", "normal", "hard", "impossible"))
    for key in ("join", "rcon"):
        choice = configure.add_mutually_exclusive_group()
        choice.add_argument(f"--{key}-password-file", metavar="PATH", help=("Unsupported for vanilla L4D2; use --clear-join-password" if key == "join"
                                              else "Private UTF-8 file, no group/other permissions"))
        choice.add_argument(f"--clear-{key}-password", action="store_true")
    auto = configure.add_mutually_exclusive_group()
    auto.add_argument("--autostart", action="store_true", default=None)
    auto.add_argument("--no-autostart", dest="autostart", action="store_false")
    output_flags(configure, changes=True, confirmation=True)
    for action, budget in (("start", 120), ("stop", 60), ("restart", 180)):
        item = server.add_parser(action, help=f"{action.capitalize()} main via systemd")
        output_flags(item, timeout=budget, changes=True, confirmation=action != "start")
    status = server.add_parser("status", help="Inspect install, service and local query state")
    output_flags(status)
    logs = server.add_parser("logs", help="Show redacted service logs; sudo required")
    logs.add_argument("--lines", type=int, default=100)
    logs.add_argument("--since", help="ISO date or datetime")
    logs.add_argument("--follow", action="store_true", help="Stream until Ctrl+C")
    from .ops.cli import register
    register(groups, net)
    from .web.cli import register as register_web
    register_web(groups)
    from .agent_skill import register as register_skill
    register_skill(groups)
    return root


def password_file(filename: str) -> str:
    path = Path(filename)
    storage.reject_links(path)
    if not path.is_file() or path.stat().st_mode & 0o077:
        raise CliError("Password input must be a private regular file with mode 0600.", "secret_permissions", 3)
    if path.stat().st_size > 1024:
        raise CliError("Password file is too large.", "invalid_password", 2)
    value = path.read_text(encoding="utf-8")
    if value.endswith("\n"):
        value = value[:-1]
    if value.endswith("\r"):
        value = value[:-1]
    REDACTOR.register(value)
    if len(value) > 256:
        raise CliError("Password is longer than 256 characters.", "invalid_password", 2)
    cfg_quote(value)
    return value


def configured_inputs(args):
    host.require_platform()
    old, _ = manager.installed()
    secrets = storage.load_secrets()
    settings = {key: getattr(args, key) for key in ("name", "bind", "port", "map", "mode", "difficulty", "autostart", "visibility", "steam_group_id")
                if getattr(args, key) is not None}
    if args.steam_group_id is not None and args.visibility is None:
        settings["visibility"] = "group"
    private_changes = any(getattr(args, f"{key}_password_file") or getattr(args, f"clear_{key}_password") for key in ("join", "rcon"))
    if not settings and not private_changes and not args.dry_run:
        if args.json or not sys.stdin.isatty():
            raise CliError("Supply config flags for noninteractive usage; the wizard requires a terminal.", "interactive_required", 2)
        for key in ("name", "bind", "port", "map", "mode", "visibility"):
            value = input(f"{key} [{getattr(old, key)}]: ").strip()
            if value:
                if key == "port":
                    try:
                        value = int(value)
                    except ValueError:
                        raise CliError("Port must be an integer.", "invalid_port", 2) from None
                settings[key] = value
        if settings.get("mode", old.mode) == "coop":
            value = input(f"difficulty [{old.difficulty}]: ").strip()
            if value:
                settings["difficulty"] = value
        if settings.get("visibility", old.visibility) == "group":
            value = input(f"steam_group_id [{old.steam_group_id}]: ").strip()
            if value:
                settings["steam_group_id"] = value
        for key in ("rcon_password",):
            value = getpass.getpass(f"{key} (blank keeps current value): ")
            if value:
                REDACTOR.register(value)
                secrets[key] = value
    for key in ("join", "rcon"):
        filename = getattr(args, f"{key}_password_file")
        if filename:
            secrets[f"{key}_password"] = password_file(filename)
        if getattr(args, f"clear_{key}_password"):
            secrets.pop(f"{key}_password", None)
    if settings.get("visibility") == "public" and args.steam_group_id is None:
        settings["steam_group_id"] = ""
    config = replace(old, **settings)
    if args.difficulty is not None and config.mode == "versus":
        raise CliError("Explicit --difficulty is only valid for coop.", "difficulty_mode_mismatch", 2)
    config.validate()
    return config, secrets


def dispatch(args) -> dict:
    if args.group == 'setup':
        from .onboarding import setup
        return setup(args)
    if args.group == 'skill':
        from .agent_skill import dispatch as skill_dispatch
        return skill_dispatch(args)
    if args.group == 'web':
        from .web.cli import dispatch as web_dispatch
        return web_dispatch(args)
    from .ops.cli import GROUPS, dispatch as operations_dispatch
    if args.group in GROUPS or (args.group == 'network' and args.action != 'check'):
        return operations_dispatch(args)
    if args.group == "auth":
        if args.method == "qr":
            if args.steamcmd_path or args.steamcmd_archive or args.sha256 or args.steam_tcp:
                raise CliError("QR uses --depotdownloader-path and its own cache; omit SteamCMD source/TCP flags.", "invalid_arguments", 2)
            return depot.login(args.depotdownloader_path, args.cache, args.timeout,
                               steam_user=args.steam_user, fresh=args.fresh)
        if args.depotdownloader_path:
            raise CliError("--depotdownloader-path is only valid with --method qr.", "invalid_arguments", 2)
        return auth.login(get_source(args), args.steam_user, args.cache, args.timeout,
                          steam_tcp=args.steam_tcp, fresh=args.fresh)
    if args.group == "doctor":
        report, healthy = manager.doctor()
        if not healthy:
            raise CliError("Doctor found failed checks.", "unhealthy", details=report)
        return report
    if args.group == "network":
        host.require_platform()
        report = network.check(get_source(args), timeout=args.timeout, needs_dependencies=bool(host.missing_dependencies()))
        network.require_network(report)
        return report
    if args.group == "download":
        host.require_platform()
        output = Path(args.output).absolute()
        storage.reject_links(output)
        if output.exists():
            raise CliError("Output already exists; choose another name.", "output_exists", 4)
        if args.action == "server" and args.qr_cache:
            if args.steam_user != "anonymous" or args.steam_tcp or args.sha256:
                raise CliError("--qr-cache supplies the verified account/backend; omit SteamCMD user/TCP/archive hash flags.",
                               "invalid_arguments", 2)
            confirm("Prepare official Linux content using the separate QR backend cache; network/content access remains unverified.",
                    args.yes, as_json=args.json)
            return depot.prepare_bundle(args.qr_cache, output, args.timeout)
        source = get_source(args) if args.action == "server" else Source()
        report = network.check(source, timeout=min(30, args.timeout), archive_only=args.action == "steamcmd")
        network.require_network(report)
        if args.action == "steamcmd":
            return packages.download(STEAMCMD_URL, output, args.timeout)
        confirm("Prepare fresh official Linux server content and a complete portable bundle. "
                "Steam bootstrap/login/content access remains unverified until it runs.", args.yes, as_json=args.json)
        return steam.prepare_bundle(source, output, args.timeout, steam_user=args.steam_user, steam_tcp=args.steam_tcp)
    if args.action == "install":
        return installer.install(args.dir, get_source(args), dry_run=args.dry_run, yes=args.yes,
                                 timeout=args.timeout, steam_user=args.steam_user, steam_tcp=args.steam_tcp)
    if args.action == "configure":
        config, secrets = configured_inputs(args)
        return manager.configure(config, secrets, dry_run=args.dry_run, yes=args.yes)
    if args.action in {"start", "stop", "restart"}:
        return manager.lifecycle(args.action, dry_run=args.dry_run, yes=getattr(args, "yes", False),
                                 timeout=args.timeout, as_json=args.json)
    if args.action == "status":
        return manager.status()
    if args.action == "logs":
        return manager.logs(args.lines, args.since, args.follow)
    raise CliError("Unknown command.", "invalid_arguments", 2)


def _main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in argv or argv[:2] == ['web', '_bridge']
    command = "l4d2"
    try:
        interface = parser()
        args = interface.parse_args(argv)
        if not args.group:
            interface.print_help()
            return 0
        command = " ".join(["l4d2", args.group, *([args.action] if hasattr(args, "action") else [])])
        data = dispatch(args)
        if args.group == 'skill' and args.action == 'show' and not as_json:
            print(data['content'], end='')
        elif data or args.group != "server" or args.action != "logs":
            result(command, data, as_json=as_json)
        return 0
    except KeyboardInterrupt:
        error = CliError("Operation cancelled; managed state is retained. Inspect status before retrying.", "cancelled", 130)
    except CliError as exc:
        error = exc
    except PermissionError:
        error = CliError("Permission denied; use an authorized account or sudo for management operations.", "permission_denied", 3)
    except (OSError, ValueError, TypeError) as exc:
        error = CliError(f"Operation failed: {exc}", "operation_failed")
    except Exception as exc:
        error = CliError(f"Internal error ({type(exc).__name__}); inspect recorded state before retrying.", "internal_error")
    result(command, error.details, as_json=as_json, error=error)
    return error.exit_code


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    confirmation_context = JSON_CONFIRMATION.set('--json' in arguments)
    try:
        return _main(arguments)
    finally:
        JSON_CONFIRMATION.reset(confirmation_context)
