"""Optional QR authentication and downloads via the external DepotDownloader.

SteamCMD password authentication remains the default, independent provider.
"""
from __future__ import annotations

import os
import platform
import secrets
import shutil
import sys
import tempfile
import time
from pathlib import Path

from . import auth, installer, packages, steam, storage
from .common import CliError, REDACTOR, deadline_remaining, progress, run
from .model import APP_ID, MIN_FREE

PURPOSE = "depotdownloader_qr"
MARKER = ".l4d2-qr-auth.json"
LINUX_DEPOT = 222863


def require_context(*, terminal=False):
    if (os.name != "posix" or platform.system() != "Linux" or
            platform.machine() not in {"x86_64", "amd64"} or platform.libc_ver()[0] != "glibc"):
        raise CliError("QR downloads require Linux x86_64/glibc.", "unsupported_platform", 4)
    if os.geteuid() == 0:
        raise CliError("Run QR authentication/downloads as a normal user, without sudo.", "root_login_refused", 3)
    if terminal and not all(s.isatty() for s in (sys.stdin, sys.stdout, sys.stderr)):
        raise CliError("QR authentication needs a foreground terminal (ssh -t).", "interactive_required", 2)


def select_cache(value: str | None) -> Path:
    cache = Path(value).expanduser() if value else Path.home() / ".local/share/l4d2-cli/qr-auth"
    if not cache.is_absolute() or ".." in cache.parts or cache == Path.home() or len(cache.parts) < 3:
        raise CliError("Choose an absolute dedicated QR cache directory.", "unsafe_auth_cache", 4)
    storage.reject_links(cache)
    return cache


def read_state(cache: Path) -> dict:
    auth.private_tree(cache, chmod=False)
    state = storage.read_json(cache / MARKER)
    if (state.get("schema_version") != 1 or state.get("purpose") != PURPOSE or
            state.get("app_id") != APP_ID):
        raise CliError("Not a registered CLI QR cache; password caches are separate.", "auth_cache_conflict", 4)
    return state


def environment(cache: Path) -> dict:
    env = os.environ.copy()
    # .NET IsolatedStorage stores login tokens in the user's data directory.
    for name, folder in (("HOME", "home"), ("DOTNET_CLI_HOME", "home"),
                         ("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"),
                         ("XDG_CACHE_HOME", "cache")):
        target = cache / folder
        target.mkdir(mode=0o700, exist_ok=True)
        env[name] = str(target)
    return env


def validate_helper(path: Path) -> None:
    storage.reject_links(path)
    if not path.is_file() or path.stat().st_size > 256 * 1024**2:
        raise CliError("Supply the trusted self-contained Linux x64 DepotDownloader executable.",
                       "invalid_qr_helper", 2)
    with path.open("rb") as handle:
        header = handle.read(20)
    if (len(header) < 20 or header[:4] != b"\x7fELF" or header[4:6] != b"\x02\x01" or
            int.from_bytes(header[18:20], "little") != 62):
        raise CliError("QR helper must be the self-contained Linux x64 ELF release, not a DLL or archive.",
                       "invalid_qr_helper", 2)


def clone_helper(source: str, cache: Path) -> tuple[Path, str]:
    source_path = Path(source).absolute()
    validate_helper(source_path)
    folder = cache / "tool"
    folder.mkdir(mode=0o700)
    tool = folder / "DepotDownloader"
    # Only the program is copied; no source login data is imported.
    shutil.copyfile(source_path, tool)
    tool.chmod(0o700)
    return tool, storage.sha256(tool)


def registered_helper(cache: Path, state: dict) -> Path:
    tool = cache / "tool/DepotDownloader"
    validate_helper(tool)
    if storage.sha256(tool) != state.get("helper_sha256"):
        raise CliError("QR helper changed since cache registration.", "qr_helper_changed", 4)
    return tool


def manifest_arguments(tool: Path, probe: Path, *, qr=False, username=None) -> list[str]:
    args = [str(tool), "-app", str(APP_ID), "-depot", str(LINUX_DEPOT), "-os", "linux",
            "-dir", str(probe), "-manifest-only", "-remember-password",
            "-loginid", str(secrets.randbelow(2**31 - 1) + 1)]
    # DepotDownloader explicitly rejects combining -qr and -username.
    return args + (["-qr"] if qr else ["-username", username])


def verify_manifest(probe: Path) -> None:
    manifests = list(probe.glob(f"manifest_{LINUX_DEPOT}_*.txt"))
    if not any(p.is_file() and not p.is_symlink() and p.stat().st_size > 0 for p in manifests):
        raise CliError("QR backend did not provide a fresh Linux depot manifest. "
                       "Authentication or Linux authorization remains unverified.",
                       "qr_manifest_unverified", details={"cached_login_verified": False})


def login(helper: str | None, cache_value: str | None, timeout: float, *,
          steam_user: str | None = None, fresh=False) -> dict:
    require_context(terminal=True)
    cache = select_cache(cache_value)
    exists = cache.exists()
    state = {}
    if exists:
        if not cache.is_dir():
            raise CliError("QR cache must be a directory.", "unsafe_auth_cache", 4)
        if fresh and any(cache.iterdir()):
            raise CliError("--fresh requires a new/empty QR cache; existing logins are retained.",
                           "fresh_cache_required", 4)
        if any(cache.iterdir()):
            state = read_state(cache)
    if not state and not helper:
        raise CliError("QR requires the optional DepotDownloader backend. Supply --depotdownloader-path "
                       "/path/to/DepotDownloader (official SteamRE Linux x64 release).", "qr_helper_required", 2)
    if helper:
        validate_helper(Path(helper).absolute())
    if steam_user is None:
        steam_user = input("Steam account name (verify this account after scanning): ").strip()
    steam.validate_login_name(steam_user)
    if steam_user == "anonymous":
        raise CliError("QR login requires your Steam account name.", "invalid_steam_user", 2)
    REDACTOR.register(steam_user)
    deadline = time.monotonic() + timeout
    mask = os.umask(0o077)
    try:
        cache.mkdir(mode=0o700, parents=True, exist_ok=True)
        auth.private_tree(cache)
        with auth.cache_lock(cache):
            if state:
                tool = registered_helper(cache, state)
            else:
                tool, digest = clone_helper(helper, cache)
                state = {"schema_version": 1, "purpose": PURPOSE, "app_id": APP_ID,
                         "helper_sha256": digest}
            state["verified"] = False
            state.pop("steam_user", None)
            storage.save_json(cache / MARKER, state, private=True)
            env = environment(cache)
            probe = Path(tempfile.mkdtemp(prefix="qr-manifest-", dir=cache))
            progress("Scan the terminal QR code with the Steam mobile app and confirm this sign-in. "
                     "QR tokens stay in this private backend cache; they are not SteamCMD credentials.")
            try:
                auth.interactive(manifest_arguments(tool, probe, qr=True), tool, env,
                                 deadline_remaining(deadline), label="DepotDownloader")
                verify_manifest(probe)
                # Do not claim cache reuse solely because a scan was accepted.
                check = Path(tempfile.mkdtemp(prefix="cached-manifest-", dir=cache))
                result = run(manifest_arguments(tool, check, username=steam_user), cwd=tool.parent,
                             env=env, timeout=deadline_remaining(deadline), check=False)
                if result.returncode:
                    raise CliError("QR cache reuse failed. Check the account name and sign in again.",
                                   "qr_cached_login_unverified")
                verify_manifest(check)
                state.update(verified=True, steam_user=steam_user)
                storage.save_json(cache / MARKER, state, private=True)
            finally:
                auth.private_tree(cache)
    finally:
        os.umask(mask)
    return {"auth_method": "qr", "provider": "DepotDownloader", "cached_login_verified": True,
            "linux_manifest_verified": True, "full_download_verified": False,
            "steamcmd_cache_compatible": False, "auth_cache": str(cache),
            "next_step": "l4d2 download server --qr-cache " + str(cache) + " --output server-linux.tar.gz"}


def prepare_bundle(cache_value: str, output: Path, timeout: float) -> dict:
    require_context()
    deadline = time.monotonic() + timeout
    cache = select_cache(cache_value)
    state = read_state(cache)
    if not state.get("verified") or not state.get("steam_user"):
        raise CliError("First complete auth login --method qr for this cache.", "qr_cache_unverified", 4)
    username = state["steam_user"]
    steam.validate_login_name(username)
    REDACTOR.register(username)
    storage.reject_links(output)
    if output.exists() or cache == output or cache in output.parents:
        raise CliError("Choose a new output outside the private QR cache.", "output_exists", 4)
    mask = os.umask(0o077)
    try:
        with auth.cache_lock(cache):
            state = read_state(cache)
            if not state.get("verified") or not state.get("steam_user"):
                raise CliError("QR cache changed; complete QR authentication again.", "qr_cache_unverified", 4)
            username = state["steam_user"]
            steam.validate_login_name(username)
            REDACTOR.register(username)
            tool = registered_helper(cache, state)
            content = cache / "content"
            content_marker = cache / ".qr-content.json"
            if content.exists() and any(content.iterdir()):
                if storage.read_json(content_marker) != {"app_id": APP_ID, "os": "linux"}:
                    raise CliError("Unregistered QR download content.", "target_conflict", 4)
            content.mkdir(mode=0o700, exist_ok=True)
            storage.save_json(content_marker, {"app_id": APP_ID, "os": "linux"}, private=True)
            credit = installer.allocated_content_bytes(content, os.geteuid())
            minimum = max(2 * 1024**3, MIN_FREE - min(credit, MIN_FREE))
            free = shutil.disk_usage(content).free
            if free < minimum:
                raise CliError("Insufficient space for QR game download/bundling; partial content is retained.",
                               "insufficient_space", details={"minimum_free_bytes": minimum, "free_bytes": free})
            env = environment(cache)
            args = [str(tool), "-app", str(APP_ID), "-os", "linux", "-all-archs", "-language", "english",
                    "-dir", str(content), "-validate", "-username", username, "-remember-password",
                    "-loginid", str(secrets.randbelow(2**31 - 1) + 1)]
            try:
                result = run(args, cwd=tool.parent, env=env, timeout=deadline_remaining(deadline),
                             stream=True, check=False)
                if result.returncode:
                    raise CliError("QR backend download failed. Check its diagnostics; reauthenticate if the cache expired.",
                                   "qr_download_failed", details={"partial_content_retained": True})
                packages.validate_game_tree(content)
                bundled = packages.create_bundle(content, output, deadline,
                                                  source_label="DepotDownloader official public dedicated content")
                return {**bundled, "provider": "DepotDownloader", "auth_method": "qr_cached"}
            finally:
                auth.private_tree(cache)
    finally:
        os.umask(mask)
