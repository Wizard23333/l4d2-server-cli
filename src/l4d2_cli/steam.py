"""Official SteamCMD acquisition and vanilla game preparation."""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path

from . import host, packages
from .common import CliError, deadline_remaining, progress, run
from .model import APP_ID, STEAMCMD_URL, Source
from .storage import reject_links


def prepare_tool(source: Source, destination: Path, timeout: float, *, include_login_cache: bool = True) -> Path:
    deadline = time.monotonic() + timeout
    reject_links(destination)
    destination.mkdir(parents=True, exist_ok=True)
    ready = destination / "steamcmd.sh"
    if ready.is_file():
        return validate_tool(destination)
    if any(destination.iterdir()):
        raise CliError("Incomplete SteamCMD directory; preserve it and choose a clean tool directory.", "tool_conflict", 4)
    if source.steamcmd_path:
        input_tool = Path(source.steamcmd_path).resolve()
        if input_tool.name not in {"steamcmd.sh", "steamcmd"}:
            raise CliError("Expected a Linux steamcmd.sh/steamcmd path.", "invalid_steamcmd", 2)
        parent = input_tool.parent
        if not (parent / "linux32/steamcmd").is_file():
            raise CliError("An existing full Linux SteamCMD directory is required, not an apt wrapper.", "invalid_steamcmd", 2)
        # Clone executable/runtime files so SteamCMD updates cannot rewrite the original.
        def copy_file(input_path, output_path):
            deadline_remaining(deadline)
            with open(input_path, "rb") as input_file, open(output_path, "xb") as output_file:
                while block := input_file.read(1024 * 1024):
                    deadline_remaining(deadline)
                    output_file.write(block)
            shutil.copystat(input_path, output_path)
            return output_path
        names = ["steamcmd.sh", "linux32", "linux64", "package", "public"]
        if include_login_cache:
            names.extend(["config", "Steam/config"])
        for name in names:
            path = parent / name
            if not path.exists():
                continue
            reject_links(path)
            if path.is_dir():
                for child in path.rglob("*"):
                    if child.is_symlink():
                        raise CliError("Linked tool files are unsupported.", "unsafe_tool", 2)
                shutil.copytree(path, destination / name, copy_function=copy_file)
            else:
                copy_file(path, destination / name)
        # Cached authentication stays in the private tool directory, never a game bundle.
        for directory in (destination / "config", destination / "Steam/config"):
            for private in directory.rglob("*"):
                private.chmod(0o700 if private.is_dir() else 0o600)
            if directory.exists():
                directory.chmod(0o700)
        if (destination / "Steam").exists():
            (destination / "Steam").chmod(0o700)
    else:
        if source.steamcmd_archive:
            archive = Path(source.steamcmd_archive)
            packages.validate_bootstrap(archive, source.sha256, deadline)
        else:
            archive = destination.parent / "steamcmd_linux.tar.gz"
            if not archive.exists():
                packages.download(STEAMCMD_URL, archive, deadline_remaining(deadline))
            packages.validate_bootstrap(archive, deadline=deadline)
        if not include_login_cache:
            with packages.open_archive(archive, deadline) as handle:
                members = packages.inventory(handle, deadline)
                for name in members:
                    parts = Path(name).parts
                    if (parts[0] in {"config", "userdata", ".steam"} or
                            parts[:2] == ("Steam", "config") or parts[0].startswith("ssfn")):
                        raise CliError("Fresh login input must not contain Steam login files; "
                                       "use the official bootstrap or a runtime-only directory.",
                                       "fresh_source_contains_login", 4)
        packages.extract(archive, destination, max_bytes=512 * 1024**2, deadline=deadline)
    return validate_tool(destination)


def validate_tool(destination: Path) -> Path:
    ready = destination / "steamcmd.sh"
    binary = destination / "linux32/steamcmd"
    if not ready.is_file() or ready.is_symlink() or not binary.is_file():
        raise CliError("Archive is not a complete Linux SteamCMD bootstrap.", "invalid_steamcmd", 2)
    with binary.open("rb") as handle:
        if handle.read(4) != b"\x7fELF":
            raise CliError("SteamCMD runtime is not a Linux executable.", "invalid_steamcmd", 2)
    ready.chmod(0o755)
    binary.chmod(0o755)
    return ready


def validate_login_name(steam_user: str) -> None:
    import re
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}", steam_user):
        raise CliError("Use a Steam account login name, without whitespace or command characters.",
                       "invalid_steam_user", 2)


def content_failure(code: str, steam_user: str, *, timed_out: bool = False) -> CliError:
    details = {"phase": "steam_content", "app_id": APP_ID,
               "login": "anonymous" if steam_user == "anonymous" else "cached_account",
               "http_proxy_proves_steam_routing": False, "partial_content_retained": True}
    if code == "steam_invalid_platform":
        message = ("Steam rejected the Linux install request (Invalid platform). Changing the HTTP proxy "
                   "or supplying a SteamCMD bootstrap archive does not resolve this rejection. "
                   "Verify the official Linux install and account authorization on a connected Linux host.")
    elif code == "steam_auth_required":
        message = ("Steam rejected the login or depot authorization. A proxy or SteamCMD bootstrap package "
                   "does not grant a Steam license. Prepare a private login with l4d2 auth login --steam-user NAME, then retry using that cache.")
    elif timed_out:
        message = ("SteamCMD timed out during bootstrap, login or content download. Partial game files are retained; "
                   "retry the same registered target. HTTP proxy routing does not verify Steam content routing.")
    else:
        message = ("Steam content download failed after bounded retries. Partial files are retained; "
                   "retry the same registered target or diagnose Steam connectivity.")
    details["next_steps"] = [
        "For an authorized non-anonymous account, run as a normal user in a terminal: l4d2 auth login --steam-user NAME. Then retry with --steam-user NAME --steamcmd-path PRIVATE_CACHE/steamcmd/steamcmd.sh. A Web API key or GSLT is not a SteamCMD login token.",
        "Retry the same registered target with --steamcmd-path /path/to/steamcmd.sh; use --steam-user NAME only for a valid authorized cached login.",
        "On a connected authorized Linux host: l4d2 download server --output server.tar.gz --steamcmd-path /path/to/steamcmd.sh.",
        "After preparing system dependencies, transfer the complete verified Linux bundle and run: l4d2 server install --server-archive server.tar.gz --offline.",
    ]
    details["bootstrap_archive_scope"] = "A SteamCMD archive replaces tool acquisition only; updates, login and game download may still need the network."
    return CliError(message + " A complete verified Linux game bundle is the offline alternative.",
                    "timeout" if timed_out else code, 124 if timed_out else 1, details)


def download_game(tool: Path, game_dir: Path, timeout: float, *, game_user: bool = True,
                  home: Path | None = None, steam_user: str = "anonymous", steam_tcp: bool = False) -> dict:
    validate_login_name(steam_user)
    if steam_user != "anonymous":
        from .common import REDACTOR
        REDACTOR.register(steam_user)
    deadline = time.monotonic() + timeout
    args = [str(tool), *(["-tcp"] if steam_tcp else []),
            "+@ShutdownOnFailedCommand", "1", "+@NoPromptForPassword", "1",
            "+force_install_dir", str(game_dir), "+login", steam_user,
            "+app_update", str(APP_ID), "validate", "+quit"]
    environment = os.environ.copy()
    environment["HOME"] = str(home or tool.parent)
    if game_user:
        args, environment = host.as_game_user(args, home or tool.parent)
    progress("SteamCMD phase: bootstrap/update -> login -> official dedicated content. HTTP proxy is not proof of Steam routing.")
    for attempt in range(1, 4):
        try:
            completed = run(args, cwd=tool.parent, env=environment,
                            timeout=deadline_remaining(deadline), stream=True, check=False)
        except CliError as exc:
            if exc.code == "timeout":
                raise content_failure("timeout", steam_user, timed_out=True) from None
            raise
        lowered = completed.stdout.lower()
        if "invalid platform" in lowered:
            raise content_failure("steam_invalid_platform", steam_user)
        if any(term in lowered for term in ("no subscription", "invalid password", "steam guard", "account logon denied", "failed to log on")):
            raise content_failure("steam_auth_required", steam_user)
        success = "success! app '222860' fully installed" in lowered
        if completed.returncode == 0 and success:
            return packages.validate_game_tree(game_dir)
        progress(f"Steam content attempt {attempt}/3 did not complete. Existing app files are retained for retry.")
        if attempt < 3:
            time.sleep(min(attempt * 2, deadline_remaining(deadline)))
    raise content_failure("steam_download_failed", steam_user)


def prepare_bundle(source: Source, output: Path, timeout: float, *, steam_user: str = "anonymous",
                   steam_tcp: bool = False) -> dict:
    validate_login_name(steam_user)
    if os.geteuid() == 0:
        raise CliError("Prepare downloads as a normal user; SteamCMD must not run as root.", "root_download_refused", 3)
    missing = host.missing_dependencies()
    if missing:
        raise CliError("Prepare these dependencies first: " + ", ".join(missing), "missing_dependencies")
    reject_links(output)
    if output.exists():
        raise CliError("Output already exists.", "output_exists", 4)
    # Fresh content only; never bundle a production installation or login caches.
    with tempfile.TemporaryDirectory(prefix="l4d2-prepare-") as temporary:
        root = Path(temporary)
        if shutil.disk_usage(root).free < 20 * 1024**3:
            raise CliError("At least 20 GiB provisional preparation budget is required.", "insufficient_space")
        deadline = time.monotonic() + timeout
        tool = prepare_tool(source, root / "steamcmd", deadline_remaining(deadline))
        content = root / "serverfiles"
        content.mkdir()
        download_game(tool, content, deadline_remaining(deadline), game_user=False, home=root / "steamcmd",
                      steam_user=steam_user, steam_tcp=steam_tcp)
        return packages.create_bundle(content, output, deadline)
