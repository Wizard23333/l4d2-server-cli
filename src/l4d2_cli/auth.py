"""Interactive native SteamCMD login and private cache enrollment.

Passwords and Steam Guard input go directly to SteamCMD's inherited terminal.
"""
from __future__ import annotations

import contextlib
import os
import platform
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

from . import host, network, steam, storage
from .common import CliError, REDACTOR, _terminate, deadline_remaining, progress, run
from .model import Source


def private_tree(cache: Path, *, chmod: bool = True) -> None:
    """Reject foreign/link/special entries before changing private permissions."""
    storage.reject_links(cache)
    entries = [cache, *cache.rglob("*")]
    for entry in entries:
        metadata = entry.lstat()
        if entry.is_symlink() or metadata.st_uid != os.geteuid():
            raise CliError("Login cache must contain only files owned by the current user, without links.",
                           "unsafe_auth_cache", 4)
        if not stat.S_ISDIR(metadata.st_mode) and (
                not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1):
            raise CliError("Login cache contains a special or hard-linked file.", "unsafe_auth_cache", 4)
    if not chmod:
        return
    for entry in entries:
        metadata = entry.stat()
        entry.chmod(0o700 if entry.is_dir() or metadata.st_mode & 0o111 else 0o600)


@contextlib.contextmanager
def cache_lock(cache: Path):
    import fcntl
    lock = cache / ".login.lock"
    storage.reject_links(lock)
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_uid != os.geteuid():
            raise CliError("Unsafe login lock.", "unsafe_auth_cache", 4)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CliError("Another login is using this cache.", "auth_busy", 4) from None
        yield
    finally:
        os.close(descriptor)


@contextlib.contextmanager
def foreground_terminal():
    """Keep the native login in this controlling terminal's foreground group."""
    import termios
    terminal = sys.stdin.fileno()
    original = termios.tcgetattr(terminal)
    foreground = os.tcgetpgrp(terminal)
    if foreground != os.getpgrp():
        raise CliError("Run auth login in the foreground of an interactive terminal.",
                       "foreground_terminal_required", 2)
    previous = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
    try:
        yield terminal
    finally:
        try:
            os.tcsetpgrp(terminal, foreground)
            termios.tcsetattr(terminal, termios.TCSANOW, original)
        finally:
            signal.signal(signal.SIGTTOU, previous)


def _terminal_child() -> None:
    os.setpgrp()
    signal.signal(signal.SIGTTOU, signal.SIG_DFL)


def interactive(args: list[str], tool: Path, environment: dict, timeout: float, *, label: str = "SteamCMD") -> None:
    # No pipes or transcript. A new session would lose /dev/tty; create a process
    # group in the existing session and hand it the controlling terminal instead.
    with foreground_terminal() as terminal:
        process = subprocess.Popen(args, cwd=tool.parent, env=environment,
                                   preexec_fn=_terminal_child)
        try:
            os.tcsetpgrp(terminal, process.pid)
            # A child which read before the handoff may have stopped on SIGTTIN.
            os.killpg(process.pid, signal.SIGCONT)
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _terminate(process)
            raise CliError(f"{label} update or login timed out; the private cache is retained. "
                           "Retry auth login, or use a new --cache with --fresh for a clean attempt.",
                           "timeout", 124) from None
        except BaseException:
            _terminate(process)
            raise
        if code in (-signal.SIGINT, 128 + signal.SIGINT):
            raise KeyboardInterrupt
        if code:
            raise CliError(f"{label} update or interactive login did not complete. "
                           "Inspect its terminal error; check connectivity and the 32-bit runtime. "
                           "The private cache is retained.", "steam_auth_failed",
                           details={"phase": "bootstrap_or_interactive_login",
                                    "cached_login_verified": False})


def verify_cached_login(tool: Path, steam_user: str, environment: dict,
                        timeout: float, steam_tcp: bool) -> None:
    args = [str(tool), *(["-tcp"] if steam_tcp else []),
            "+@ShutdownOnFailedCommand", "1", "+@NoPromptForPassword", "1",
            "+login", steam_user, "+quit"]
    completed = run(args, cwd=tool.parent, env=environment, timeout=timeout, check=False)
    import re
    # SteamCMD 1791918946 emits CSI color codes even when stdout is a pipe,
    # including between "...", "OK" and within failure text. Parse status text
    # after removing presentation codes; keep the native transcript private.
    lowered = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", completed.stdout).lower()
    rejected = ("invalid password", "steam guard", "no cached credentials",
                "cached credentials not found", "cached credentials expired",
                "account logon denied", "failed to log on", "failed to log in",
                "login failure", "password required")
    # Native SteamCMD prints "Logging in user ... to Steam Public...OK" and
    # "Waiting for user info...OK". Exit zero alone does not prove authentication.
    login_ok = re.search(r"logging in user[^\r\n]*\.\.\.ok", lowered) is not None
    info_ok = re.search(r"waiting for user info[^\r\n]*\.\.\.ok", lowered) is not None
    if completed.returncode or any(term in lowered for term in rejected) or not (login_ok and info_ok):
        raise CliError("Reusable cached login could not be verified. Retry auth login in a terminal; "
                       "check Steam Guard and connectivity. Download permission has not been verified.",
                       "steam_auth_unverified", details={"cached_login_verified": False,
                                                       "download_authorization": "unknown"})


def login(source: Source, steam_user: str | None, cache: str | None, timeout: float,
          *, steam_tcp: bool = False, fresh: bool = False) -> dict:
    if os.name != "posix" or platform.system() != "Linux" or (
            platform.machine() not in {"x86_64", "amd64"} or platform.libc_ver()[0] != "glibc"):
        raise CliError("Steam login preparation requires Linux x86_64/glibc.", "unsupported_platform", 4)
    if os.geteuid() == 0:
        raise CliError("Run auth login as a normal user, without sudo.", "root_login_refused", 3)
    if not all(stream.isatty() for stream in (sys.stdin, sys.stdout, sys.stderr)):
        raise CliError("auth login needs an interactive terminal. Connect with ssh -t and run it there; "
                       "never send passwords or Steam tokens through command flags or JSON.",
                       "interactive_required", 2)
    if steam_user is None:
        steam_user = input("Steam account name: ").strip()
    steam.validate_login_name(steam_user)
    if steam_user == "anonymous":
        raise CliError("auth login requires a Steam account name.", "invalid_steam_user", 2)
    REDACTOR.register(steam_user)
    source.validate()
    selected = Path(cache).expanduser() if cache else Path.home() / ".local/share/l4d2-cli/steamcmd-auth"
    if not selected.is_absolute() or ".." in selected.parts or selected == Path.home():
        raise CliError("Choose an absolute dedicated cache directory, not your home directory.",
                       "unsafe_auth_cache", 4)
    storage.reject_links(selected)
    cache_created = not selected.exists()
    marker = selected / ".l4d2-auth.json"
    if selected.exists():
        if not selected.is_dir():
            raise CliError("Cache must be a directory.", "unsafe_auth_cache", 4)
        if fresh and any(selected.iterdir()):
            raise CliError("--fresh requires an empty or new --cache directory; existing logins are retained.",
                           "fresh_cache_required", 4)
        private_tree(selected, chmod=False)
        if any(selected.iterdir()) and storage.read_json(marker) != {"schema_version": 1, "purpose": "steamcmd_auth"}:
            raise CliError("Nonempty cache is not registered for CLI login; choose a new directory.",
                           "auth_cache_conflict", 4)
    deadline = time.monotonic() + timeout
    started_empty = cache_created or not any(selected.iterdir())
    previous_umask = os.umask(0o077)
    try:
        selected.mkdir(parents=True, exist_ok=True, mode=0o700)
        private_tree(selected)
        with cache_lock(selected):
            storage.save_json(marker, {"schema_version": 1, "purpose": "steamcmd_auth"}, private=True)
            tool_dir = selected / "steamcmd"
            if not (tool_dir / "steamcmd.sh").is_file():
                report = network.check(source, timeout=min(30, deadline_remaining(deadline)), archive_only=True)
                network.require_network(report)
            progress("SteamCMD will prompt directly for credentials and Steam Guard. "
                     "Its private cache contains login credentials; keep it out of Git and game bundles.")
            try:
                tool = steam.prepare_tool(source, tool_dir, deadline_remaining(deadline), include_login_cache=not fresh)
                environment = os.environ.copy()
                environment["HOME"] = str(tool_dir)
                environment["XDG_CONFIG_HOME"] = str(selected / "xdg-config")
                environment["XDG_DATA_HOME"] = str(selected / "xdg-data")
                environment["XDG_CACHE_HOME"] = str(selected / "xdg-cache")
                interactive([str(tool), *(["-tcp"] if steam_tcp else []), "+@NoPromptForPassword", "0", "+login", steam_user, "+quit"],
                            tool, environment, deadline_remaining(deadline))
                private_tree(selected)
                verify_cached_login(tool, steam_user, environment, deadline_remaining(deadline), steam_tcp)
            finally:
                private_tree(selected)
    finally:
        os.umask(previous_umask)
    return {"cache_created": cache_created, "started_with_empty_cache": started_empty,
            "login_cache_import_allowed": not fresh, "cached_login_verified": True, "download_authorization": "unknown",
            "steamcmd_path": str(tool), "private_cache": str(selected),
            "next_step": "Retry install or download server with --steam-user NAME and --steamcmd-path "
                         + str(tool),
            "cache_scope": "Native SteamCMD login cache; validity and cross-machine reuse are not guaranteed."}
